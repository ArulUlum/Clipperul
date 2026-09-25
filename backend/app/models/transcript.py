import uuid

from sqlalchemy import Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class Transcript(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "transcripts"

    video_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    full_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    video: Mapped["Video"] = relationship(back_populates="transcript")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="TranscriptSegment.start_time"
    )


class TranscriptSegment(Base, UUIDPKMixin):
    __tablename__ = "transcript_segments"

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    transcript: Mapped["Transcript"] = relationship(back_populates="segments")
