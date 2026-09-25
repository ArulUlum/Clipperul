from uuid import UUID

from pydantic import BaseModel


class SubtitlePresetResponse(BaseModel):
    id: UUID
    name: str
    config: dict
    is_system_default: bool

    model_config = {"from_attributes": True}
