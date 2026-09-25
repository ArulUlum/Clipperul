from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.clip import Clip
from app.models.enums import AspectRatio, ClipStatus, VideoCategory
from app.models.user import User
from app.models.video import Video


def _setup_clip(db_session) -> tuple[User, Video, Clip]:
    user = User(email="ratio@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    video = Video(
        user_id=user.id,
        title="Source video",
        original_filename="source.mp4",
        storage_key="videos/source.mp4",
        file_size_bytes=1000,
        duration_seconds=120.0,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    clip = Clip(
        video_id=video.id,
        title="Highlight",
        start_time=10.0,
        end_time=25.0,
        score=80.0,
        category=VideoCategory.GAMING,
        status=ClipStatus.READY,
        storage_key="clips/clip.mp4",
        duration_seconds=15.0,
        aspect_ratio=AspectRatio.VERTICAL_9_16,
    )
    db_session.add(clip)
    db_session.commit()
    db_session.refresh(clip)

    return user, video, clip


def test_change_aspect_ratio_updates_clip_and_enqueues_retrim(db_session, mocker):
    delay_mock = mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/aspect-ratio",
            headers={"Authorization": f"Bearer {token}"},
            json={"aspect_ratio": "3:4"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["aspect_ratio"] == "3:4"
        assert body["status"] == "RENDERING"

        db_session.refresh(clip)
        assert clip.aspect_ratio == AspectRatio.PORTRAIT_3_4
        assert clip.status == ClipStatus.RENDERING
        # start/end are untouched by an aspect-ratio-only change.
        assert clip.start_time == 10.0
        assert clip.end_time == 25.0

        delay_mock.assert_called_once_with(str(clip.id))
    finally:
        app.dependency_overrides.clear()


def test_change_aspect_ratio_to_1_1(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/aspect-ratio",
            headers={"Authorization": f"Bearer {token}"},
            json={"aspect_ratio": "1:1"},
        )
        assert response.status_code == 202
        assert response.json()["aspect_ratio"] == "1:1"
    finally:
        app.dependency_overrides.clear()


def test_change_aspect_ratio_rejects_unknown_value(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/aspect-ratio",
            headers={"Authorization": f"Bearer {token}"},
            json={"aspect_ratio": "21:9"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_change_aspect_ratio_404_for_other_users_clip(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    other_user = User(email="not-the-owner@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    token = create_access_token(other_user.id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/aspect-ratio",
            headers={"Authorization": f"Bearer {token}"},
            json={"aspect_ratio": "1:1"},
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
