"""The daily auto-clip: pick today's trending video, clip it, post the clips one at a time.

Everything advances through `tick()`, which a beat task calls every minute. Each tick looks at
every run's current state and moves it one step — so nothing depends on in-process callbacks
that a crash or restart could lose. The functions here only touch the database; they return
what should be dispatched to Celery (`TickResult`), and the task layer does the dispatching
after the transaction has committed.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.security import decrypt_token
from app.models.auto_clip import AutoClipRun, AutoClipSettings, AutoClipSource
from app.models.clip import Clip
from app.models.enums import (
    AspectRatio,
    ClipStatus,
    ScheduledPostStatus,
    SocialProvider,
    VideoCategory,
    VideoStatus,
)
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob
from app.repositories.clip_repository import ClipRepository
from app.repositories.job_repository import JobRepository
from app.repositories.video_repository import VideoRepository
from app.services.auto_clip.trending import (
    CATEGORIES,
    TrendingError,
    extract_youtube_id,
    fetch_candidates,
    filter_candidates,
)
from app.services.social.registry import get_social_provider
from app.services.social.youtube import YouTubeProvider
from app.services.video.exceptions import PipelineError
from app.services.video.video_service import VideoService

logger = logging.getLogger(__name__)

IN_PROGRESS = ("SELECTING", "STARTING", "PROCESSING", "PUBLISHING")
# Long enough to hold several clips, short enough to transcribe in reasonable time on CPU.
MIN_SOURCE_SECONDS = 8 * 60
MAX_SOURCE_SECONDS = 75 * 60
MAX_ATTEMPTS = 3  # candidates tried per run before giving up
KEEP_REMAINING = 5
STARTING_TIMEOUT = timedelta(minutes=10)
# A run still going after this long is stuck (lost dispatch, dead worker...) — stop it so it can't
# block every later day, since a new run never starts while one is in progress.
MAX_RUN_AGE = timedelta(hours=36)
DEFAULT_CATEGORY = "podcast"
DEFAULT_REGION = "ID"
DEFAULT_RUN_TIME = "08:00"
DEFAULT_MAX_CLIPS = 5

_VIDEO_CATEGORY = {"podcast": VideoCategory.PODCAST, "gaming": VideoCategory.GAMING}
_ACTIVE_POST_STATUSES = (
    ScheduledPostStatus.SCHEDULED,
    ScheduledPostStatus.PROCESSING,
    ScheduledPostStatus.UPLOADING,
)


class AutoClipError(Exception):
    """A problem worth showing the user verbatim."""


@dataclass
class TickResult:
    runs_to_start: list[str] = field(default_factory=list)
    jobs_to_process: list[str] = field(default_factory=list)
    posts_to_publish: list[tuple[str, str]] = field(default_factory=list)  # (post_id, run_token)


# --------------------------------------------------------------------------- time / schedule

def user_zone(tz_name: str | None) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def local_now(st: AutoClipSettings, now_utc: datetime) -> datetime:
    return now_utc.astimezone(user_zone(st.timezone))


def parse_run_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def is_due(st: AutoClipSettings, now_utc: datetime) -> bool:
    """Enabled, today's run time has passed in the user's timezone, and it hasn't run today."""
    if not st.enabled:
        return False
    local = local_now(st, now_utc)
    return local.time() >= parse_run_time(st.run_time) and st.last_run_date != local.date()


# --------------------------------------------------------------------------- lookups

def get_settings(db: Session, user_id: UUID) -> AutoClipSettings | None:
    return db.scalar(select(AutoClipSettings).where(AutoClipSettings.user_id == user_id))


def has_in_progress_run(db: Session, user_id: UUID) -> bool:
    return (
        db.scalar(
            select(func.count(AutoClipRun.id)).where(AutoClipRun.user_id == user_id, AutoClipRun.status.in_(IN_PROGRESS))
        )
        or 0
    ) > 0


def has_credential(db: Session, user_id: UUID) -> bool:
    """Whether trending can be read at all: an API key, or a connected YouTube account."""
    if app_settings.YOUTUBE_API_KEY:
        return True
    return db.scalar(
        select(func.count(SocialAccount.id)).where(
            SocialAccount.user_id == user_id, SocialAccount.provider == SocialProvider.YOUTUBE
        )
    ) > 0


