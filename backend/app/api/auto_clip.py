from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.auto_clip import AutoClipRun, AutoClipSettings
from app.models.enums import ScheduledPostStatus
from app.models.scheduled_post import ScheduledPost
from app.models.user import User
from app.schemas.auto_clip import (
    AutoClipRunResponse,
    AutoClipSettingsResponse,
    AutoClipSettingsUpdate,
    CategoryOption,
    PlatformOption,
)
from app.services.auto_clip import service
from app.services.auto_clip.trending import CATEGORIES
from app.workers.tasks.auto_clip_tasks import auto_clip_tick

router = APIRouter(prefix="/auto-clip", tags=["auto-clip"])


def _settings_response(db: Session, user: User, st: AutoClipSettings | None) -> AutoClipSettingsResponse:
    connected = service.connected_platforms(db, user.id)
    platforms = service.selected_platforms(st, connected)
    return AutoClipSettingsResponse(
        enabled=st.enabled if st else False,
        category=st.category if st else service.DEFAULT_CATEGORY,
        region=st.region if st else service.DEFAULT_REGION,
        run_time=st.run_time if st else service.DEFAULT_RUN_TIME,
        timezone=st.timezone if st else "UTC",
        max_clips=st.max_clips if st else service.DEFAULT_MAX_CLIPS,
        last_run_date=st.last_run_date if st else None,
        categories=[CategoryOption(id=k, label=v.label) for k, v in CATEGORIES.items()],
        platforms=platforms,
        platform_options=[
            PlatformOption(id=k, label=label, connected=k in connected) for k, label in service.PLATFORM_LABELS.items()
        ],
        gap_mode=st.gap_mode if st else "random",
        gap_unit=st.gap_unit if st else "minute",
        gap_min=st.gap_min if st else 60,
        # A static wait has no upper end: report it equal to the lower one.
        gap_max=(st.gap_min if st.gap_mode == "static" else st.gap_max) if st else 90,
        can_discover=service.has_credential(db, user.id),
        publish_accounts=len(service.publishable_accounts(db, user.id, platforms)),
        in_progress=service.has_in_progress_run(db, user.id),
    )


def _run_response(db: Session, run: AutoClipRun) -> AutoClipRunResponse:
    counts = dict(
        db.execute(
            select(ScheduledPost.status, func.count(ScheduledPost.id))
            .where(ScheduledPost.auto_run_id == run.id)
            .group_by(ScheduledPost.status)
        ).all()
    )
    failed = counts.get(ScheduledPostStatus.FAILED, 0) + counts.get(ScheduledPostStatus.CANCELLED, 0)
    return AutoClipRunResponse(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        run_date=run.run_date,
        category=run.category,
        source_title=run.source_title,
        source_url=run.source_url,
        video_id=run.video_id,
        clips_total=run.clips_total,
        published=counts.get(ScheduledPostStatus.PUBLISHED, 0),
        failed=failed,
        remaining=len(run.publish_queue or []),
        next_publish_at=run.next_publish_at,
        error=run.error,
        note=run.note,
        created_at=run.created_at,
    )


@router.get("", response_model=AutoClipSettingsResponse)
def get_auto_clip_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _settings_response(db, current_user, service.get_settings(db, current_user.id))


@router.put("", response_model=AutoClipSettingsResponse)
def update_auto_clip_settings(
    payload: AutoClipSettingsUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown timezone") from None
    if payload.enabled and not service.has_credential(db, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect a YouTube account (or set YOUTUBE_API_KEY) before turning this on — it's needed to read trending videos.",
        )

    if payload.platforms is not None:
        connected = service.connected_platforms(db, current_user.id)
        missing = [p for p in payload.platforms if p not in connected]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Connect {service.PLATFORM_LABELS[missing[0]]} in Settings before choosing it.",
            )

    gap = (payload.gap_mode, payload.gap_unit, payload.gap_min, payload.gap_max)
    if any(v is not None for v in gap):
        if payload.gap_mode is None or payload.gap_unit is None or payload.gap_min is None or (
            payload.gap_mode == "random" and payload.gap_max is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Give the wait between uploads as a type, a unit and its time(s) together.",
            )
        gap_max = payload.gap_min if payload.gap_mode == "static" else payload.gap_max
        problem = service.validate_gap(payload.gap_mode, payload.gap_unit, payload.gap_min, gap_max)
        if problem:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

    st = service.get_settings(db, current_user.id)
    was_enabled = bool(st and st.enabled)
    run_time_changed = bool(st and st.run_time != payload.run_time)
    if st is None:
        st = AutoClipSettings(user_id=current_user.id)
        db.add(st)
    st.enabled = payload.enabled
    st.category = payload.category
    st.region = payload.region
    st.run_time = payload.run_time
    st.timezone = payload.timezone
    st.max_clips = payload.max_clips
    if payload.platforms is not None:
        st.platforms = payload.platforms
    if payload.gap_mode is not None:
        st.gap_mode, st.gap_unit, st.gap_min, st.gap_max = payload.gap_mode, payload.gap_unit, payload.gap_min, gap_max

    # Turning it on (or moving the time) after today's run time has already passed should wait
    # for tomorrow instead of firing a surprise run the moment Save is clicked — "Run now" is
    # there for immediate runs.
    if payload.enabled and (not was_enabled or run_time_changed):
        local = service.local_now(st, datetime.now(timezone.utc))
        if local.time() >= service.parse_run_time(st.run_time):
            st.last_run_date = local.date()
    db.commit()
    db.refresh(st)
    return _settings_response(db, current_user, st)


@router.post("/run", response_model=AutoClipRunResponse, status_code=status.HTTP_202_ACCEPTED)
def run_auto_clip_now(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        run = service.request_manual_run(db, current_user.id)
    except service.AutoClipError as exc:
        code = status.HTTP_409_CONFLICT if "in progress" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    auto_clip_tick.delay()  # don't make the user wait up to a minute for the next scheduled tick
    return _run_response(db, run)


@router.post("/stop", response_model=AutoClipRunResponse)
def stop_auto_clip(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stops the run that is in progress (scheduled or manual). It still counts as that day's run,
    so the schedule doesn't start another one straight away; "Run now" works again at once."""
    run = service.cancel_run(db, current_user.id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No auto clip run is in progress.")
    return _run_response(db, run)


@router.get("/runs", response_model=list[AutoClipRunResponse])
def list_auto_clip_runs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    runs = db.scalars(
        select(AutoClipRun).where(AutoClipRun.user_id == current_user.id).order_by(AutoClipRun.created_at.desc()).limit(10)
    ).all()
    return [_run_response(db, r) for r in runs]
