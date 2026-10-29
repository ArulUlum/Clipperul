import logging
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models.clip import Clip
from app.models.enums import PublishJobStatus, ScheduledPostStatus
from app.models.publish_job import PublishJob
from app.models.scheduled_post import ScheduledPost
from app.services.social.registry import get_social_provider
from app.services.storage.storage_service import storage_service
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Exponential-ish backoff: 1m, 5m, 15m, 1h, 6h. After this many failed attempts, the post is
# marked FAILED for good instead of retried again — a bad connect/expired token isn't going to
# fix itself by waiting longer.
_RETRY_BACKOFF_SECONDS = [60, 300, 900, 3600, 21600]
_MAX_RETRIES = len(_RETRY_BACKOFF_SECONDS)


@celery_app.task(name="dispatch_due_scheduled_posts")
def dispatch_due_scheduled_posts() -> None:
    """Celery-beat periodic task (see beat_schedule in celery_app.py) — finds every SCHEDULED
    post whose time has come and hands each to its own publish task. Claims them (flips to
    PROCESSING) in a single bulk UPDATE before dispatching anything, so a slow tick can't pick
    up the same row twice."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due_ids = list(
            db.execute(
                select(ScheduledPost.id).where(
                    ScheduledPost.status == ScheduledPostStatus.SCHEDULED,
                    ScheduledPost.scheduled_at <= now,
                )
            ).scalars()
        )
        if not due_ids:
            return
        tokens = {post_id: uuid.uuid4().hex for post_id in due_ids}
        for post_id, token in tokens.items():
            db.execute(
                update(ScheduledPost)
                .where(ScheduledPost.id == post_id, ScheduledPost.status == ScheduledPostStatus.SCHEDULED)
                .values(status=ScheduledPostStatus.PROCESSING, run_token=token)
            )
        db.commit()
    finally:
        db.close()

    for post_id, token in tokens.items():
        publish_scheduled_post_task.delay(str(post_id), token)


class PublishAborted(Exception):
    """This attempt was cancelled (or superseded by a manual retry) while it was running."""


_LIVE_STATUSES = (ScheduledPostStatus.PROCESSING, ScheduledPostStatus.UPLOADING)


def _is_current_attempt(db, scheduled_post_id: str, run_token: str | None) -> bool:
    """True while this attempt is still the one the post wants: not cancelled, not already
    finished, and not replaced by a newer manual retry. Reads the columns straight from the DB
    (not the session's cached ORM object), since the whole point is to see a cancel that another
    process just committed. `run_token=None` is a task queued before tokens existed — only the
    status is checked for those."""
    row = db.execute(
        select(ScheduledPost.status, ScheduledPost.run_token).where(ScheduledPost.id == UUID(scheduled_post_id))
    ).one_or_none()
    if row is None or row.status not in _LIVE_STATUSES:
        return False
    return run_token is None or row.run_token == run_token


def _mark_failed(db, scheduled_post_id: str, job: PublishJob | None, message: str) -> None:
    if job is not None:
        job.status = PublishJobStatus.FAILED
        job.error_message = message[:2000]
        db.commit()
    # Conditional: a post the user cancelled while this attempt was failing must stay CANCELLED.
    db.execute(
        update(ScheduledPost)
        .where(ScheduledPost.id == UUID(scheduled_post_id), ScheduledPost.status.in_(_LIVE_STATUSES))
        .values(status=ScheduledPostStatus.FAILED)
    )
    db.commit()


@celery_app.task(bind=True, name="publish_scheduled_post", max_retries=_MAX_RETRIES)
def publish_scheduled_post_task(self, scheduled_post_id: str, run_token: str | None = None) -> None:
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="autoclipper-publish-")
    job: PublishJob | None = None
    try:
        post = db.get(ScheduledPost, UUID(scheduled_post_id))
        if post is None:
            return
        if not _is_current_attempt(db, scheduled_post_id, run_token):
            # Cancelled, already finished, or replaced by a newer manual retry — including a
            # retry that sat in the queue for hours before firing. Nothing to do.
            logger.info("Skipping stale publish attempt for scheduled_post=%s", scheduled_post_id)
            return

        job = PublishJob(
            scheduled_post_id=post.id,
            status=PublishJobStatus.RUNNING,
            retry_count=self.request.retries,
            last_attempt_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        clip = db.get(Clip, post.clip_id)
        if clip is None or not clip.storage_key:
            raise ValueError("Clip has no finished render to publish yet")

        local_path = str(Path(tmpdir) / "clip.mp4")
        storage_service.download_to_file(clip.storage_key, local_path)

        # Atomic PROCESSING -> UPLOADING: if the user cancelled while the file was downloading,
        # this matches no row and we stop here instead of overwriting CANCELLED with UPLOADING.
        claim = update(ScheduledPost).where(
            ScheduledPost.id == post.id, ScheduledPost.status == ScheduledPostStatus.PROCESSING
        )
        if run_token is not None:
            claim = claim.where(ScheduledPost.run_token == run_token)
        claimed = db.execute(claim.values(status=ScheduledPostStatus.UPLOADING, progress=0))
        db.commit()
        if claimed.rowcount == 0:
            raise PublishAborted()
        db.refresh(post)

        def report_progress(percent: float) -> None:
            # This is also the cancel checkpoint: the platform adapter calls it after every chunk,
            # so raising here stops an in-flight upload within one chunk of the user cancelling.
            # (A failed check is treated as "still running" — a DB hiccup mustn't kill an upload.)
            try:
                still_current = _is_current_attempt(db, scheduled_post_id, run_token)
            except Exception:
                db.rollback()
                still_current = True
            if not still_current:
                raise PublishAborted()
            # Capped at 99 — 100 is reserved for "actually published", which is only known once
            # the platform has accepted the whole upload and returned a post id. A failed progress
            # write must never abort the upload it's merely reporting on.
            try:
                post.progress = max(0, min(99, int(percent)))
                db.commit()
            except Exception:
                logger.warning("Could not record upload progress for scheduled_post=%s", scheduled_post_id)
                db.rollback()

        provider = get_social_provider(post.platform)
        provider.refresh_token_if_needed(post.social_account_id)
        result = provider.publish_video(
            post.social_account_id, local_path, post.title, post.caption or "", on_progress=report_progress
        )

        # Unconditional on purpose: if the video made it onto the platform, "published" is the
        # truth even if a cancel landed in the last instant — hiding it would be worse.
        job.status = PublishJobStatus.SUCCEEDED
        post.status = ScheduledPostStatus.PUBLISHED
        post.progress = 100
        post.published_at = datetime.now(timezone.utc)
        post.external_post_id = result.external_post_id
        post.external_url = result.url
        db.commit()

    except PublishAborted:
        logger.info("Publish attempt stopped for scheduled_post=%s (cancelled or superseded)", scheduled_post_id)
        if job is not None:
            job.status = PublishJobStatus.FAILED
            job.error_message = "Cancelled — upload stopped before it finished."
            db.commit()
        # The post's own status is deliberately left alone: cancel already set it (CANCELLED),
        # or a newer retry owns it now.
    except NotImplementedError as exc:
        # A platform whose publish_video isn't wired up yet (still pending app review, etc.) —
        # this isn't transient, so don't burn retries on it.
        logger.warning("Publishing not available for scheduled_post=%s: %s", scheduled_post_id, exc)
        _mark_failed(db, scheduled_post_id, job, str(exc))
    except Exception as exc:
        logger.exception("Publish attempt failed for scheduled_post=%s", scheduled_post_id)
        attempt = self.request.retries
        if job is not None:
            job.status = PublishJobStatus.FAILED
            job.error_message = str(exc)[:2000]
            if attempt < _MAX_RETRIES:
                job.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=_RETRY_BACKOFF_SECONDS[attempt])
            db.commit()
        if attempt >= _MAX_RETRIES:
            _mark_failed(db, scheduled_post_id, job, str(exc))
        elif _is_current_attempt(db, scheduled_post_id, run_token):
            # Stays PROCESSING (not back to SCHEDULED) so the beat dispatcher's "due now" query
            # never re-claims it — the countdown below is what re-fires this task. Only while the
            # post is still ours: if the user cancelled mid-failure, no retry is queued at all.
            db.execute(
                update(ScheduledPost)
                .where(ScheduledPost.id == UUID(scheduled_post_id), ScheduledPost.status.in_(_LIVE_STATUSES))
                .values(status=ScheduledPostStatus.PROCESSING, progress=0)  # next attempt restarts the upload
            )
            db.commit()
            raise self.retry(exc=exc, countdown=_RETRY_BACKOFF_SECONDS[attempt])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        db.close()
