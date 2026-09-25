from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings(current_user: User = Depends(get_current_user)):
    return {"email": current_user.email, "full_name": current_user.full_name}