def publishable_accounts(db: Session, user_id: UUID) -> list[SocialAccount]:
    """Connected accounts whose platform can actually publish today (YouTube) — others would
    only produce posts that fail."""
    accounts = db.scalars(
        select(SocialAccount).where(SocialAccount.user_id == user_id).order_by(SocialAccount.connected_at)
    ).all()
    return [a for a in accounts if get_social_provider(a.provider).can_publish]


def used_video_ids(db: Session, user_id: UUID) -> set[str]:
    """Every YouTube video this user has already used — by a previous auto-clip run or by
    uploading its link themselves. Never picked again."""
    ids = set(db.scalars(select(AutoClipSource.youtube_video_id).where(AutoClipSource.user_id == user_id)))
    for url in db.scalars(select(Video.source_url).where(Video.user_id == user_id, Video.source_url.is_not(None))):
        video_id = extract_youtube_id(url)
        if video_id:
            ids.add(video_id)
    return ids


def get_credentials(db: Session, user_id: UUID) -> tuple[str | None, str | None]:
    """(api_key, access_token) — the API key if configured, else a fresh token from the user's
    connected YouTube account (which carries read access)."""
    if app_settings.YOUTUBE_API_KEY:
        return app_settings.YOUTUBE_API_KEY, None
    account = db.scalar(
        select(SocialAccount)
        .where(SocialAccount.user_id == user_id, SocialAccount.provider == SocialProvider.YOUTUBE)
        .order_by(SocialAccount.connected_at)
    )
    if account is None:
        raise AutoClipError("Connect a YouTube account (or set YOUTUBE_API_KEY) so trending videos can be read.")
    YouTubeProvider().refresh_token_if_needed(account.id)
    db.refresh(account)
    return None, decrypt_token(account.encrypted_access_token)


# --------------------------------------------------------------------------- run lifecycle

def create_run(db: Session, user_id: UUID, trigger: str, category: str, run_date: date) -> AutoClipRun:
    run = AutoClipRun(
        user_id=user_id, trigger=trigger, status="SELECTING", run_date=run_date, category=category,
        remaining_candidates=[], publish_queue=[],
    )
    db.add(run)
    db.flush()
    return run


def request_manual_run(db: Session, user_id: UUID, now_utc: datetime | None = None) -> AutoClipRun:
    """The 'Run now' button. Counts as today's run, so the scheduled one doesn't fire again."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if has_in_progress_run(db, user_id):
        raise AutoClipError("A run is already in progress.")
    if not has_credential(db, user_id):
        raise AutoClipError("Connect a YouTube account (or set YOUTUBE_API_KEY) so trending videos can be read.")
    st = get_settings(db, user_id)
    category = st.category if st else DEFAULT_CATEGORY
    run_date = local_now(st, now_utc).date() if st else now_utc.date()
    if st is not None:
        st.last_run_date = run_date
    run = create_run(db, user_id, "manual", category, run_date)
    db.commit()
    return run


def _fail(db: Session, run: AutoClipRun, message: str) -> None:
    run.status = "FAILED"
    run.error = message[:2000]
    run.remaining_candidates = []
    db.commit()


def _launch_from(db: Session, run: AutoClipRun, queue: list[tuple[str, str]], last_error: str | None = None) -> str | None:
    """Starts processing the first candidate in `queue` that can be used. Returns the new
    processing job's id (for the caller to enqueue), or None after marking the run FAILED."""
    st = get_settings(db, run.user_id)
    category = _VIDEO_CATEGORY.get(run.category)
    while queue:
        if run.attempts >= MAX_ATTEMPTS:
            break
        (youtube_id, title), queue = queue[0], queue[1:]

        # Reserve the link FIRST: (user, video) is unique, so two runs can never take the same one
        # — the existence check handles the normal case, the constraint the race.
        if db.scalar(
            select(AutoClipSource.id).where(
                AutoClipSource.user_id == run.user_id, AutoClipSource.youtube_video_id == youtube_id
            )
        ):
            continue
        try:
            db.add(AutoClipSource(user_id=run.user_id, youtube_video_id=youtube_id))
            db.flush()
        except IntegrityError:
            db.rollback()
            continue

        try:
            video, job = VideoService(VideoRepository(db), JobRepository(db)).create_video_from_url(
                user_id=run.user_id,
                title=title[:255],
                url=f"https://www.youtube.com/watch?v={youtube_id}",
                subtitles_enabled=True,
                category=category,  # podcast/gaming skip AI detection; other categories get detected
                clip_aspect_ratio=AspectRatio.SQUARE_1_1,
                auto_track=False,
            )
        except PipelineError as exc:
            logger.warning("Auto-clip couldn't queue %s: %s", youtube_id, exc.message)
            last_error = exc.message
            continue

        run.attempts += 1
        run.status = "PROCESSING"
        run.source_video_id = youtube_id
        run.source_url = f"https://www.youtube.com/watch?v={youtube_id}"
        run.source_title = title[:500]
        run.video_id = video.id
        run.remaining_candidates = [{"id": i, "title": t} for i, t in queue[:KEEP_REMAINING]]
        run.error = None
        db.commit()
        return str(job.id)

    reason = "No other usable trending video was left to try."
    _fail(db, run, f"{last_error} {reason}" if last_error else reason)
    return None


