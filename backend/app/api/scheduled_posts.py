import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.enums import ClipStatus, ScheduledPostStatus
from app.models.publish_job import PublishJob
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.repositories.clip_repository import ClipRepository
from app.schemas.scheduled_post import (
    ScheduledPostCreateRequest,
    ScheduledPostResponse,
    ScheduledPostUpdateRequest,
    build_scheduled_post_response,
)
from app.workers.tasks.publish_tasks import enqueue_publish

router = APIRouter(prefix="/scheduled-posts", tags=["scheduled-posts"])


def _latest_job(db: Session, scheduled_post_id: UUID) -> PublishJob | None:
    return db.execute(
        select(PublishJob)
        .where(PublishJob.scheduled_post_id == scheduled_post_id)
        .order_by(PublishJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _get_owned_post(db: Session, post_id: UUID, user_id: UUID) -> ScheduledPost | None:
    return db.execute(
        select(ScheduledPost)
        .join(SocialAccount, SocialAccount.id == ScheduledPost.social_account_id)
        .where(ScheduledPost.id == post_id, SocialAccount.user_id == user_id)
    ).scalar_one_or_none()


@router.get("", response_model=list[ScheduledPostResponse])
def list_scheduled_posts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    posts = db.execute(
        select(ScheduledPost)
        .join(SocialAccount, SocialAccount.id == ScheduledPost.social_account_id)
        .where(SocialAccount.user_id == current_user.id)
        .order_by(ScheduledPost.scheduled_at)
    ).scalars().all()

    responses = []
    for post in posts:
        clip = ClipRepository(db).get_for_user(post.clip_id, current_user.id)
        social_account = db.get(SocialAccount, post.social_account_id)
        if clip is None or social_account is None:
            continue
        latest_job = _latest_job(db, post.id) if post.status in (ScheduledPostStatus.FAILED, ScheduledPostStatus.PROCESSING) else None
        responses.append(build_scheduled_post_response(post, clip, social_account, latest_job))
    return responses


@router.post("", response_model=list[ScheduledPostResponse], status_code=status.HTTP_201_CREATED)
def create_scheduled_post(
    payload: ScheduledPostCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Creates one ScheduledPost per account in `social_account_ids` — this is what makes
    posting the same clip to 2+ platforms/accounts a single request instead of one call per
    account, while keeping each account's own retry/failure history independent. With
    `publish_now`, every row created here is claimed and dispatched immediately instead of
    waiting for the next dispatch_due_scheduled_posts tick (up to 60s away)."""
    clip = ClipRepository(db).get_for_user(payload.clip_id, current_user.id)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    if clip.status != ClipStatus.READY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clip isn't ready to publish yet")

    accounts: list[SocialAccount] = []
    for account_id in payload.social_account_ids:
        account = db.execute(
            select(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.user_id == current_user.id)
        ).scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Social account {account_id} not found")
        accounts.append(account)

    if payload.publish_now:
        scheduled_at = datetime.now(timezone.utc)
    else:
        if payload.scheduled_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="scheduled_at is required unless publish_now is set"
            )
        scheduled_at = payload.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scheduled_at must be in the future")

    posts: list[ScheduledPost] = []
    for account in accounts:
        post = ScheduledPost(
            clip_id=clip.id,
            social_account_id=account.id,
            platform=account.provider,
            scheduled_at=scheduled_at,
            title=payload.title,
            caption=payload.caption,
            status=ScheduledPostStatus.SCHEDULED,
        )
        db.add(post)
        posts.append(post)
    db.commit()
    for post in posts:
        db.refresh(post)

    if payload.publish_now:
        # Claim right away (mirrors dispatch_due_scheduled_posts' own claim step) so the worker
        # doesn't have to wait for a due-post scan that only runs once a minute.
        for post in posts:
            post.status = ScheduledPostStatus.PROCESSING
            post.run_token = uuid.uuid4().hex
        db.commit()
        for post in posts:
            if not enqueue_publish(post.id, post.run_token):
                db.refresh(post)  # back to SCHEDULED: the dispatcher queues it within a minute

    return [build_scheduled_post_response(post, clip, account) for post, account in zip(posts, accounts)]


@router.patch("/{post_id}", response_model=ScheduledPostResponse)
def update_scheduled_post(
    post_id: UUID,
    payload: ScheduledPostUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = _get_owned_post(db, post_id, current_user.id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found")
    if post.status != ScheduledPostStatus.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can't edit a post that's already {post.status.value.lower()}",
        )

    if payload.scheduled_at is not None:
        scheduled_at = payload.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scheduled_at must be in the future")
        post.scheduled_at = scheduled_at
    if payload.title is not None:
        post.title = payload.title
    if payload.caption is not None:
        post.caption = payload.caption
    db.commit()
    db.refresh(post)

    clip = ClipRepository(db).get_for_user(post.clip_id, current_user.id)
    social_account = db.get(SocialAccount, post.social_account_id)
    return build_scheduled_post_response(post, clip, social_account)


_CANCELLABLE = (ScheduledPostStatus.SCHEDULED, ScheduledPostStatus.PROCESSING, ScheduledPostStatus.UPLOADING)
_RETRYABLE = (ScheduledPostStatus.FAILED, ScheduledPostStatus.CANCELLED)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_scheduled_post(post_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cancels a post at any point before it's published — still scheduled, being prepared,
    waiting out an automatic retry, or mid-upload. The row is kept (status CANCELLED) so it stays
    visible and can be retried by hand. Clearing `run_token` is what actually stops the work: an
    upload in flight notices at its next chunk, and a retry already queued with the old token
    stands down when it fires — so cancelling also ends the automatic retrying."""
    post = _get_owned_post(db, post_id, current_user.id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found")

    # Conditional UPDATE rather than set-then-commit: if the worker publishes it in the same
    # instant, this matches no row and the (already-published) post is left alone.
    result = db.execute(
        update(ScheduledPost)
        .where(ScheduledPost.id == post.id, ScheduledPost.status.in_(_CANCELLABLE))
        .values(status=ScheduledPostStatus.CANCELLED, run_token=None, progress=0)
    )
    db.commit()
    if result.rowcount == 0:
        db.refresh(post)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can't cancel a post that's already {post.status.value.lower()}",
        )


@router.post("/{post_id}/retry", response_model=ScheduledPostResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_scheduled_post(post_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Manually re-runs a FAILED or CANCELLED post right away. It starts a fresh attempt chain
    (new run_token, so automatic retries apply again from the first backoff step) and ignores
    the original scheduled time — for a cancelled post that was still waiting for its slot, this
    means "post it now"."""
    post = _get_owned_post(db, post_id, current_user.id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found")
    if post.status not in _RETRYABLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a failed or cancelled post can be retried",
        )

    clip = ClipRepository(db).get_for_user(post.clip_id, current_user.id)
    social_account = db.get(SocialAccount, post.social_account_id)
    if clip is None or social_account is None or clip.status != ClipStatus.READY or not clip.storage_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Clip isn't ready to publish")

    token = uuid.uuid4().hex
    result = db.execute(
        update(ScheduledPost)
        .where(ScheduledPost.id == post.id, ScheduledPost.status.in_(_RETRYABLE))
        .values(status=ScheduledPostStatus.PROCESSING, progress=0, run_token=token)
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This post was just changed — refresh and retry")

    enqueue_publish(post.id, token)
    db.refresh(post)
    return build_scheduled_post_response(post, clip, social_account)
