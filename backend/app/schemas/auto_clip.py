import re
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.services.auto_clip.trending import CATEGORIES

_RUN_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AutoClipSettingsUpdate(BaseModel):
    enabled: bool
    category: str
    region: str = Field(min_length=2, max_length=2)
    run_time: str
    timezone: str = Field(min_length=1, max_length=64)
    max_clips: int = Field(ge=1, le=8)

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"Unknown category: {value}")
        return value

    @field_validator("region")
    @classmethod
    def _region_code(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("Region must be a 2-letter country code")
        return value.upper()

    @field_validator("run_time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        if not _RUN_TIME.match(value):
            raise ValueError("run_time must be HH:MM (24-hour)")
        return value


class CategoryOption(BaseModel):
    id: str
    label: str


class AutoClipSettingsResponse(BaseModel):
    enabled: bool
    category: str
    region: str
    run_time: str
    timezone: str
    max_clips: int
    last_run_date: date | None
    categories: list[CategoryOption]
    # Whether trending can be read (API key or connected YouTube account) / how many connected
    # accounts a run would actually post to / whether a run is happening right now.
    can_discover: bool
    publish_accounts: int
    in_progress: bool


class AutoClipRunResponse(BaseModel):
    id: UUID
    trigger: str
    status: str
    run_date: date
    category: str
    source_title: str | None
    source_url: str | None
    video_id: UUID | None
    clips_total: int
    published: int
    failed: int
    remaining: int
    next_publish_at: datetime | None
    error: str | None
    note: str | None
    created_at: datetime