def start_run(db: Session, run_id: UUID) -> str | None:
    """The network step: read today's trending list and start processing the best unused video.
    Returns the processing job id to enqueue, or None (run marked FAILED)."""
    run = db.get(AutoClipRun, run_id)
    if run is None or run.status != "STARTING":
        return None
    st = get_settings(db, run.user_id)
    region = st.region if st else DEFAULT_REGION
    try:
        api_key, access_token = get_credentials(db, run.user_id)
        candidates = fetch_candidates(run.category, region, api_key=api_key, access_token=access_token)
    except (AutoClipError, TrendingError) as exc:
        _fail(db, run, str(exc))
        return None

    usable = filter_candidates(
        candidates, used_video_ids(db, run.user_id), min_seconds=MIN_SOURCE_SECONDS, max_seconds=MAX_SOURCE_SECONDS
    )
    if not usable:
        label = CATEGORIES[run.category].label if run.category in CATEGORIES else run.category
        _fail(db, run, f"No new trending {label} video ({MIN_SOURCE_SECONDS // 60}-{MAX_SOURCE_SECONDS // 60} min) found for today.")
        return None
    return _launch_from(db, run, [(c.video_id, c.title) for c in usable])


# --------------------------------------------------------------------------- tick

def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _create_due_runs(db: Session, now_utc: datetime) -> None:
    for st in db.scalars(select(AutoClipSettings).where(AutoClipSettings.enabled.is_(True))).all():
        if not is_due(st, now_utc) or has_in_progress_run(db, st.user_id):
            continue
        run_date = local_now(st, now_utc).date()
        st.last_run_date = run_date
        create_run(db, st.user_id, "scheduled", st.category, run_date)
    db.commit()


def _cancel_runs_of_disabled_users(db: Session) -> None:
    """Turning the feature off stops scheduled runs mid-flight (an upload already running
    finishes; nothing new is queued). Manual runs are the user's explicit request — left alone."""
    for run in db.scalars(select(AutoClipRun).where(AutoClipRun.status.in_(IN_PROGRESS), AutoClipRun.trigger == "scheduled")).all():
        st = get_settings(db, run.user_id)
        if st is None or not st.enabled:
            run.status = "CANCELLED"
            run.publish_queue = []
            run.remaining_candidates = []
            run.note = "Stopped because the daily auto-clip was turned off."
    db.commit()


def _claim_selecting(db: Session, result: TickResult) -> None:
    for run in db.scalars(
        select(AutoClipRun).where(AutoClipRun.status == "SELECTING").with_for_update(skip_locked=True)
    ).all():
        run.status = "STARTING"
        result.runs_to_start.append(str(run.id))
    db.commit()


def _expire_stuck_starting(db: Session, now_utc: datetime) -> None:
    for run in db.scalars(select(AutoClipRun).where(AutoClipRun.status == "STARTING")).all():
        if now_utc - _aware(run.updated_at) > STARTING_TIMEOUT:
            _fail(db, run, "Timed out while choosing a video.")


