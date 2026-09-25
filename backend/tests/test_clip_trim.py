from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.clip import Clip
from app.models.enums import ClipStatus, VideoCategory
from app.models.user import User
from app.models.video import Video


def _setup_clip(db_session, *, video_duration: float = 120.0) -> tuple[User, Video, Clip]:
    user = User(email="trimmer@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    video = Video(
        user_id=user.id,
        title="Source video",
        original_filename="source.mp4",
        storage_key="videos/source.mp4",
        file_size_bytes=1000,
        duration_seconds=video_duration,
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


def test_trim_clip_enqueues_retrim_and_updates_range(db_session, mocker):
    delay_mock = mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/trim",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 12.0, "end_time": 30.0},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["start_time"] == 12.0
        assert body["end_time"] == 30.0
        assert body["status"] == "RENDERING"

        db_session.refresh(clip)
        assert clip.start_time == 12.0
        assert clip.end_time == 30.0
        assert clip.duration_seconds == 18.0
        assert clip.status == ClipStatus.RENDERING

        delay_mock.assert_called_once_with(str(clip.id))
    finally:
        app.dependency_overrides.clear()


def test_trim_clip_rejects_end_before_start(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/trim",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 30.0, "end_time": 20.0},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_trim_clip_rejects_range_shorter_than_minimum(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/trim",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 10.0, "end_time": 12.0},  # 2s, below MIN_CLIP_DURATION_SECONDS
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_trim_clip_rejects_range_past_video_duration(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session, video_duration=60.0)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/trim",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 50.0, "end_time": 90.0},  # past the 60s source video
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()
