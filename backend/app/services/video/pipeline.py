import json
import subprocess
from pathlib import Path

from app.services.video.exceptions import PipelineError


def _run(cmd: list[str], error_code: str, error_message: str, cwd: str | None = None) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(error_code, f"{error_message}: {exc.stderr[-2000:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(error_code, f"{error_message}: timed out") from exc
    except FileNotFoundError as exc:
        raise PipelineError(error_code, f"{error_message}: ffmpeg/ffprobe not found") from exc


def get_video_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise PipelineError("PROBE_FAILED", "Unable to read video metadata") from exc
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError("PROBE_FAILED", "Unable to parse video duration") from exc


def extract_audio(video_path: str, output_wav_path: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_wav_path,
    ]
    _run(cmd, "AUDIO_EXTRACTION_FAILED", "Unable to extract audio")


_ASPECT_RATIO_DIMENSIONS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "3:4": (1080, 1440),
    "4:3": (1440, 1080),
    "16:9": (1920, 1080),
}


def _center_crop_scale_filter(target_w: int, target_h: int) -> str:
    """Center-crops the source to the target aspect ratio (never letterboxes/pillarboxes),
    then scales to the exact target resolution. Crops width when the source is wider than
    the target ratio (the common 16:9-source -> 9:16-output case), or height otherwise."""
    ratio = f"({target_w}/{target_h})"
    crop_w = f"if(gt(a,{ratio}),ih*{ratio},iw)"
    crop_h = f"if(gt(a,{ratio}),ih,iw*{target_h}/{target_w})"
    return f"crop='{crop_w}':'{crop_h}':'(iw-ow)/2':'(ih-oh)/2',scale={target_w}:{target_h}"


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", video_path,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError,
            KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError("PROBE_FAILED", "Unable to read video dimensions") from exc


def get_target_dimensions(aspect_ratio: str) -> tuple[int, int]:
    return _ASPECT_RATIO_DIMENSIONS.get(aspect_ratio, _ASPECT_RATIO_DIMENSIONS["9:16"])


def trim_clip(video_path: str, start: float, end: float, output_path: str) -> None:
    """Trims [start, end] only — no crop/scale. A re-encode (not stream-copy) so the output
    always starts exactly at `start` regardless of keyframe placement; the crop step that
    follows (smart-crop or plain center-crop) re-encodes again anyway."""
    duration = max(0.1, end - start)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-i", video_path, "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    _run(cmd, "CLIP_TRIM_FAILED", "Unable to trim clip")


def crop_and_scale(video_path: str, output_path: str, target_w: int, target_h: int) -> None:
    """Center-crops+scales to an exact target resolution (e.g. true 1080x1920), never
    letterboxed/pillarboxed. No trimming — operates on the whole input."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", _center_crop_scale_filter(target_w, target_h),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd, "CLIP_GENERATION_FAILED", "Unable to generate clip")


def _positioned_crop_scale_filter(target_w: int, target_h: int, position_x_pct: float, position_y_pct: float) -> str:
    """Like _center_crop_scale_filter, but the crop window's CENTER sits at
    (position_x_pct, position_y_pct) of the source (0-100, percentage of width/height) instead
    of the exact center — clamped so the window never runs off any edge."""
    ratio = f"({target_w}/{target_h})"
    crop_w = f"if(gt(a,{ratio}),ih*{ratio},iw)"
    crop_h = f"if(gt(a,{ratio}),ih,iw*{target_h}/{target_w})"
    x_frac = max(0.0, min(100.0, position_x_pct)) / 100
    y_frac = max(0.0, min(100.0, position_y_pct)) / 100
    x = f"max(0,min(iw-ow,iw*{x_frac}-ow/2))"
    y = f"max(0,min(ih-oh,ih*{y_frac}-oh/2))"
    return f"crop='{crop_w}':'{crop_h}':'{x}':'{y}',scale={target_w}:{target_h}"


def crop_and_scale_positioned(
    video_path: str, output_path: str, target_w: int, target_h: int, position_x_pct: float, position_y_pct: float
) -> None:
    """Crops+scales to an exact target resolution at a manually-chosen crop-window position
    instead of auto-tracking a detected face — used to override smart-crop when it picked the
    wrong subject. No trimming — operates on the whole input."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", _positioned_crop_scale_filter(target_w, target_h, position_x_pct, position_y_pct),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd, "CLIP_GENERATION_FAILED", "Unable to generate clip")


def generate_thumbnail(clip_path: str, output_image_path: str) -> None:
    duration = get_video_duration(clip_path)
    midpoint = max(0.0, duration / 2)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{midpoint:.3f}", "-i", clip_path,
        "-vframes", "1", "-q:v", "3",
        output_image_path,
    ]
    _run(cmd, "THUMBNAIL_GENERATION_FAILED", "Unable to generate thumbnail")


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    """Burns an .ass subtitle track into the video via libass. Runs with cwd set to the
    subtitle file's directory and references it by filename only — avoids ffmpeg filtergraph
    escaping headaches (colons/backslashes in absolute paths break the `ass=` filter arg)."""
    ass_file = Path(ass_path)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"ass={ass_file.name}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd, "SUBTITLE_BURN_FAILED", "Unable to burn subtitles into clip", cwd=str(ass_file.parent))


def ensure_parent_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
