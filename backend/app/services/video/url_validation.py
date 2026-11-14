import ipaddress
import socket
import threading
from contextlib import contextmanager
from urllib.parse import urlparse

from app.services.video.exceptions import PipelineError

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_video_url(url: str) -> None:
    """Rejects anything that isn't a plain public http(s) URL, to stop the worker (which
    runs inside the docker network) from being used as an SSRF vector against internal
    services (postgres/redis/minio/backend) or cloud metadata endpoints.

    This only checks the URL as submitted, for quick feedback. What protects the worker is
    public_addresses_only(), which the download runs inside: it refuses every connection to a
    non-public address, including redirects and a hostname that changes its answer later.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PipelineError("INVALID_URL", "Only http:// and https:// URLs are supported")

    hostname = parsed.hostname
    if not hostname:
        raise PipelineError("INVALID_URL", "URL is missing a host")
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise PipelineError("INVALID_URL", "This URL is not allowed")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise PipelineError("INVALID_URL", "Could not resolve the URL's host") from exc

    for info in addr_infos:
        raw_ip = info[4][0]
        ip = ipaddress.ip_address(raw_ip.split("%")[0])  # strip IPv6 zone id if present
        if _is_disallowed_ip(ip):
            raise PipelineError("INVALID_URL", "This URL points to a disallowed network address")


# --------------------------------------------------------------------------- guard for the download itself

_guard = threading.local()
_real_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, *args, **kwargs):
    infos = _real_getaddrinfo(host, *args, **kwargs)
    if getattr(_guard, "active", False):
        for info in infos:
            if _is_disallowed_ip(ipaddress.ip_address(info[4][0].split("%")[0])):
                raise socket.gaierror(socket.EAI_NONAME, "address not allowed")
    return infos


# Every Python HTTP stack (urllib, requests/urllib3, http.client) resolves through socket.getaddrinfo right
# before it connects, so checking there covers each redirect hop and DNS rebinding. It does nothing unless
# the calling thread is inside public_addresses_only().
# shortcut: a downloader that resolves names itself (an external ffmpeg, a libcurl build) is not covered; yt-dlp
# here uses urllib/requests only, so revisit if curl_cffi or an external downloader is ever enabled.
socket.getaddrinfo = _guarded_getaddrinfo


@contextmanager
def public_addresses_only():
    """While active in this thread, connecting to a loopback, private, link-local or otherwise non-public
    address fails like an unresolvable host."""
    _guard.active = True
    try:
        yield
    finally:
        _guard.active = False
