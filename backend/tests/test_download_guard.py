"""The download runs inside public_addresses_only(): redirects and rebinding can't reach internal services."""
import http.server
import socket
import threading
import urllib.request

import pytest

from app.services.video import downloader, url_validation
from app.services.video.exceptions import PipelineError
from app.services.video.url_validation import public_addresses_only


@pytest.fixture()
def local_server():
    """A real HTTP server on loopback, standing in for an internal service (redis, minio, the metadata API)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"internal secret")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "0.0.0.0"])
def test_non_public_addresses_do_not_resolve_inside_the_guard(host):
    with public_addresses_only():
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(host, 80)


def test_public_addresses_still_resolve_inside_the_guard():
    with public_addresses_only():
        assert socket.getaddrinfo("8.8.8.8", 80)  # an IP literal needs no network


def test_outside_the_guard_nothing_changes(local_server):
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert urllib.request.urlopen(f"http://127.0.0.1:{local_server}/").read() == b"internal secret"


def test_an_internal_service_cannot_be_reached_from_inside_the_guard(local_server):
    with public_addresses_only():
        with pytest.raises(OSError):  # URLError wraps the failed lookup
            urllib.request.urlopen(f"http://127.0.0.1:{local_server}/")
        with pytest.raises(OSError):
            socket.create_connection(("localhost", local_server))  # what every redirect hop ends up calling
    assert urllib.request.urlopen(f"http://127.0.0.1:{local_server}/").read() == b"internal secret"  # and it is off again


def test_the_guard_is_off_again_after_an_error():
    with pytest.raises(RuntimeError):
        with public_addresses_only():
            raise RuntimeError("download failed")
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_the_guard_only_applies_to_the_thread_that_asked_for_it():
    """The worker's own connections (database, redis, storage) must keep working while a download runs."""
    result = {}

    def other_thread():
        result["resolved"] = bool(socket.getaddrinfo("127.0.0.1", 80))

    with public_addresses_only():
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
    assert result["resolved"] is True


def test_the_download_runs_inside_the_guard_and_leaves_it_afterwards(mocker, tmp_path):
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            seen["active"] = getattr(url_validation._guard, "active", False)
            (tmp_path / "out.mp4").write_bytes(b"video")

    mocker.patch("yt_dlp.YoutubeDL", FakeYDL)
    downloader.download_video_from_url("http://8.8.8.8/v.mp4", str(tmp_path / "out.mp4"))

    assert seen["active"] is True
    assert getattr(url_validation._guard, "active", False) is False


def test_a_blocked_redirect_target_is_a_failed_download_with_a_clear_error(mocker, tmp_path):
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            socket.getaddrinfo("169.254.169.254", 80)  # the redirect target, resolved while downloading

    mocker.patch("yt_dlp.YoutubeDL", FakeYDL)
    with pytest.raises(PipelineError) as error:
        downloader.download_video_from_url("http://8.8.8.8/v.mp4", str(tmp_path / "out.mp4"))
    assert error.value.code == "URL_DOWNLOAD_FAILED" and "address not allowed" in error.value.message
    assert getattr(url_validation._guard, "active", False) is False


def test_the_real_yt_dlp_never_reaches_an_internal_service_through_the_guard():
    """End to end with the actual yt-dlp: a counting server on loopback stands in for an internal service."""
    import yt_dlp

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/video.mp4"
    options = {"quiet": True, "no_warnings": True, "socket_timeout": 5, "retries": 0}
    try:
        with pytest.raises(yt_dlp.utils.DownloadError):
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=False)
        assert hits, "control: without the guard yt-dlp does call the internal service"

        hits.clear()
        with pytest.raises((yt_dlp.utils.DownloadError, OSError)):
            with public_addresses_only(), yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=False)
        assert hits == []  # not one request got through
    finally:
        server.shutdown()
