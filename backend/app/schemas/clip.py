from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.models.clip import Clip, ClipRender
from app.models.enums import AspectRatio, ClipStatus, VideoCategory

ClipLayout = Literal["full", "news", "gaming"]  # see app/services/video/layout.py


class ClipResponse(BaseModel):
    id: UUID
    video_id: UUID
    title: str
    caption: str | None
    start_time: float
    end_time: float
    duration_seconds: float
    score: float
    hook_score: float | None
    flow_score: float | None
    value_score: float | None
    trend_score: float | None
    reason: str | None
    category: VideoCategory
    confidence: float
    status: ClipStatus
    aspect_ratio: AspectRatio
    crop_x: float | None
    crop_y: float | None
    layout: ClipLayout
    facecam_x: float | None
    facecam_y: float | None
    facecam_size: float | None
    subtitle_preset_id: UUID | None
    subtitle_font_size: int | None
    subtitle_position_x: float | None
    subtitle_position_y: float | None
    thumbnail_url: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def build_clip_response(clip: Clip) -> ClipResponse:
    """Constructs the response with a presigned thumbnail URL — thumbnail_key itself is
    never exposed, only a short-lived signed URL, same as clip downloads."""
    from app.services.storage.storage_service import storage_service

    thumbnail_url = storage_service.generate_presigned_get_url(clip.thumbnail_key) if clip.thumbnail_key else None
    return ClipResponse(
        id=clip.id,
        video_id=clip.video_id,
        title=clip.title,
        caption=clip.caption,
        start_time=clip.start_time,
        end_time=clip.end_time,
        duration_seconds=clip.duration_seconds,
        score=clip.score,
        hook_score=clip.hook_score,
        flow_score=clip.flow_score,
        value_score=clip.value_score,
        trend_score=clip.trend_score,
        reason=clip.reason,
        category=clip.category,
        confidence=clip.confidence,
        status=clip.status,
        aspect_ratio=clip.aspect_ratio,
        crop_x=clip.crop_x,
        crop_y=clip.crop_y,
        layout=clip.layout,
        facecam_x=clip.facecam_x,
        facecam_y=clip.facecam_y,
        facecam_size=clip.facecam_size,
        subtitle_preset_id=clip.subtitle_preset_id,
        subtitle_font_size=clip.subtitle_font_size,
        subtitle_position_x=clip.subtitle_position_x,
        subtitle_position_y=clip.subtitle_position_y,
        thumbnail_url=thumbnail_url,
        created_at=clip.created_at,
    )


class ClipUpdateRequest(BaseModel):
    title: str | None = None
    caption: str | None = None


class ClipTrimRequest(BaseModel):
    start_time: float
    end_time: float


class ClipAspectRatioRequest(BaseModel):
    aspect_ratio: AspectRatio


class ClipCropPositionRequest(BaseModel):
    # Both given together = manual override (0-100, percent of source width/height, placing
    # the crop window's CENTER at that point). Both omitted/null = clear the override and go
    # back to automatic face-tracking.
    position_x: float | None = None
    position_y: float | None = None


class ClipApplyEditsRequest(BaseModel):
    """The editor's single 'Save' action — everything a live-preview editing session can change,
    applied together in one re-render instead of one server round-trip per slider tweak."""

    start_time: float
    end_time: float
    aspect_ratio: AspectRatio
    # None/None = auto face-tracking; both given = manual crop override.
    crop_x: float | None = None
    crop_y: float | None = None
    # None = keep the clip's current layout.
    layout: ClipLayout | None = None
    # The gaming layout's face crop (see Clip.facecam_*): all three given = use this one; all None =
    # detect it on the render. Ignored by the other layouts.
    facecam_x: float | None = None
    facecam_y: float | None = None
    facecam_size: float | None = None
    # None subtitle_preset_id = no subtitles on the result.
    subtitle_preset_id: UUID | None = None
    subtitle_font_size: int | None = None
    subtitle_position_x: float | None = None
    subtitle_position_y: float | None = None


class ClipRenderRequest(BaseModel):
    subtitle_preset_id: UUID | None = None
    # Overrides of the preset's own font size / position. font_size stands alone; position_x
    # and position_y (0-100, percent of video width/height, placing the subtitle's CENTER at
    # that point) must be given together or not at all.
    font_size: int | None = None
    position_x: float | None = None
    position_y: float | None = None


class ClipRenderResponse(BaseModel):
    id: UUID
    clip_id: UUID
    aspect_ratio: AspectRatio
    subtitle_preset_id: UUID | None
    font_size: int | None
    position_x: float | None
    position_y: float | None
    status: ClipStatus
    video_url: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


def build_clip_render_response(render: ClipRender, clip: Clip) -> ClipRenderResponse:
    from app.services.storage.storage_service import storage_service

    video_url = storage_service.generate_presigned_get_url(render.storage_key) if render.storage_key else None
    return ClipRenderResponse(
        id=render.id,
        clip_id=render.clip_id,
        aspect_ratio=render.aspect_ratio,
        subtitle_preset_id=render.subtitle_preset_id,
        font_size=render.font_size,
        position_x=render.position_x,
        position_y=render.position_y,
        status=render.status,
        video_url=video_url,
        is_active=render.storage_key is not None and render.storage_key == clip.storage_key,
        created_at=render.created_at,
    )
