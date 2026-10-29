from unittest.mock import MagicMock

import pytest

from app.services.social import youtube
from app.services.social.youtube import YouTubeProvider

CHUNK = youtube._UPLOAD_CHUNK_SIZE


def _response(status: int, range_header: str | None = None, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {"Range": range_header} if range_header else {}
    r.json.return_value = body or {}
    return r


def _video(tmp_path, size: int) -> str:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * size)
    return str(path)


def test_chunked_upload_sends_sequential_ranges_and_reports_progress(tmp_path, mocker):
    size = CHUNK * 2 + 100  # two full chunks + a short final one
    put = mocker.patch(
        "app.services.social.youtube.requests.put",
        side_effect=[
            _response(308, f"bytes=0-{CHUNK - 1}"),
            _response(308, f"bytes=0-{CHUNK * 2 - 1}"),
            _response(200, body={"id": "vid123"}),
        ],
    )
    progress: list[float] = []

    result = YouTubeProvider()._upload_in_chunks("https://upload.example", _video(tmp_path, size), size, progress.append)

    assert result == {"id": "vid123"}
    ranges = [call.kwargs["headers"]["Content-Range"] for call in put.call_args_list]
    assert ranges == [
        f"bytes 0-{CHUNK - 1}/{size}",
        f"bytes {CHUNK}-{CHUNK * 2 - 1}/{size}",
        f"bytes {CHUNK * 2}-{size - 1}/{size}",
    ]
    # Reported after each non-final chunk only — 100% is left to the caller, once published.
    assert progress == pytest.approx([CHUNK / size * 100, CHUNK * 2 / size * 100])


def test_chunked_upload_resumes_from_what_the_server_actually_received(tmp_path, mocker):
    size = CHUNK * 2
    half = CHUNK // 2
    put = mocker.patch(
        "app.services.social.youtube.requests.put",
        side_effect=[
            _response(308, f"bytes=0-{half - 1}"),  # server only kept half of the first chunk
            _response(308, f"bytes=0-{half + CHUNK - 1}"),
            _response(201, body={"id": "vid"}),
        ],
    )

    YouTubeProvider()._upload_in_chunks("https://upload.example", _video(tmp_path, size), size, None)

    second = put.call_args_list[1].kwargs["headers"]["Content-Range"]
    assert second.startswith(f"bytes {half}-")


def test_chunked_upload_gives_up_when_server_stops_advancing(tmp_path, mocker):
    size = CHUNK * 3
    mocker.patch("app.services.social.youtube.requests.put", return_value=_response(308, "bytes=0-99"))

    with pytest.raises(RuntimeError, match="stopped making progress"):
        YouTubeProvider()._upload_in_chunks("https://upload.example", _video(tmp_path, size), size, None)


def test_chunked_upload_raises_on_http_error(tmp_path, mocker):
    error = _response(500)
    error.raise_for_status.side_effect = RuntimeError("500 server error")
    mocker.patch("app.services.social.youtube.requests.put", return_value=error)

    with pytest.raises(RuntimeError, match="500"):
        YouTubeProvider()._upload_in_chunks("https://upload.example", _video(tmp_path, 1000), 1000, None)
