from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob


class JobRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, job: VideoProcessingJob) -> VideoProcessingJob:
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_for_user(self, job_id: UUID, user_id: UUID) -> VideoProcessingJob | None:
        return self.db.execute(
            select(VideoProcessingJob)
            .join(Video, Video.id == VideoProcessingJob.video_id)
            .where(VideoProcessingJob.id == job_id, Video.user_id == user_id)
        ).scalar_one_or_none()
