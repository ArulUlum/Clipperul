from sqlalchemy import JSON, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPKMixin


class SubtitlePreset(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "subtitle_presets"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_system_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
