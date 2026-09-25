import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import app
from app.services.video.exceptions import PipelineError
from app.services.video.url_validation import validate_video_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/video.mp4",  # loopback
        "http://169.254.169.254/latest/meta-data/",  # link-local / cloud metadata
        "http://10.0.0.5/video.mp4",  # private range
        "http://192.168.1.10/video.mp4",  # private range
        "http://localhost/video.mp4",  # blocked hostname
        "ftp://8.8.8.8/video.mp4",  # disallowed scheme
        "not-a-url",
    ],
)
def test_validate_video_url_rejects_ssrf_targets(url):
    with pytest.raises(PipelineError) as exc_info:
        validate_video_url(url)
    assert exc_info.value.code == "INVALID_URL"


def test_validate_video_url_accepts_public_ip_literal():
    # IP literals skip DNS resolution, so this is deterministic without network access.
    validate_video_url("http://8.8.8.8/video.mp4")


def test_create_video_from_url_rejects_ssrf_target(db_session, mocker):
    mocker.patch("app.api.videos.process_video.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    user = User(email="urluser@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id)

    client = TestClient(app)
    try:
        response = client.post(
            "/api/videos/from-url",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Sneaky", "url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_create_video_from_url_enqueues_job(db_session, mocker):
    delay_mock = mocker.patch("app.api.videos.process_video.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session

    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    user = User(email="urluser2@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id)

    client = TestClient(app)
    try:
        response = client.post(
            "/api/videos/from-url",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "A Public Video", "url": "http://8.8.8.8/video.mp4"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        delay_mock.assert_called_once()
    finally:
        app.dependency_overrides.clear()
