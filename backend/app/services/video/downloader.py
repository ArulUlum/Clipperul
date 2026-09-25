import glob
from pathlib import Path

from app.core.config import settings
from app.services.video.exceptions import PipelineError


def download_video_from_url(url: str, output_path: str) -> None:
    """Downloads a YouTube (or any yt-dlp-supported / direct-file) URL to output_path.
    output_path should end in .mp4 — yt-dlp is forced to remux/merge to mp4 so the rest of
    the pipeline (ffprobe/ffmpeg) always sees a consistent container."""
    import yt_dlp

    output = Path(output_path)
    template = str(output.with_suffix("")) + ".%(ext)s"

    ydl_opts = {
        "outtmpl": template,
        # Prefer H.264 (avc1) capped at 1080p — universally decodable by ffmpeg/OpenCV.
        # Modern YouTube often serves AV1/VP9 for "best", which this environment's OpenCV
        # build can't hardware- or software-decode, making scene detection crawl or fail.
        # Falls through to whatever's available if no avc1 stream exists.
        "format": (
            "bv*[vcodec^=avc1][height<=1080]+ba/b[vcodec^=avc1][height<=1080]"
            "/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        # "quiet" alone still leaves the per-chunk progress ticker on, which Celery captures
        # from stdout/stderr and re-emits as one WARNING log line per tick — floods the
        # worker logs on any large download. This is the separate flag that actually mutes it.
        "noprogress": True,
        "noplaylist": True,
        "max_filesize": settings.MAX_UPLOAD_SIZE_BYTES,
        "socket_timeout": 30,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise PipelineError("URL_DOWNLOAD_FAILED", f"Could not download video from URL: {exc}") from exc

    if not output.exists():
        # merge_output_format should guarantee .mp4, but fall back to whatever extension
        # yt-dlp actually produced (e.g. a source with no audio track to merge) and rename it.
        candidates = glob.glob(str(output.with_suffix(".*")))
        if not candidates:
            raise PipelineError("URL_DOWNLOAD_FAILED", "Download finished but no output file was found")
        Path(candidates[0]).rename(output)
