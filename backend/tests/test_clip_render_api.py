from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.clip import Clip
from app.models.enums import ClipStatus, VideoCategory
from app.models.user import User
from app.models.video import Video


def _setup_clip(db_session) -> tuple[User, Video, Clip]:
    user = User(email="renderer@example.com", hashed_password=hash_password("supersecret123"))
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
    )
    db_session.add(clip)
    db_session.commit()
    db_session.refresh(clip)

    return user, video, clip


def test_render_accepts_custom_font_size_and_position(db_session, mocker):
    delay_mock = mocker.patch("app.api.clips.render_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/render",
            headers={"Authorization": f"Bearer {token}"},
            json={"font_size": 72, "position_x": 30.0, "position_y": 80.0},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["font_size"] == 72
        assert body["position_x"] == 30.0
        assert body["position_y"] == 80.0
        delay_mock.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_render_rejects_font_size_out_of_range(db_session, mocker):
    mocker.patch("app.api.clips.render_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/render",
            headers={"Authorization": f"Bearer {token}"},
            json={"font_size": 500},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_render_rejects_position_given_without_its_pair(db_session, mocker):
    mocker.patch("app.api.clips.render_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/render",
            headers={"Authorization": f"Bearer {token}"},
            json={"position_x": 50.0},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_render_rejects_position_out_of_0_to_100_range(db_session, mocker):
    mocker.patch("app.api.clips.render_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/render",
            headers={"Authorization": f"Bearer {token}"},
            json={"position_x": 50.0, "position_y": 150.0},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_render_without_position_fields_still_works(db_session, mocker):
    delay_mock = mocker.patch("app.api.clips.render_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/render",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["font_size"] is None
        assert body["position_x"] is None
        assert body["position_y"] is None
        delay_mock.assert_called_once()
    finally:
        app.dependency_overrides.clear()
