from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.clip import ClipRender
from app.models.enums import ClipStatus
from app.models.subtitle_preset import SubtitlePreset
from app.models.user import User
from app.models.video import Video
from app.repositories.clip_repository import ClipRepository
from app.schemas.clip import (
    ClipApplyEditsRequest,
    ClipAspectRatioRequest,
    ClipCropPositionRequest,
    ClipRenderRequest,
    ClipRenderResponse,
    ClipResponse,
    ClipTrimRequest,
    ClipUpdateRequest,
    build_clip_render_response,
    build_clip_response,
)
from app.services.storage.storage_service import storage_service
from app.workers.tasks.clip_render_tasks import activate_clip_render_task, render_clip_task, retrim_clip_task

router = APIRouter(prefix="/clips", tags=["clips"])


@router.get("", response_model=list[ClipResponse])
def list_ready_clips(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Flat, cross-video list of the user's READY clips — used by the schedule-post picker on
    the Scheduled Posts page, which needs a title/thumbnail per clip without the caller having
    to walk every video's own clip list first."""
    return [build_clip_response(c) for c in ClipRepository(db).list_ready_for_user(current_user.id)]


def _validate_position_pair(x: float | None, y: float | None) -> None:
    has_x, has_y = x is not None, y is not None
    if has_x != has_y:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="position_x and position_y must be given together"
        )
    if has_x and not (0 <= x <= 100 and 0 <= y <= 100):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="position_x/position_y must be 0-100")


def _validate_trim_range(start_time: float, end_time: float, video: Video | None) -> float:
    if end_time <= start_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_time must be after start_time")
    duration = end_time - start_time
    if duration < settings.MIN_CLIP_DURATION_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Clip must be at least {settings.MIN_CLIP_DURATION_SECONDS:.0f} seconds long",
        )
    if duration > settings.MAX_CLIP_DURATION_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Clip cannot exceed {settings.MAX_CLIP_DURATION_SECONDS:.0f} seconds",
        )
    if start_time < 0 or (video and video.duration_seconds and end_time > video.duration_seconds):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Trim range is outside the source video")
    return duration


