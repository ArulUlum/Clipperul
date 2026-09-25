import logging
import shutil
import tempfile
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
        db.execute(
            update(ScheduledPost)
            .where(ScheduledPost.id.in_(due_ids))
            .values(status=ScheduledPostStatus.PROCESSING)
        )
        db.commit()
    finally:
        db.close()

    for post_id in due_ids:
        publish_scheduled_post_task.delay(str(post_id))


def _mark_failed(db, scheduled_post_id: str, job: PublishJob | None, message: str) -> None:
    if job is not None:
        job.status = PublishJobStatus.FAILED
        job.error_message = message[:2000]
        db.commit()
    post = db.get(ScheduledPost, UUID(scheduled_post_id))
    if post is not None:
        post.status = ScheduledPostStatus.FAILED
        db.commit()


@celery_app.task(bind=True, name="publish_scheduled_post", max_retries=_MAX_RETRIES)
def publish_scheduled_post_task(self, scheduled_post_id: str) -> None:
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="autoclipper-publish-")
    job: PublishJob | None = None
    try:
        post = db.get(ScheduledPost, UUID(scheduled_post_id))
        if post is None:
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

        post.status = ScheduledPostStatus.UPLOADING
        db.commit()

        provider = get_social_provider(post.platform)
        provider.refresh_token_if_needed(post.social_account_id)
        result = provider.publish_video(post.social_account_id, local_path, post.title, post.caption or "")

        job.status = PublishJobStatus.SUCCEEDED
        post.status = ScheduledPostStatus.PUBLISHED
        post.published_at = datetime.now(timezone.utc)
        post.external_post_id = result.external_post_id
        post.external_url = result.url
        db.commit()

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
        if attempt < _MAX_RETRIES:
            post = db.get(ScheduledPost, UUID(scheduled_post_id))
            if post is not None:
                # Stays PROCESSING (not back to SCHEDULED) so the beat dispatcher's "due now"
                # query never re-claims it — the countdown below is what re-fires this task.
                post.status = ScheduledPostStatus.PROCESSING
                db.commit()
            raise self.retry(exc=exc, countdown=_RETRY_BACKOFF_SECONDS[attempt])
        _mark_failed(db, scheduled_post_id, job, str(exc))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        db.close()
