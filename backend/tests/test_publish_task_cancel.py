"""The publish worker's cancel/retry behavior, run through the real task body.

The task opens its own session via SessionLocal(), so these tests point that at the in-memory
test database and swap the storage download and the platform adapter for fakes."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.security import hash_password
from app.models.clip import Clip
from app.models.enums import ClipStatus, PublishJobStatus, ScheduledPostStatus, SocialProvider, VideoCategory
from app.models.publish_job import PublishJob
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.models.video import Video
from app.services.social.provider import PublishResult
from app.workers.tasks import publish_tasks

TOKEN = "attempt-1"


@pytest.fixture()
def post(db_session, mocker):
    user = User(email="worker@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    video = Video(
        user_id=user.id, title="v", original_filename="v.mp4", storage_key="videos/v.mp4",
        file_size_bytes=1, duration_seconds=60.0,
    )
    db_session.add(video)
    db_session.commit()
    clip = Clip(
        video_id=video.id, title="c", start_time=0, end_time=20, score=1, category=VideoCategory.GAMING,
        status=ClipStatus.READY, storage_key="clips/c.mp4", duration_seconds=20.0,
    )
    account = SocialAccount(
        user_id=user.id, provider=SocialProvider.YOUTUBE, account_name="a", external_account_id="x",
        encrypted_access_token="enc", connected_at=datetime.now(timezone.utc),
    )
    db_session.add_all([clip, account])
    db_session.commit()
    row = ScheduledPost(
        clip_id=clip.id, social_account_id=account.id, platform=SocialProvider.YOUTUBE,
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1), title="t",
        status=ScheduledPostStatus.PROCESSING, run_token=TOKEN,
    )
    db_session.add(row)
    db_session.commit()

    mocker.patch.object(publish_tasks, "SessionLocal", return_value=db_session)
    mocker.patch.object(publish_tasks.storage_service, "download_to_file")
    # Only the id: the task closes the (shared) session, which detaches ORM instances.
    return SimpleNamespace(id=row.id)


def _use_provider(mocker, publish_video):
    fake = mocker.MagicMock()
    fake.publish_video.side_effect = publish_video
    mocker.patch.object(publish_tasks, "get_social_provider", return_value=fake)
    return fake


def _cancel(db_session, post):
    """What DELETE /scheduled-posts/{id} does, from the point of view of the running worker."""
    row = db_session.get(ScheduledPost, post.id)
    row.status = ScheduledPostStatus.CANCELLED
    row.run_token = None
    db_session.commit()


def _state(db_session, post):
    db_session.expire_all()
    row = db_session.get(ScheduledPost, post.id)
    return row.status, row.progress


def test_successful_attempt_publishes_and_reports_progress(db_session, post, mocker):
    seen = []

    def publish(account_id, path, title, caption, on_progress=None):
        on_progress(50)
        seen.append(_state(db_session, post))
        return PublishResult(external_post_id="vid1", url="https://youtu.be/vid1")

    _use_provider(mocker, publish)
    publish_tasks.publish_scheduled_post_task(str(post.id), TOKEN)

    assert seen == [(ScheduledPostStatus.UPLOADING, 50)]
    status, progress = _state(db_session, post)
    assert (status, progress) == (ScheduledPostStatus.PUBLISHED, 100)
    assert db_session.get(ScheduledPost, post.id).external_url == "https://youtu.be/vid1"


def test_cancelling_mid_upload_stops_the_upload_and_leaves_it_cancelled(db_session, post, mocker):
    def publish(account_id, path, title, caption, on_progress=None):
        on_progress(30)  # first chunk goes through...
        _cancel(db_session, post)  # ...the user cancels...
        on_progress(60)  # ...and the next chunk's progress report must abort the upload
        pytest.fail("upload continued after cancel")

    _use_provider(mocker, publish)
    publish_tasks.publish_scheduled_post_task(str(post.id), TOKEN)  # returns normally: no retry raised

    status, _ = _state(db_session, post)
    assert status == ScheduledPostStatus.CANCELLED
    job = db_session.query(PublishJob).one()
    assert job.status == PublishJobStatus.FAILED
    assert "Cancelled" in job.error_message


def test_a_cancelled_post_is_not_retried_after_the_attempt_then_fails(db_session, post, mocker):
    def publish(account_id, path, title, caption, on_progress=None):
        _cancel(db_session, post)
        raise RuntimeError("network blip")  # would normally queue an automatic retry

    _use_provider(mocker, publish)
    # No self.retry() => no exception escapes the task.
    publish_tasks.publish_scheduled_post_task(str(post.id), TOKEN)

    status, _ = _state(db_session, post)
    assert status == ScheduledPostStatus.CANCELLED


def test_failure_without_cancel_queues_an_automatic_retry(db_session, post, mocker):
    _use_provider(mocker, mocker.MagicMock(side_effect=RuntimeError("network blip")))

    # Called directly (outside a worker) Celery's retry() re-raises the original error.
    with pytest.raises(RuntimeError, match="network blip"):
        publish_tasks.publish_scheduled_post_task(str(post.id), TOKEN)

    status, progress = _state(db_session, post)
    assert (status, progress) == (ScheduledPostStatus.PROCESSING, 0)
    assert db_session.query(PublishJob).one().next_retry_at is not None


def test_a_stale_attempt_stands_down_after_a_manual_retry(db_session, post, mocker):
    # The post was cancelled and manually retried: it now belongs to a NEW token.
    db_session.get(ScheduledPost, post.id).run_token = "attempt-2"
    db_session.commit()
    provider = _use_provider(mocker, lambda *a, **k: pytest.fail("stale attempt published"))

    publish_tasks.publish_scheduled_post_task(str(post.id), TOKEN)  # the old queued retry fires

    provider.publish_video.assert_not_called()
    assert db_session.query(PublishJob).count() == 0
    status, _ = _state(db_session, post)
    assert status == ScheduledPostStatus.PROCESSING


def test_a_retry_firing_after_cancel_does_nothing(db_session, post, mocker):
    _cancel(db_session, post)
    provider = _use_provider(mocker, lambda *a, **k: pytest.fail("cancelled post published"))

    publish_tasks.publish_scheduled_post_task(str(post.id), TOKEN)

    provider.publish_video.assert_not_called()
    status, _ = _state(db_session, post)
    assert status == ScheduledPostStatus.CANCELLED
