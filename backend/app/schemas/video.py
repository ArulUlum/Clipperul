from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.models.enums import VideoCategory, VideoStatus


class VideoCreateResponse(BaseModel):
    video_id: UUID
    job_id: UUID
    status: str


class VideoCreateFromUrlRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    url: HttpUrl
    subtitles_enabled: bool = True


class VideoResponse(BaseModel):
    id: UUID
    title: str
    category: VideoCategory | None  # null until the DETECTING_CATEGORY stage completes
    status: VideoStatus
    original_filename: str
    source_url: str | None
    subtitles_enabled: bool
    duration_seconds: float | None
    file_size_bytes: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VideoDetailResponse(VideoResponse):
    current_stage: VideoStatus | None = None
    error_code: str | None = None
    error_message: str | None = None


class TranscriptSegmentResponse(BaseModel):
    start_time: float
    end_time: float
    text: str

    model_config = {"from_attributes": True}


class TranscriptResponse(BaseModel):
    language: str | None
    full_text: str
    segments: list[TranscriptSegmentResponse]

    model_config = {"from_attributes": True}
