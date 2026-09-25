from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.clip import Clip
from app.models.enums import ScheduledPostStatus, SocialProvider
from app.models.publish_job import PublishJob
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount


class ScheduledPostCreateRequest(BaseModel):
    clip_id: UUID
    # One row is created per account — this is how "post to 2 accounts at once" works: a single
    # request, one ScheduledPost (and independent retry/failure tracking) per platform.
    social_account_ids: list[UUID] = Field(min_length=1)
    # Required unless publish_now is set, in which case it's ignored (the server uses "now").
    scheduled_at: datetime | None = None
    publish_now: bool = False
    title: str = Field(min_length=1, max_length=255)
    caption: str | None = None


class ScheduledPostUpdateRequest(BaseModel):
    """All fields optional — only editable while status is still SCHEDULED (not yet claimed
    by the dispatcher). None means "leave unchanged", so this can't be used to clear caption;
    send an empty string for that instead."""

    scheduled_at: datetime | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    caption: str | None = None


class PublishJobResponse(BaseModel):
    id: UUID
    status: str
    error_message: str | None
    retry_count: int
    last_attempt_at: datetime | None
    next_retry_at: datetime | None

    model_config = {"from_attributes": True}


class ScheduledPostResponse(BaseModel):
    id: UUID
    clip_id: UUID
    clip_title: str
    clip_thumbnail_url: str | None
    social_account_id: UUID
    platform: SocialProvider
    account_name: str
    scheduled_at: datetime
    status: ScheduledPostStatus
    title: str
    caption: str | None
    published_at: datetime | None
    external_url: str | None
    latest_error: str | None
    created_at: datetime


def build_scheduled_post_response(
    post: ScheduledPost,
    clip: Clip,
    social_account: SocialAccount,
    latest_job: PublishJob | None = None,
) -> ScheduledPostResponse:
    from app.services.storage.storage_service import storage_service

    thumbnail_url = storage_service.generate_presigned_get_url(clip.thumbnail_key) if clip.thumbnail_key else None
    return ScheduledPostResponse(
        id=post.id,
        clip_id=post.clip_id,
        clip_title=clip.title,
        clip_thumbnail_url=thumbnail_url,
        social_account_id=post.social_account_id,
        platform=post.platform,
        account_name=social_account.account_name,
        scheduled_at=post.scheduled_at,
        status=post.status,
        title=post.title,
        caption=post.caption,
        published_at=post.published_at,
        external_url=post.external_url,
        latest_error=latest_job.error_message if latest_job else None,
        created_at=post.created_at,
    )
