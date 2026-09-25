import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.redis import async_redis_client, video_status_channel
from app.models.user import User
from app.repositories.clip_repository import ClipRepository
from app.repositories.job_repository import JobRepository
from app.repositories.video_repository import VideoRepository
from app.schemas.clip import ClipResponse, build_clip_response
from app.schemas.video import (
    TranscriptResponse,
    VideoCreateFromUrlRequest,
    VideoCreateResponse,
    VideoDetailResponse,
    VideoResponse,
)
from app.services.storage.storage_service import storage_service
from app.services.video.exceptions import PipelineError
from app.services.video.video_service import VideoService
from app.workers.tasks.video_tasks import process_video

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("", response_model=VideoCreateResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def create_video(
    request: Request,
    title: str = Form(...),
    file: UploadFile = File(...),
    subtitles_enabled: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw_bytes = await file.read()

    service = VideoService(VideoRepository(db), JobRepository(db))
    try:
        video, job = service.create_video_and_job(
            user_id=current_user.id,
            title=title,
            file=file,
            raw_bytes=raw_bytes,
            subtitles_enabled=subtitles_enabled,
        )
    except PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    process_video.delay(str(job.id))

    return VideoCreateResponse(video_id=video.id, job_id=job.id, status="queued")


@router.post("/from-url", response_model=VideoCreateResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def create_video_from_url(
    request: Request,
    payload: VideoCreateFromUrlRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accepts a YouTube link or any other yt-dlp-supported / direct video URL. The actual
    download happens in the worker (never in the request) — see PipelineError INVALID_URL
    for the synchronous SSRF/format checks that do run here, which are cheap validation,
    not video processing."""
    service = VideoService(VideoRepository(db), JobRepository(db))
    try:
        video, job = service.create_video_from_url(
            user_id=current_user.id,
            title=payload.title,
            url=str(payload.url),
            subtitles_enabled=payload.subtitles_enabled,
        )
    except PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    process_video.delay(str(job.id))

    return VideoCreateResponse(video_id=video.id, job_id=job.id, status="queued")


@router.get("", response_model=list[VideoResponse])
def list_videos(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VideoRepository(db).list_for_user(current_user.id)


@router.get("/{video_id}", response_model=VideoDetailResponse)
def get_video(video_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    video_repo = VideoRepository(db)
    video = video_repo.get_for_user(video_id, current_user.id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    job = video_repo.latest_job_for_video(video.id)
    return VideoDetailResponse(
        **VideoResponse.model_validate(video).model_dump(),
        current_stage=job.current_stage if job else None,
        error_code=job.error_code if job else None,
        error_message=job.error_message if job else None,
    )


@router.get("/{video_id}/stream")
async def stream_video_status(
    video_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    video = VideoRepository(db).get_for_user(video_id, current_user.id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    async def event_generator():
        pubsub = async_redis_client.pubsub()
        await pubsub.subscribe(video_status_channel(str(video_id)))
        try:
            yield {"event": "status", "data": json.dumps({"stage": video.status.value, "job_status": "SNAPSHOT"})}
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None:
                    payload = message["data"]
                    yield {"event": "status", "data": payload}
                    data = json.loads(payload)
                    if data.get("stage") in ("COMPLETED", "FAILED"):
                        break
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    return EventSourceResponse(event_generator())


@router.get("/{video_id}/transcript", response_model=TranscriptResponse)
def get_transcript(video_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    video = VideoRepository(db).get_for_user(video_id, current_user.id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    if video.transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript not available yet")
    return video.transcript


@router.get("/{video_id}/clips", response_model=list[ClipResponse])
def get_video_clips(video_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    video = VideoRepository(db).get_for_user(video_id, current_user.id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    return [build_clip_response(clip) for clip in ClipRepository(db).list_for_video(video.id)]


@router.get("/{video_id}/download")
def download_video(video_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Presigned URL for the ORIGINAL source video — used by the clip editor's live preview so
    trim/crop can be scrubbed against the full source instead of an already-cut clip."""
    video = VideoRepository(db).get_for_user(video_id, current_user.id)
    if video is None or video.storage_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")
    return {"url": storage_service.generate_presigned_get_url(video.storage_key)}


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(video_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    video_repo = VideoRepository(db)
    video = video_repo.get_for_user(video_id, current_user.id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    storage_service.delete(video.storage_key)
    for clip in video.clips:
        if clip.storage_key:
            storage_service.delete(clip.storage_key)
        if clip.thumbnail_key:
            storage_service.delete(clip.thumbnail_key)

    video_repo.delete(video)
