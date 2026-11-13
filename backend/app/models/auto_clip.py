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
    # Which platforms a run posts to — SocialProvider values ("youtube", "tiktok", "instagram"). Null =
    # never chosen: every connected account that can post. An empty list = none (clips are only made).
    platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The wait between one upload and the next on the same platform. "static": always gap_min; "random":
    # a fresh random value between gap_min and gap_max each time. Both numbers are in gap_unit
    # ("minute" or "hour"). The defaults are the original fixed behavior: random, 60-90 minutes.
    gap_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="random", server_default="random")
    gap_unit: Mapped[str] = mapped_column(String(10), nullable=False, default="minute", server_default="minute")
    gap_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    gap_max: Mapped[int] = mapped_column(Integer, nullable=False, default=90, server_default="90")


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

    # Pending uploads, in order. One upload at a time PER PLATFORM: an upload is created and
    # dispatched only after the previous one on the same platform has finished; different platforms
    # run side by side. List of {"clip_id", "account_id"}.
    publish_queue: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    clips_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # While PUBLISHING: per platform ("youtube", "tiktok", ...), the earliest time its next upload
    # may start, as an ISO timestamp. Set to a random 60-90 min after an upload on that platform, so
    # a platform's posts aren't clustered together. A platform with no entry has nothing to wait for.
    next_publish_by_platform: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    @property
    def next_publish_at(self) -> datetime | None:
        """The soonest pending deadline across platforms — what the UI shows as "next upload"."""
        deadlines = [datetime.fromisoformat(v) for v in (self.next_publish_by_platform or {}).values()]
        return min(deadlines) if deadlines else None

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