def _expire_overdue_runs(db: Session, now_utc: datetime) -> None:
    for run in db.scalars(select(AutoClipRun).where(AutoClipRun.status.in_(IN_PROGRESS))).all():
        if now_utc - _aware(run.created_at) > MAX_RUN_AGE:
            run.publish_queue = []
            _fail(db, run, "This run took too long and was stopped.")


def _latest_job_error(db: Session, video_id: UUID) -> str | None:
    job = db.scalar(
        select(VideoProcessingJob).where(VideoProcessingJob.video_id == video_id).order_by(VideoProcessingJob.created_at.desc())
    )
    return job.error_message if job else None


def _build_publish_queue(db: Session, run: AutoClipRun, video: Video) -> list[dict]:
    st = get_settings(db, run.user_id)
    max_clips = st.max_clips if st else DEFAULT_MAX_CLIPS
    clips = [c for c in ClipRepository(db).list_for_video(video.id) if c.status == ClipStatus.READY][:max_clips]
    accounts = publishable_accounts(db, run.user_id)
    # Clip-major: a clip goes to every account before the next clip starts.
    return [{"clip_id": str(c.id), "account_id": str(a.id)} for c in clips for a in accounts]


def _advance_processing(db: Session, run: AutoClipRun, result: TickResult) -> None:
    video = db.get(Video, run.video_id) if run.video_id else None
    if video is None:
        _fail(db, run, "The video for this run was deleted.")
        return
    if video.status == VideoStatus.COMPLETED:
        queue = _build_publish_queue(db, run, video)
        run.publish_queue = queue
        run.clips_total = len(queue)
        if not queue:
            run.status = "COMPLETED"
            run.note = "Clips were created, but no connected account can publish — nothing was posted."
        else:
            run.status = "PUBLISHING"
        db.commit()
    elif video.status == VideoStatus.FAILED:
        error = _latest_job_error(db, video.id) or "Processing failed."
        job_id = _launch_from(
            db, run, [(c["id"], c["title"]) for c in run.remaining_candidates], last_error=f"Video failed: {error}"
        )
        if job_id:
            result.jobs_to_process.append(job_id)


def _advance_publishing(db: Session, run: AutoClipRun, result: TickResult, now_utc: datetime) -> None:
    """One upload at a time: nothing new starts until the previous post is PUBLISHED, FAILED or
    CANCELLED."""
    in_flight = db.scalar(
        select(func.count(ScheduledPost.id)).where(
            ScheduledPost.auto_run_id == run.id, ScheduledPost.status.in_(_ACTIVE_POST_STATUSES)
        )
    )
    if in_flight:
        return

    queue = list(run.publish_queue)
    while queue:
        item = queue.pop(0)
        clip = db.get(Clip, UUID(item["clip_id"]))
        account = db.get(SocialAccount, UUID(item["account_id"]))
        if clip is None or account is None or clip.status != ClipStatus.READY or not clip.storage_key:
            continue  # deleted/disconnected since the queue was built
        post = ScheduledPost(
            clip_id=clip.id,
            social_account_id=account.id,
            platform=account.provider,
            scheduled_at=now_utc,
            title=clip.title[:255],
            caption=clip.caption,
            status=ScheduledPostStatus.PROCESSING,
            run_token=uuid.uuid4().hex,
            auto_run_id=run.id,
        )
        db.add(post)
        run.publish_queue = queue
        db.commit()
        result.posts_to_publish.append((str(post.id), post.run_token))
        return

    run.publish_queue = []
    run.status = "COMPLETED"
    db.commit()


def tick(db: Session, now_utc: datetime | None = None) -> TickResult:
    now_utc = now_utc or datetime.now(timezone.utc)
    result = TickResult()
    _expire_overdue_runs(db, now_utc)
    _create_due_runs(db, now_utc)
    _cancel_runs_of_disabled_users(db)
    _claim_selecting(db, result)
    _expire_stuck_starting(db, now_utc)
    for run in db.scalars(select(AutoClipRun).where(AutoClipRun.status == "PROCESSING").with_for_update(skip_locked=True)).all():
        _advance_processing(db, run, result)
    for run in db.scalars(select(AutoClipRun).where(AutoClipRun.status == "PUBLISHING").with_for_update(skip_locked=True)).all():
        _advance_publishing(db, run, result, now_utc)
    return result