@router.get("/{clip_id}", response_model=ClipResponse)
def get_clip(clip_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clip = ClipRepository(db).get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    return build_clip_response(clip)


@router.patch("/{clip_id}", response_model=ClipResponse)
def update_clip(
    clip_id: UUID,
    payload: ClipUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    if payload.title is not None:
        clip.title = payload.title
    if payload.caption is not None:
        clip.caption = payload.caption

    return build_clip_response(repo.update(clip))


@router.post("/{clip_id}/trim", response_model=ClipResponse, status_code=status.HTTP_202_ACCEPTED)
def trim_clip(
    clip_id: UUID,
    payload: ClipTrimRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Adjusts a clip's start/end range and queues a re-cut from the original source video —
    used when the AI-picked window clipped the hype moment too early/late. Updates the range
    immediately (status flips to RENDERING) so the UI reflects intent right away; the worker
    re-crops (with smart-crop re-run) and re-burns subtitles if they were showing."""
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    video = db.get(Video, clip.video_id)
    duration = _validate_trim_range(payload.start_time, payload.end_time, video)

    clip.start_time = payload.start_time
    clip.end_time = payload.end_time
    clip.duration_seconds = duration
    clip.status = ClipStatus.RENDERING
    repo.update(clip)

    retrim_clip_task.delay(str(clip.id))

    return build_clip_response(clip)


@router.post("/{clip_id}/aspect-ratio", response_model=ClipResponse, status_code=status.HTTP_202_ACCEPTED)
def change_clip_aspect_ratio(
    clip_id: UUID,
    payload: ClipAspectRatioRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-crops an already-generated clip to a different aspect ratio (e.g. 9:16 -> 1:1 or
    3:4), queued the same way as a trim: re-cuts fresh from the original source video (not from
    an already-cropped derivative, which would lose extra content) so quality never degrades
    across repeated changes. Reuses retrim_clip_task, which re-derives its target dimensions
    from clip.aspect_ratio — updating that column here is what makes the same task produce a
    differently-shaped output even though start/end don't change."""
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    clip.aspect_ratio = payload.aspect_ratio
    clip.status = ClipStatus.RENDERING
    repo.update(clip)

    retrim_clip_task.delay(str(clip.id))

    return build_clip_response(clip)


@router.post("/{clip_id}/crop-position", response_model=ClipResponse, status_code=status.HTTP_202_ACCEPTED)
def change_clip_crop_position(
    clip_id: UUID,
    payload: ClipCropPositionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Overrides (or clears) the clip's crop position — used when smart-crop's automatic
    face-tracking picked the wrong subject. Giving both position_x/position_y locks the crop
    window to that fixed point instead of tracking; omitting both clears the override and goes
    back to automatic tracking. Re-crops fresh from the original source video via
    retrim_clip_task, same as an aspect-ratio change."""
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    _validate_position_pair(payload.position_x, payload.position_y)

    clip.crop_x = payload.position_x
    clip.crop_y = payload.position_y
    clip.status = ClipStatus.RENDERING
    repo.update(clip)

    retrim_clip_task.delay(str(clip.id))

    return build_clip_response(clip)


@router.post("/{clip_id}/apply-edits", response_model=ClipResponse, status_code=status.HTTP_202_ACCEPTED)
def apply_clip_edits(
    clip_id: UUID,
    payload: ClipApplyEditsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The clip editor's single 'Save' action — trim range, aspect ratio, crop position, and
    subtitle config are all adjusted live in the browser (no server round-trip per change) and
    only sent here once, together, producing exactly one re-render instead of one per tweak.
    Reuses retrim_clip_task, which already re-derives everything from the clip's current
    columns — this endpoint's only job is validating and writing those columns in one place."""
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    video = db.get(Video, clip.video_id)
    duration = _validate_trim_range(payload.start_time, payload.end_time, video)
    _validate_position_pair(payload.crop_x, payload.crop_y)
    _validate_position_pair(payload.subtitle_position_x, payload.subtitle_position_y)
    if payload.subtitle_font_size is not None and not (8 <= payload.subtitle_font_size <= 200):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subtitle_font_size must be between 8 and 200")
    if payload.subtitle_preset_id is not None and db.get(SubtitlePreset, payload.subtitle_preset_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown subtitle preset")

    clip.start_time = payload.start_time
    clip.end_time = payload.end_time
    clip.duration_seconds = duration
    clip.aspect_ratio = payload.aspect_ratio
    clip.crop_x = payload.crop_x
    clip.crop_y = payload.crop_y
    clip.subtitle_preset_id = payload.subtitle_preset_id
    clip.subtitle_font_size = payload.subtitle_font_size
    clip.subtitle_position_x = payload.subtitle_position_x
    clip.subtitle_position_y = payload.subtitle_position_y
    clip.status = ClipStatus.RENDERING
    repo.update(clip)

    retrim_clip_task.delay(str(clip.id))

    return build_clip_response(clip)


@router.get("/{clip_id}/download")
def download_clip(clip_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns the presigned URL as JSON rather than a 302 — a <video src> or plain browser
    navigation can't attach the Authorization header this endpoint requires, so the frontend
    must fetch this (authenticated) first, then navigate/play the presigned URL it gets back."""
    clip = ClipRepository(db).get_for_user(clip_id, current_user.id)
    if clip is None or clip.storage_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    url = storage_service.generate_presigned_get_url(clip.storage_key)
    return {"url": url}


@router.post("/{clip_id}/render", response_model=ClipRenderResponse, status_code=status.HTTP_202_ACCEPTED)
def render_clip(
    clip_id: UUID,
    payload: ClipRenderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Queues a new render of this clip with the given subtitle preset burned in (or a plain
    re-encode if subtitle_preset_id is omitted). Never blocks on FFmpeg — creates a PENDING
    ClipRender row and hands off to the worker, same queue-only pattern as the main pipeline."""
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    if payload.subtitle_preset_id is not None:
        preset = db.get(SubtitlePreset, payload.subtitle_preset_id)
        if preset is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown subtitle preset")

    if payload.font_size is not None and not (8 <= payload.font_size <= 200):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="font_size must be between 8 and 200")
    _validate_position_pair(payload.position_x, payload.position_y)

    render = ClipRender(
        clip_id=clip.id,
        aspect_ratio=clip.aspect_ratio,
        subtitle_preset_id=payload.subtitle_preset_id,
        font_size=payload.font_size,
        position_x=payload.position_x,
        position_y=payload.position_y,
        status=ClipStatus.PENDING,
    )
    render = repo.create_render(render)

    render_clip_task.delay(str(render.id))

    return build_clip_render_response(render, clip)


@router.get("/{clip_id}/renders", response_model=list[ClipRenderResponse])
def list_clip_renders(clip_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    return [build_clip_render_response(r, clip) for r in repo.list_renders(clip.id)]


@router.post("/{clip_id}/renders/{render_id}/activate", response_model=ClipRenderResponse)
def activate_clip_render(
    clip_id: UUID,
    render_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Switches which existing render (e.g. plain vs. a specific subtitle preset) is the
    clip's primary/shown version — no re-rendering, just a thumbnail refresh + pointer swap.
    This is how 'remove subtitles' works: activate the plain (no-preset) render."""
    repo = ClipRepository(db)
    clip = repo.get_for_user(clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")

    render = repo.get_render_for_user(render_id, current_user.id)
    if render is None or render.clip_id != clip.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Render not found")
    if render.status != ClipStatus.READY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Render is not ready yet")

    activate_clip_render_task.delay(str(clip.id), str(render.id))

    return build_clip_render_response(render, clip)


# Publishing/scheduling a clip is now POST /api/scheduled-posts (see app.api.scheduled_posts) —
# it needs a social_account_id + scheduled_at alongside the clip, which doesn't fit this
# router's clip-scoped shape as a single extra endpoint.
