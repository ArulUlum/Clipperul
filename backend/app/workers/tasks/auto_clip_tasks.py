import logging
from datetime import datetime, timezone
from uuid import UUID

from app.core.db import SessionLocal
from app.models.auto_clip import AutoClipRun
from app.services.auto_clip import service
from app.workers.celery_app import celery_app
from app.workers.tasks.publish_tasks import enqueue_publish
from app.workers.tasks.video_tasks import process_video

logger = logging.getLogger(__name__)


@celery_app.task(name="auto_clip_tick")
def auto_clip_tick() -> None:
    """Beat task (every minute): advances every daily auto-clip run one step — starts due runs,
    watches video processing, and releases the next upload when the previous one has finished.
    All state lives in the database, so a missed or doubled tick is harmless."""
    db = SessionLocal()
    try:
        result = service.tick(db, datetime.now(timezone.utc))
    finally:
        db.close()

    # Dispatched only after the transaction above has committed, so workers never see a
    # run/post/job id that isn't visible yet.
    for run_id in result.runs_to_start:
        start_auto_clip_run.delay(run_id)
    for job_id in result.jobs_to_process:
        process_video.delay(job_id)
    for post_id, token in result.posts_to_publish:
        enqueue_publish(post_id, token)


@celery_app.task(name="start_auto_clip_run")
def start_auto_clip_run(run_id: str) -> None:
    """Reads today's trending list (network calls — kept out of the tick) and starts processing
    the best video that hasn't been used before."""
    db = SessionLocal()
    try:
        job_id = service.start_run(db, UUID(run_id))
    except Exception:
        logger.exception("Auto-clip run %s failed to start", run_id)
        db.rollback()
        run = db.get(AutoClipRun, UUID(run_id))
        if run is not None:
            service._fail(db, run, "Unexpected error while choosing a video.")
        job_id = None
    finally:
        db.close()
    if job_id:
        process_video.delay(job_id)
