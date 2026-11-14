import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import hash_password, verify_password
from app.main import app
from app.services.ai import transcription


# --------------------------------------------------------------------------- 13: bcrypt's 72-byte limit

def _register(db_session, password):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        return TestClient(app).post("/api/auth/register", json={"email": "pw@example.com", "password": password})
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "password,status",
    [
        ("a" * 72, 201),  # exactly the limit
        ("a" * 73, 422),  # one byte over
        ("é" * 36, 201),  # 72 bytes in 36 characters
        ("é" * 37, 422),  # 74 bytes: short in characters, too long in bytes
        ("🙂" * 19, 422),  # 76 bytes
    ],
)
def test_registration_rejects_a_password_bcrypt_would_silently_cut(db_session, password, status):
    response = _register(db_session, password)
    assert response.status_code == status
    if status == 422:
        assert "72 bytes" in response.text


def test_a_long_password_still_works_for_login_without_an_error():
    """Hashes made before the limit existed were made from the first 72 bytes: they must keep verifying."""
    long_password = "correct horse battery staple " * 5  # 145 bytes
    hashed = hash_password(long_password)

    assert verify_password(long_password, hashed) is True
    assert verify_password(long_password[:72], hashed) is True  # that is what the hash really holds
    assert verify_password("something else entirely", hashed) is False


def test_a_hash_made_the_old_way_from_the_truncated_bytes_still_verifies():
    import bcrypt

    legacy = bcrypt.hashpw(("x" * 90).encode()[:72], bcrypt.gensalt()).decode()  # what bcrypt 4 stored for a long password
    assert verify_password("x" * 90, legacy) is True


# --------------------------------------------------------------------------- 12: one Whisper model per process

def test_the_whisper_model_is_loaded_once_and_reused_for_every_video(mocker):
    loads = []

    class FakeModel:
        def transcribe(self, path, vad_filter=True):
            return [], type("Info", (), {"language": "en"})()

    def fake_whisper(*args, **kwargs):
        loads.append(args)
        return FakeModel()

    mocker.patch("faster_whisper.WhisperModel", side_effect=fake_whisper)
    transcription.get_transcription_provider.cache_clear()
    try:
        for _ in range(3):  # three videos
            transcription.get_transcription_provider().transcribe("audio.wav")
        assert len(loads) == 1
        assert transcription.get_transcription_provider() is transcription.get_transcription_provider()
    finally:
        transcription.get_transcription_provider.cache_clear()
