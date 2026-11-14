"""Orphaned render files on delete / re-render, and posts that must not get stuck when queuing fails."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api import deps
from app.core.security import create_access_token
from app.main import app
from app.models.clip import Clip, ClipRender
from app.models.enums import AspectRatio, ClipStatus, ScheduledPostStatus, SocialProvider
from app.models.scheduled_post import ScheduledPost
from app.models.video import Video
from app.workers.tasks import clip_render_tasks, publish_tasks
from tests.test_scheduled_posts import _setup


def _video_with_clip(db_session, user):
    video = Video(
        user_id=user.id, title="v", original_filename="v.mp4", storage_key="videos/v.mp4", file_size_bytes=1,
        duration_seconds=120.0,
    )
    db_session.add(video)
    db_session.commit()
    clip = Clip(
        video_id=video.id, title="c", start_time=10.0, end_time=25.0, score=1, category="gaming",
        status=ClipStatus.READY, storage_key="clips/c_subtitled.mp4", thumbnail_key="clips/c_thumb.jpg",
        duration_seconds=15.0, aspect_ratio=AspectRatio.SQUARE_1_1,
    )
    db_session.add(clip)
    db_session.commit()
    for key in ("clips/c_subtitled.mp4", "clips/c_plain.mp4", "clips/render_old.mp4"):  # one is the active file
        db_session.add(ClipRender(clip_id=clip.id, storage_key=key, aspect_ratio=AspectRatio.SQUARE_1_1, status=ClipStatus.READY))
    db_session.commit()
    return video, clip


# --------------------------------------------------------------------------- 4: stored objects

def test_a_clip_knows_every_object_it_owns(db_session):
    user, *_ = _setup(db_session)
    _, clip = _video_with_clip(db_session, user)
    assert clip.stored_keys() == {"clips/c_subtitled.mp4", "clips/c_thumb.jpg", "clips/c_plain.mp4", "clips/render_old.mp4"}


def test_deleting_a_video_deletes_every_render_not_only_the_active_file(db_session, mocker):
    user, *_ = _setup(db_session)
    video, _ = _video_with_clip(db_session, user)
    delete = mocker.patch("app.api.videos.storage_service.delete")
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        response = TestClient(app).delete(f"/api/videos/{video.id}", headers={"Authorization": f"Bearer {create_access_token(user.id)}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    keys = sorted(c.args[0] for c in delete.call_args_list)
    assert keys == sorted(["videos/v.mp4", "clips/c_subtitled.mp4", "clips/c_thumb.jpg", "clips/c_plain.mp4", "clips/render_old.mp4"])
    assert len(keys) == len(set(keys))  # each object once, even though two rows point at the active file


@pytest.fixture()
def retrim(db_session, mocker):
    user, *_ = _setup(db_session)
    video, clip = _video_with_clip(db_session, user)
    mocker.patch.object(clip_render_tasks, "SessionLocal", return_value=db_session)
    storage = mocker.patch.object(clip_render_tasks, "storage_service")
    mocker.patch.object(clip_render_tasks.pipeline, "trim_clip")
    mocker.patch.object(clip_render_tasks.pipeline, "generate_thumbnail")
    mocker.patch.object(clip_render_tasks.smart_crop, "generate_smart_cropped_clip")
    clip.subtitle_preset_id = None  # no subtitles: the plain render is the active one
    clip.layout = "full"
    db_session.commit()
    ids = SimpleNamespace(clip=clip.id, video=video.id, user=user.id)
    return storage, ids


def test_saving_edits_removes_the_replaced_renders_files_but_not_the_ones_just_written(retrim, db_session):
    storage, ids = retrim
    new_plain = f"clips/{ids.user}/{ids.video}/{ids.clip}_retrim_plain.mp4"
    new_thumb = f"clips/{ids.user}/{ids.video}/{ids.clip}_thumb.jpg"

    clip_render_tasks.retrim_clip_task.run(str(ids.clip))

    deleted = {c.args[0] for c in storage.delete.call_args_list}
    assert deleted == {"clips/c_subtitled.mp4", "clips/c_thumb.jpg", "clips/c_plain.mp4", "clips/render_old.mp4"}
    assert not deleted & {new_plain, new_thumb}  # never deletes what the clip now points at
    clip = db_session.get(Clip, ids.clip)
    assert clip.status == ClipStatus.READY and clip.storage_key == new_plain


def test_a_file_rewritten_under_the_same_key_is_not_deleted(retrim, db_session):
    storage, ids = retrim
    same = f"clips/{ids.user}/{ids.video}/{ids.clip}_retrim_plain.mp4"
    clip = db_session.get(Clip, ids.clip)
    clip.storage_key = same  # a clip that was already re-rendered once
    db_session.add(ClipRender(clip_id=clip.id, storage_key=same, aspect_ratio=AspectRatio.SQUARE_1_1, status=ClipStatus.READY))
    db_session.commit()

    clip_render_tasks.retrim_clip_task.run(str(ids.clip))

    assert same not in {c.args[0] for c in storage.delete.call_args_list}


def test_a_failed_cleanup_leaves_an_orphan_but_never_fails_the_edit(retrim, db_session):
    storage, ids = retrim
    storage.delete.side_effect = RuntimeError("storage down")

    clip_render_tasks.retrim_clip_task.run(str(ids.clip))

    assert db_session.get(Clip, ids.clip).status == ClipStatus.READY  # the new render is in place regardless


# --------------------------------------------------------------------------- 6: queuing fails

def _post(db_session, *, status=ScheduledPostStatus.PROCESSING, token="tok-1"):
    user, clip, account, _ = _setup(db_session)
    row = ScheduledPost(
        clip_id=clip.id, social_account_id=account.id, platform=SocialProvider.YOUTUBE,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1), title="t", status=status, run_token=token,
    )
    db_session.add(row)
    db_session.commit()
    return SimpleNamespace(id=row.id, user=user.id, clip=clip.id, account=account.id)


def _own_session(mocker, db_session):
    """What enqueue_publish gets in production: its OWN session, not the request's."""
    mocker.patch.object(publish_tasks, "SessionLocal", side_effect=lambda: sessionmaker(bind=db_session.get_bind())())


