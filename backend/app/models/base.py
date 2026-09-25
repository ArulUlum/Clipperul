import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column


def pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    """SQLAlchemy's Enum() sends the Python member NAME to the DB by default (e.g. "PODCAST"),
    not its .value ("podcast") — a mismatch for every str-Enum here whose member names differ
    from their values (VideoCategory, AspectRatio, SocialProvider). values_callable makes it
    send .value instead, matching the lowercase values the Postgres enum type was created with.
    """
    return Enum(enum_cls, name=name, values_callable=lambda cls: [e.value for e in cls])


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
