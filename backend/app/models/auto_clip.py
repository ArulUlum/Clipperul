import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class AutoClipSettings(Base, UUIDPKMixin, TimestampMixin):
    """One row per user: the daily auto-clip feature's on/off switch and preferences."""

    __tablename__ = "auto_clip_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Key into app.services.auto_clip.trending.CATEGORIES (e.g. "podcast", "gaming").
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="podcast")
    # ISO 3166-1 alpha-2 — "trending" is per country.
    region: Mapped[str] = mapped_column(String(2), nullable=False, default="ID")
    # Local wall-clock time ("HH:MM") the daily run starts at, in `timezone`.
    run_time: Mapped[str] = mapped_column(String(5), nullable=False, default="08:00")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    # Cap on clips published per run (best-scoring first) — YouTube's default API quota only
    # allows a handful of uploads a day per Google Cloud project, so "all of them" isn't safe.
    max_clips: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # The user's LOCAL date of the most recent run — what keeps it to once a day.
    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class AutoClipRun(Base, UUIDPKMixin, TimestampMixin):
    """One daily (or manual) run: pick a trending video -> process it -> publish its clips."""

    __tablename__ = "auto_clip_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)  # "scheduled" | "manual"
    # SELECTING -> STARTING -> PROCESSING -> PUBLISHING -> COMPLETED, or FAILED / CANCELLED.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SELECTING", index=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)

    # The candidate currently being worked on.
    source_video_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("videos.id", ondelete="SET NULL"), nullable=True
    )

    # Next-best trending candidates, tried in order if the chosen one can't be processed
    # (private, region-locked, undownloadable...). List of {"id", "title"}.
    remaining_candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Pending uploads, strictly in order — one is created and dispatched only after the previous
    # one has finished. List of {"clip_id", "account_id"}.
    publish_queue: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    clips_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # While PUBLISHING: the earliest time the next upload may start. Set to a random 60-90 min
    # after an upload, so a run's posts aren't clustered together. None = nothing to wait for.
    next_publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AutoClipSource(Base, UUIDPKMixin, TimestampMixin):
    """A YouTube video this user's auto-clip has already used — never picked again."""

    __tablename__ = "auto_clip_sources"
    __table_args__ = (UniqueConstraint("user_id", "youtube_video_id", name="uq_auto_clip_sources_user_video"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    youtube_video_id: Mapped[str] = mapped_column(String(20), nullable=False)
