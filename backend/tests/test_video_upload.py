import io
import uuid

from fastapi.testclient import TestClient

from app.api import deps
from app.main import app
from app.models.enums import VideoStatus
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob


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
