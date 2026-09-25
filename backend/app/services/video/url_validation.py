import ipaddress
import socket
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

    Note: this resolves the hostname once at submit time. It does not pin that IP for the
    actual download later, so it doesn't fully defend against DNS-rebinding — a hostname
    that resolves safely now but repoints to an internal IP by the time the worker fetches
    it. That would need IP-pinning through yt-dlp's socket layer, which is out of scope here.
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
