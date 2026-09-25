from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.enums import ScheduledPostStatus
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.repositories.clip_repository import ClipRepository
from app.repositories.video_repository import VideoRepository
from app.schemas.dashboard import DashboardStatsResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _count_scheduled_posts(db: Session, user_id, status: ScheduledPostStatus) -> int:
    return db.execute(
        select(func.count(ScheduledPost.id))
        .join(SocialAccount, SocialAccount.id == ScheduledPost.social_account_id)
        .where(SocialAccount.user_id == user_id, ScheduledPost.status == status)
    ).scalar_one()


@router.get("/stats", response_model=DashboardStatsResponse)
def get_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    video_repo = VideoRepository(db)
    clip_repo = ClipRepository(db)

    return DashboardStatsResponse(
        total_videos=video_repo.count_for_user(current_user.id),
        total_clips=clip_repo.count_for_user(current_user.id),
        scheduled_posts=_count_scheduled_posts(db, current_user.id, ScheduledPostStatus.SCHEDULED),
        published_posts=_count_scheduled_posts(db, current_user.id, ScheduledPostStatus.PUBLISHED),
        processing_jobs=video_repo.count_active_jobs_for_user(current_user.id),
    )
