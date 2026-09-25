from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.publish_job import PublishJob
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.schemas.scheduled_post import PublishJobResponse

router = APIRouter(prefix="/publishing", tags=["publishing"])


@router.get("/{scheduled_post_id}/status", response_model=list[PublishJobResponse])
def publishing_status(scheduled_post_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Full attempt history for one scheduled post (newest first) — lets the frontend show
    why a post failed and how many retries are left, beyond the single-line summary the
    scheduled-posts list already carries."""
    post = db.execute(
        select(ScheduledPost)
        .join(SocialAccount, SocialAccount.id == ScheduledPost.social_account_id)
        .where(ScheduledPost.id == scheduled_post_id, SocialAccount.user_id == current_user.id)
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found")

    jobs = db.execute(
        select(PublishJob).where(PublishJob.scheduled_post_id == post.id).order_by(PublishJob.created_at.desc())
    ).scalars().all()
    return jobs
