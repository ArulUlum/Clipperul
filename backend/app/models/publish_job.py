import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin, pg_enum
from app.models.enums import PublishJobStatus


class PublishJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "publish_jobs"

    scheduled_post_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scheduled_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[PublishJobStatus] = mapped_column(
        pg_enum(PublishJobStatus, "publish_job_status"), nullable=False, default=PublishJobStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scheduled_post: Mapped["ScheduledPost"] = relationship(back_populates="publish_jobs")
