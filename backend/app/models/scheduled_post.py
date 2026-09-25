import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin, pg_enum
from app.models.enums import ScheduledPostStatus, SocialProvider


class ScheduledPost(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "scheduled_posts"

    clip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[SocialProvider] = mapped_column(pg_enum(SocialProvider, "social_provider"), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[ScheduledPostStatus] = mapped_column(
        pg_enum(ScheduledPostStatus, "scheduled_post_status"),
        nullable=False,
        default=ScheduledPostStatus.SCHEDULED,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once the publish task succeeds — the platform's own post id / permalink for the
    # "Published" page to link out to.
    external_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    clip: Mapped["Clip"] = relationship(back_populates="scheduled_posts")
    social_account: Mapped["SocialAccount"] = relationship(back_populates="scheduled_posts")
    publish_jobs: Mapped[list["PublishJob"]] = relationship(
        back_populates="scheduled_post", cascade="all, delete-orphan"
    )
