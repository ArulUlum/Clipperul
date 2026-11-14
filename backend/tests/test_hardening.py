"""Production secrets guard, streamed uploads, and process_video idempotency."""
import inspect
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import deps, videos as videos_api
from app.core.config import Settings
from app.core.rate_limit import limiter
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.enums import JobStatus, VideoStatus
from app.models.transcript import Transcript
from app.models.user import User
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob
from app.services.video.exceptions import PipelineError
from app.workers.celery_app import celery_app
from app.workers.tasks import video_tasks

STRONG = "a-long-random-secret-value-of-40-characters!!"


# --------------------------------------------------------------------------- secrets guard

def _settings(**kw):
    return Settings(_env_file=None, **kw)


@pytest.mark.parametrize(
    "jwt,key",
    [
        ("change-me-in-env", STRONG),  # the code default
        (STRONG, "change-me-32-byte-fernet-key-please"),  # long, but still the default
        ("dev-only-change-me", STRONG),  # the .env.example placeholder: too short
        (STRONG, "short"),
    ],
)
def test_production_refuses_default_or_weak_secrets(jwt, key):
    with pytest.raises(ValidationError, match="must be a real secret"):
        _settings(ENV="production", JWT_SECRET=jwt, TOKEN_ENCRYPTION_KEY=key)


def test_production_accepts_real_secrets_and_development_keeps_working_with_placeholders():
    assert _settings(ENV="production", JWT_SECRET=STRONG, TOKEN_ENCRYPTION_KEY=STRONG).ENV == "production"
    assert _settings(ENV="development", JWT_SECRET="dev", TOKEN_ENCRYPTION_KEY="dev").ENV == "development"
    assert _settings().ENV == "development"  # the default environment is not strict


# --------------------------------------------------------------------------- upload

@pytest.fixture()
def uploader(db_session, mocker):
    mocker.patch.object(limiter, "enabled", False)
    mocker.patch("app.api.videos.process_video.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session
    user = User(email=f"up-{uuid.uuid4().hex[:6]}@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

    def upload(content=b"fake-video-bytes", title="t"):
        return TestClient(app).post(
            "/api/videos", headers=headers, data={"title": title}, files={"file": ("a.mp4", io.BytesIO(content), "video/mp4")}
        )

    yield upload
    app.dependency_overrides.clear()


def test_the_upload_is_streamed_from_the_spooled_file_not_read_into_memory(uploader, db_session, mocker):
    seen = {}

    def fake_upload(fileobj, key, content_type=None):
        seen["position"] = fileobj.tell()
        seen["content"] = fileobj.read()
        seen["type"] = type(fileobj).__name__
        return key

    mocker.patch("app.services.video.video_service.storage_service.upload_fileobj", side_effect=fake_upload)

    response = uploader(b"x" * 1000)

    assert response.status_code == 202
    assert seen["position"] == 0 and seen["content"] == b"x" * 1000  # rewound after measuring the size
    assert seen["type"] != "BytesIO"  # the multipart parser's own spooled file, not a copy of all the bytes
    assert db_session.query(Video).one().file_size_bytes == 1000


def test_an_oversized_upload_is_refused_before_anything_is_stored(uploader, mocker):
    store = mocker.patch("app.services.video.video_service.storage_service.upload_fileobj")
    mocker.patch("app.services.video.video_service.settings.MAX_UPLOAD_SIZE_BYTES", 10)

    response = uploader(b"x" * 11)

    assert response.status_code == 400 and "maximum" in response.json()["detail"]
    store.assert_not_called()


def test_an_empty_upload_and_an_overlong_title_are_refused(uploader, mocker):
    store = mocker.patch("app.services.video.video_service.storage_service.upload_fileobj")
    assert uploader(b"").status_code == 400
    assert uploader(b"data", title="t" * 256).status_code == 422
    store.assert_not_called()


def test_the_upload_endpoint_is_sync_so_a_long_transfer_cannot_block_the_event_loop():
    assert not inspect.iscoroutinefunction(videos_api.create_video)


# --------------------------------------------------------------------------- process_video

def test_celery_waits_long_enough_before_resending_a_slow_task():
    assert celery_app.conf.broker_transport_options["visibility_timeout"] >= 6 * 3600


def _video_and_job(db_session, status):
    user = User(email=f"pv-{uuid.uuid4().hex[:6]}@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    video = Video(
        user_id=user.id, title="V", original_filename="v.mp4", storage_key="videos/v.mp4", file_size_bytes=1, status=status,
    )
    db_session.add(video)
    db_session.commit()
    job = VideoProcessingJob(video_id=video.id, status=JobStatus.PENDING, current_stage=VideoStatus.QUEUED)
    db_session.add(job)
    db_session.commit()
    return video, job


def test_a_redelivered_task_for_an_already_processed_video_does_nothing(db_session, mocker):
    video, job = _video_and_job(db_session, VideoStatus.COMPLETED)
    video_id = video.id  # the task closes the shared session, so read the row again afterwards
    mocker.patch.object(video_tasks, "SessionLocal", return_value=db_session)
    download = mocker.patch.object(video_tasks.storage_service, "download_to_file")

    video_tasks.process_video.run(str(job.id))

    download.assert_not_called()
    assert db_session.query(Transcript).count() == 0
    assert db_session.get(Video, video_id).status == VideoStatus.COMPLETED


def test_a_video_that_is_not_done_is_still_processed(db_session, mocker):
    video, job = _video_and_job(db_session, VideoStatus.UPLOADED)
    video_id = video.id
    mocker.patch.object(video_tasks, "SessionLocal", return_value=db_session)
    download = mocker.patch.object(
        video_tasks.storage_service, "download_to_file", side_effect=PipelineError("DOWNLOAD_FAILED", "storage down")
    )

    video_tasks.process_video.run(str(job.id))

    download.assert_called_once()  # the guard did not skip it
    assert db_session.get(Video, video_id).status == VideoStatus.FAILED
