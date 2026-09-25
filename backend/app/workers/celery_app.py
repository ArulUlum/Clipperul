from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "autoclipper",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks.video_tasks",
        "app.workers.tasks.clip_render_tasks",
        "app.workers.tasks.publish_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # Runs via the `beat` service (see docker-compose.yml) — a single scheduler process that
    # enqueues dispatch_due_scheduled_posts onto the same queue the regular `worker` consumes.
    beat_schedule={
        "dispatch-due-scheduled-posts": {
            "task": "dispatch_due_scheduled_posts",
            "schedule": 60.0,
        },
    },
)
