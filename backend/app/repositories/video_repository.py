from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob


class VideoRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, video: Video) -> Video:
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video

    def get_for_user(self, video_id: UUID, user_id: UUID) -> Video | None:
        return self.db.execute(
            select(Video).where(Video.id == video_id, Video.user_id == user_id)
        ).scalar_one_or_none()

    def list_for_user(self, user_id: UUID) -> list[Video]:
        return list(
            self.db.execute(
                select(Video).where(Video.user_id == user_id).order_by(Video.created_at.desc())
            ).scalars()
        )

    def count_for_user(self, user_id: UUID) -> int:
        return self.db.execute(select(func.count(Video.id)).where(Video.user_id == user_id)).scalar_one()

    def delete(self, video: Video) -> None:
        self.db.delete(video)
        self.db.commit()

    def latest_job_for_video(self, video_id: UUID) -> VideoProcessingJob | None:
        return self.db.execute(
            select(VideoProcessingJob)
            .where(VideoProcessingJob.video_id == video_id)
            .order_by(VideoProcessingJob.created_at.desc())
        ).scalars().first()

    def count_active_jobs_for_user(self, user_id: UUID) -> int:
        from app.models.enums import JobStatus

        return self.db.execute(
            select(func.count(VideoProcessingJob.id))
            .join(Video, Video.id == VideoProcessingJob.video_id)
            .where(Video.user_id == user_id, VideoProcessingJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
        ).scalar_one()
