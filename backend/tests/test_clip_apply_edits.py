from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.clip import Clip
from app.models.enums import AspectRatio, ClipStatus, VideoCategory
from app.models.subtitle_preset import SubtitlePreset
from app.models.user import User
from app.models.video import Video


def _setup_clip(db_session, *, video_duration: float = 120.0) -> tuple[User, Video, Clip]:
    user = User(email="editor@example.com", hashed_password=hash_password("supersecret123"))
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
        aspect_ratio=AspectRatio.VERTICAL_9_16,
    )
    db_session.add(clip)
    db_session.commit()
    db_session.refresh(clip)

    return user, video, clip


def test_apply_edits_updates_everything_in_one_call(db_session, mocker):
    delay_mock = mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    preset = SubtitlePreset(
        name="Gaming", config={"font": "Inter", "size": 44, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#FFD400"}
    )
    db_session.add(preset)
    db_session.commit()
    db_session.refresh(preset)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/apply-edits",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "start_time": 12.0,
                "end_time": 30.0,
                "aspect_ratio": "3:4",
                "crop_x": 40.0,
                "crop_y": 60.0,
                "subtitle_preset_id": str(preset.id),
                "subtitle_font_size": 72,
                "subtitle_position_x": 50.0,
                "subtitle_position_y": 80.0,
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["start_time"] == 12.0
        assert body["end_time"] == 30.0
        assert body["aspect_ratio"] == "3:4"
        assert body["crop_x"] == 40.0
        assert body["crop_y"] == 60.0
        assert body["subtitle_preset_id"] == str(preset.id)
        assert body["subtitle_font_size"] == 72
        assert body["subtitle_position_x"] == 50.0
        assert body["subtitle_position_y"] == 80.0
        assert body["status"] == "RENDERING"

        db_session.refresh(clip)
        assert clip.status == ClipStatus.RENDERING
        # Exactly one re-render for the whole batch of edits, not one per field.
        delay_mock.assert_called_once_with(str(clip.id))
    finally:
        app.dependency_overrides.clear()


def test_apply_edits_allows_clearing_crop_and_subtitles(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    clip.crop_x = 20.0
    clip.crop_y = 30.0
    db_session.commit()
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/apply-edits",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 10.0, "end_time": 25.0, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["crop_x"] is None
        assert body["crop_y"] is None
        assert body["subtitle_preset_id"] is None
    finally:
        app.dependency_overrides.clear()


def test_apply_edits_rejects_invalid_trim_range(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/apply-edits",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 30.0, "end_time": 20.0, "aspect_ratio": "9:16"},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_apply_edits_rejects_unknown_subtitle_preset(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/apply-edits",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "start_time": 10.0,
                "end_time": 25.0,
                "aspect_ratio": "9:16",
                "subtitle_preset_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_apply_edits_rejects_crop_position_without_pair(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    _, _, clip = _setup_clip(db_session)
    token = create_access_token(clip.video.user_id)

    client = TestClient(app)
    try:
        response = client.post(
            f"/api/clips/{clip.id}/apply-edits",
            headers={"Authorization": f"Bearer {token}"},
            json={"start_time": 10.0, "end_time": 25.0, "aspect_ratio": "9:16", "crop_x": 50.0},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()
