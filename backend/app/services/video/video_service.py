import io
import mimetypes
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

from app.core.config import settings
from app.models.enums import AspectRatio, JobStatus, VideoCategory, VideoStatus
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob
from app.repositories.job_repository import JobRepository
from app.repositories.video_repository import VideoRepository
from app.services.storage.storage_service import storage_service
from app.services.video.exceptions import PipelineError
from app.services.video.url_validation import validate_video_url


# Browsers (Windows especially) often send no type at all for .mkv, and alias others
# (.avi as video/avi vs video/x-msvideo) — so an exact-match MIME allowlist rejects perfectly
# good files. The extension check above is the real gate, and the worker's FFmpeg step rejects
# anything that isn't actually a video; the client-supplied MIME is only a sanity check.
_GENERIC_MIME_TYPES = {"", "application/octet-stream"}


def _is_acceptable_content_type(content_type: str | None) -> bool:
    normalized = (content_type or "").split(";")[0].strip().lower()
    return normalized in _GENERIC_MIME_TYPES or normalized.startswith("video/")


def validate_upload(file: UploadFile, size_bytes: int) -> None:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in settings.ALLOWED_VIDEO_EXTENSIONS:
        raise PipelineError("INVALID_FILE_TYPE", f"Unsupported file extension: {ext}")
    if not _is_acceptable_content_type(file.content_type):
        raise PipelineError("INVALID_FILE_TYPE", f"Unsupported content type: {file.content_type}")
    if size_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
        raise PipelineError("FILE_TOO_LARGE", "Video exceeds the maximum allowed upload size")
    if size_bytes <= 0:
        raise PipelineError("EMPTY_FILE", "Uploaded file is empty")


class VideoService:
    def __init__(self, video_repo: VideoRepository, job_repo: JobRepository):
        self.video_repo = video_repo
        self.job_repo = job_repo

    def create_video_and_job(
        self,
        *,
        user_id: UUID,
        title: str,
        file: UploadFile,
        subtitles_enabled: bool = True,
        clip_layout: str | None = None,
    ) -> tuple[Video, VideoProcessingJob]:
        # The upload is already spooled to disk by the multipart parser: measure it there instead of
        # reading it into memory, and check the size before anything is stored.
        file.file.seek(0, io.SEEK_END)
        size = file.file.tell()
        file.file.seek(0)
        validate_upload(file, size)

        video_id = uuid.uuid4()
        ext = Path(file.filename or "video.mp4").suffix.lower()
        storage_key = f"videos/{user_id}/{video_id}/original{ext}"

        declared_type = (file.content_type or "").split(";")[0].strip().lower()
        stored_content_type = declared_type if declared_type.startswith("video/") else (
            mimetypes.guess_type(f"x{ext}")[0] or "video/mp4"
        )
        storage_service.upload_fileobj(file.file, storage_key, content_type=stored_content_type)

        video = Video(
            id=video_id,
            user_id=user_id,
            title=title,
            category=None,  # AI-detected during processing — see DETECTING_CATEGORY stage
            original_filename=file.filename or "video.mp4",
            storage_key=storage_key,
            file_size_bytes=size,
            subtitles_enabled=subtitles_enabled,
            clip_layout=clip_layout,
            status=VideoStatus.UPLOADED,
        )
        video = self.video_repo.create(video)

        job = VideoProcessingJob(
            video_id=video.id,
            status=JobStatus.PENDING,
            current_stage=VideoStatus.QUEUED,
        )
        job = self.job_repo.create(job)

        return video, job

    def create_video_from_url(
        self,
        *,
        user_id: UUID,
        title: str,
        url: str,
        subtitles_enabled: bool = True,
        category: VideoCategory | None = None,
        clip_aspect_ratio: AspectRatio | None = None,
        auto_track: bool = True,
        clip_layout: str | None = None,
    ) -> tuple[Video, VideoProcessingJob]:
        validate_video_url(url)

        video_id = uuid.uuid4()
        # Storage key is decided up front — the worker downloads straight into this key once
        # it runs; no file exists here yet (file_size_bytes is filled in after download).
        storage_key = f"videos/{user_id}/{video_id}/original.mp4"

        video = Video(
            id=video_id,
            user_id=user_id,
            title=title,
            # None = AI-detected during processing (DETECTING_CATEGORY stage); a caller that
            # already knows the category (e.g. the daily auto-clip) can skip that step.
            category=category,
            original_filename=url,
            storage_key=storage_key,
            source_url=url,
            file_size_bytes=0,
            subtitles_enabled=subtitles_enabled,
            clip_aspect_ratio=clip_aspect_ratio,
            auto_track=auto_track,
            clip_layout=clip_layout,
            status=VideoStatus.UPLOADED,
        )
        video = self.video_repo.create(video)

        job = VideoProcessingJob(
            video_id=video.id,
            status=JobStatus.PENDING,
            current_stage=VideoStatus.QUEUED,
        )
        job = self.job_repo.create(job)

        return video, job

    def presigned_download_url(self, video: Video) -> str:
        return storage_service.generate_presigned_get_url(video.storage_key)
