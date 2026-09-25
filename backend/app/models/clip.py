import uuid

from sqlalchemy import Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin, pg_enum
from app.models.enums import AspectRatio, ClipStatus, VideoCategory


class Clip(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clips"

    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Per-dimension breakdown behind the overall viral 'score' (0-100 each) — null when the
    # clip came from the heuristic-only fallback path, which has no basis for these.
    hook_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    flow_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[VideoCategory] = mapped_column(pg_enum(VideoCategory, "video_category"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[ClipStatus] = mapped_column(
        pg_enum(ClipStatus, "clip_status"), nullable=False, default=ClipStatus.PENDING
    )
    storage_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    aspect_ratio: Mapped[AspectRatio] = mapped_column(
        pg_enum(AspectRatio, "aspect_ratio"), nullable=False, default=AspectRatio.VERTICAL_9_16
    )
    # Manual crop-position override (0-100, percentage of source width/height, placing the crop
    # window's CENTER at that point) — null means "keep tracking the detected face
    # automatically". Set together or not at all; used when smart-crop picks the wrong subject.
    crop_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    crop_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The clip's CURRENT subtitle configuration — null preset_id means "no subtitles". Distinct
    # from ClipRender's own font_size/position_x/position_y (which record what a past render
    # used); these are what the editor currently has selected and what the next full re-render
    # (start/end + crop + subtitles together) will apply.
    subtitle_preset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("subtitle_presets.id", ondelete="SET NULL"), nullable=True
    )
    subtitle_font_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subtitle_position_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    subtitle_position_y: Mapped[float | None] = mapped_column(Float, nullable=True)

    video: Mapped["Video"] = relationship(back_populates="clips")
    renders: Mapped[list["ClipRender"]] = relationship(back_populates="clip", cascade="all, delete-orphan")
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )


class ClipRender(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clip_renders"

    clip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aspect_ratio: Mapped[AspectRatio] = mapped_column(
        pg_enum(AspectRatio, "aspect_ratio"), nullable=False, default=AspectRatio.VERTICAL_9_16
    )
    subtitle_preset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("subtitle_presets.id", ondelete="SET NULL"), nullable=True
    )
    # Per-render overrides of the preset's own font size / position — null means "use whatever
    # the preset says". position_x/position_y are 0-100 (percentage of video width/height,
    # placing the subtitle's CENTER at that point); both are set together or not at all.
    font_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[ClipStatus] = mapped_column(
        pg_enum(ClipStatus, "clip_status"), nullable=False, default=ClipStatus.PENDING
    )

    clip: Mapped["Clip"] = relationship(back_populates="renders")
