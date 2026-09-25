from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.subtitle_preset import SubtitlePreset
from app.models.user import User
from app.schemas.subtitle_preset import SubtitlePresetResponse

router = APIRouter(prefix="/subtitle-presets", tags=["subtitle-presets"])


@router.get("", response_model=list[SubtitlePresetResponse])
def list_subtitle_presets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.execute(select(SubtitlePreset).order_by(SubtitlePreset.name)).scalars())
