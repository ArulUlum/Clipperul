import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.rate_limit import limiter
from app.main import app
from app.models.enums import VideoStatus
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob


@pytest.fixture(autouse=True)
def _disable_upload_rate_limit(mocker):
    # The limiter is process-wide and in-memory, so several uploads across these tests would
    # otherwise trip the per-minute limit and return 429 instead of what's being asserted.
    mocker.patch.object(limiter, "enabled", False)


@pytest.mark.parametrize(
    "filename,content_type,expected_status",
    [
        ("recording.mkv", "", 202),  # browsers on Windows send no type for .mkv (OBS default)
        ("recording.mkv", "application/octet-stream", 202),
        ("old.avi", "video/avi", 202),  # alias of video/x-msvideo
        ("clip.mov", "video/quicktime", 202),
        ("clip.mp4", "text/plain", 400),  # right extension, clearly-not-a-video type
        ("notes.txt", "video/mp4", 400),  # video type, wrong extension
    ],
)
def test_upload_content_type_validation(db_session, mocker, filename, content_type, expected_status):
    mocker.patch("app.services.video.video_service.storage_service.upload_fileobj", return_value="videos/key")
    mocker.patch("app.api.videos.process_video.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    user = User(email=f"types-{uuid.uuid4().hex[:6]}@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id)

    try:
        response = TestClient(app).post(
            "/api/videos",
            headers={"Authorization": f"Bearer {token}"},
            data={"title": "t"},
            files={"file": (filename, io.BytesIO(b"fake-video-bytes"), content_type)},
        )
        assert response.status_code == expected_status
    finally:
        app.dependency_overrides.clear()


def test_create_video_enqueues_job_without_running_pipeline_inline(db_session, mocker):
    mocker.patch("app.services.video.video_service.storage_service.upload_fileobj", return_value="videos/key.mp4")
    delay_mock = mocker.patch("app.api.videos.process_video.delay")

    app.dependency_overrides[deps.get_db] = lambda: db_session

    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    user = User(email="creator@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id)

    client = TestClient(app)
    try:
        response = client.post(
            "/api/videos",
            headers={"Authorization": f"Bearer {token}"},
            data={"title": "My Gaming Highlights"},
            files={"file": ("clip.mp4", io.BytesIO(b"fake-video-bytes"), "video/mp4")},
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"

        # The pipeline must never run inline in the HTTP request — only enqueued.
        delay_mock.assert_called_once()

        video = db_session.get(Video, uuid.UUID(body["video_id"]))
        assert video is not None
        assert video.status == VideoStatus.UPLOADED
        # Category is no longer chosen at upload — it's AI-detected during processing.
        assert video.category is None

        job = db_session.query(VideoProcessingJob).filter_by(video_id=video.id).one()
        assert str(job.id) == body["job_id"]
    finally:
        app.dependency_overrides.clear()
