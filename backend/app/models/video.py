import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin, pg_enum
from app.models.enums import VideoCategory, VideoStatus


class Video(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "videos"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable — category is AI-detected from the transcript during processing (DETECTING_
    # CATEGORY stage) rather than chosen by the user at upload time. Null until that stage runs.
    category: Mapped[VideoCategory | None] = mapped_column(pg_enum(VideoCategory, "video_category"), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Set when the video was submitted as a YouTube/generic video URL instead of a file
    # upload — the worker downloads it (via yt-dlp) into storage_key before processing.
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Whether the pipeline should burn a default subtitle preset into each generated clip
    # automatically. Chosen at upload time; users can still pick a different preset (or none)
    # afterward per-clip via POST /clips/{id}/render.
    subtitles_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[VideoStatus] = mapped_column(
        pg_enum(VideoStatus, "video_status"), nullable=False, default=VideoStatus.UPLOADED
    )

    user: Mapped["User"] = relationship(back_populates="videos")
    processing_jobs: Mapped[list["VideoProcessingJob"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )
    transcript: Mapped["Transcript"] = relationship(
        back_populates="video", cascade="all, delete-orphan", uselist=False
    )
    clips: Mapped[list["Clip"]] = relationship(back_populates="video", cascade="all, delete-orphan")
