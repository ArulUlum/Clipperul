from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import JobStatus, VideoStatus


class JobResponse(BaseModel):
    id: UUID
    video_id: UUID
    status: JobStatus
    current_stage: VideoStatus
    error_code: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
