import redis
import redis.asyncio as aioredis

from app.core.config import settings

# Synchronous client: used by Celery workers (separate process, blocking is fine/expected there).
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Async client: used by the FastAPI SSE endpoint — a blocking call there would stall the
# whole event loop for every other request while any client has the stream open.
async_redis_client = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def video_status_channel(video_id: str) -> str:
    return f"video-status:{video_id}"


def publish_video_status(video_id: str, payload: str) -> None:
    redis_client.publish(video_status_channel(video_id), payload)