def _row(db_session, post):
    db_session.expire_all()
    return db_session.get(ScheduledPost, post.id)


@pytest.fixture()
def broker_down(mocker):
    return mocker.patch.object(publish_tasks.publish_scheduled_post_task, "delay", side_effect=ConnectionError("redis down"))


def test_a_queued_post_stays_claimed(db_session, mocker):
    post = _post(db_session)
    mocker.patch.object(publish_tasks, "SessionLocal", return_value=db_session)
    delay = mocker.patch.object(publish_tasks.publish_scheduled_post_task, "delay")

    assert publish_tasks.enqueue_publish(post.id, "tok-1") is True

    delay.assert_called_once_with(str(post.id), "tok-1")
    assert _row(db_session, post).status == ScheduledPostStatus.PROCESSING


def test_when_the_broker_is_down_the_post_goes_back_to_scheduled_and_due_now(db_session, mocker, broker_down):
    post = _post(db_session)
    mocker.patch.object(publish_tasks, "SessionLocal", return_value=db_session)

    assert publish_tasks.enqueue_publish(post.id, "tok-1") is False

    row = _row(db_session, post)
    assert row.status == ScheduledPostStatus.SCHEDULED and row.run_token is None
    assert (datetime.now(timezone.utc) - row.scheduled_at.replace(tzinfo=timezone.utc)) < timedelta(minutes=1)  # due now


@pytest.mark.parametrize(
    "status,token",
    [(ScheduledPostStatus.UPLOADING, "tok-1"), (ScheduledPostStatus.CANCELLED, None), (ScheduledPostStatus.PROCESSING, "newer-token")],
)
def test_the_reset_only_touches_the_post_it_queued(db_session, mocker, broker_down, status, token):
    post = _post(db_session, status=status, token=token)  # cancelled, already running, or claimed again meanwhile
    mocker.patch.object(publish_tasks, "SessionLocal", return_value=db_session)

    publish_tasks.enqueue_publish(post.id, "tok-1")

    row = _row(db_session, post)
    assert row.status == status and row.run_token == token


def test_the_dispatcher_recovers_after_the_broker_comes_back(db_session, mocker):
    post = _post(db_session, status=ScheduledPostStatus.SCHEDULED, token=None)
    mocker.patch.object(publish_tasks, "SessionLocal", return_value=db_session)
    delay = mocker.patch.object(publish_tasks.publish_scheduled_post_task, "delay", side_effect=ConnectionError("down"))

    publish_tasks.dispatch_due_scheduled_posts()  # tick 1: claimed, queuing fails -> put back
    assert _row(db_session, post).status == ScheduledPostStatus.SCHEDULED

    delay.side_effect = None
    publish_tasks.dispatch_due_scheduled_posts()  # tick 2, a minute later: claimed and queued
    assert _row(db_session, post).status == ScheduledPostStatus.PROCESSING
    assert delay.call_args.args[0] == str(post.id)


def test_posting_now_with_the_broker_down_still_succeeds_and_the_post_waits_for_the_dispatcher(db_session, mocker, broker_down):
    user, clip, account, _ = _setup(db_session)
    _own_session(mocker, db_session)
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        response = TestClient(app).post(
            "/api/scheduled-posts", headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
            json={"clip_id": str(clip.id), "social_account_ids": [str(account.id)], "publish_now": True, "title": "t"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()[0]["status"] == "SCHEDULED"  # not stuck as PROCESSING


def test_retrying_a_post_with_the_broker_down_does_not_leave_it_processing(db_session, mocker, broker_down):
    post = _post(db_session, status=ScheduledPostStatus.FAILED, token=None)
    _own_session(mocker, db_session)
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        response = TestClient(app).post(
            f"/api/scheduled-posts/{post.id}/retry", headers={"Authorization": f"Bearer {create_access_token(post.user)}"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202 and response.json()["status"] == "SCHEDULED"
