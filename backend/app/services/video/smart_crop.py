"""Auto-tracking crop: follows the most prominent detected face horizontally so a moving
speaker/subject stays in frame, instead of a fixed center-crop window. Falls back to a plain
center-crop whenever no face is found anywhere in the clip (gameplay footage, wide shots)."""

import logging
import subprocess
import time

import numpy as np

from app.services.video.exceptions import PipelineError

logger = logging.getLogger(__name__)

_FACE_SAMPLE_FPS = 2.5
_FACE_DETECTION_MAX_SECONDS = 180.0  # wall-clock budget, mirrors detect_scene_changes
_MIN_FACE_SIZE = 40
_SMOOTHING_WINDOW = 5  # samples; avoids jittery per-detection crop jumps


def detect_face_centers(video_path: str) -> list[tuple[float, float]]:
    """Samples frames at ~2.5fps, runs Haar-cascade face detection, and returns
    (time, center_x_fraction) for frames where a face was found. Gaps (no detection) are
    simply omitted — filling/smoothing is handled separately so this stays a thin, swappable
    detection step (e.g. to a DNN detector later)."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps / _FACE_SAMPLE_FPS)))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    samples: list[tuple[float, float]] = []
    frame_idx = 0
    deadline = time.monotonic() + _FACE_DETECTION_MAX_SECONDS

    while True:
        if time.monotonic() > deadline:
            break
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % frame_interval == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.2, minNeighbors=5, minSize=(_MIN_FACE_SIZE, _MIN_FACE_SIZE)
            )
            if len(faces) > 0:
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                frame_w = frame.shape[1]
                samples.append((frame_idx / fps, (x + w / 2) / frame_w))
        frame_idx += 1

    cap.release()
    return samples


def fill_and_smooth_centers(
    samples: list[tuple[float, float]], duration: float, smoothing_window: int = _SMOOTHING_WINDOW
) -> list[tuple[float, float]] | None:
    """Anchors the detected-center sequence at t=0 and t=duration (so interpolation never
    extrapolates), then applies a moving average to avoid jittery per-detection crop jumps.
    Returns None when there were no detections at all — caller should fall back to a plain
    center-crop."""
    if not samples:
        return None

    times = [t for t, _ in samples]
    xs = [x for _, x in samples]

    if times[0] > 0.0:
        times = [0.0] + times
        xs = [xs[0]] + xs
    if times[-1] < duration:
        times = times + [duration]
        xs = xs + [xs[-1]]

    xs_arr = np.array(xs, dtype=np.float64)
    if len(xs_arr) >= smoothing_window:
        kernel = np.ones(smoothing_window) / smoothing_window
        xs_arr = np.convolve(xs_arr, kernel, mode="same")

    return list(zip(times, xs_arr.tolist()))


def interpolate_center(keyframes: list[tuple[float, float]], t: float) -> float:
    """Linear interpolation of crop center (0-1 fraction of width) at time t."""
    times = [kt for kt, _ in keyframes]
    xs = [kx for _, kx in keyframes]
    return float(np.interp(t, times, xs))


def compute_crop_box(src_w: int, src_h: int, target_w: int, target_h: int) -> tuple[int, int]:
    """Same aspect-ratio logic as the ffmpeg center-crop filter, but returns raw pixel crop
    dimensions (crop_w, crop_h) for per-frame OpenCV cropping."""
    ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio > ratio:
        crop_h = src_h
        crop_w = int(round(crop_h * ratio))
    else:
        crop_w = src_w
        crop_h = int(round(crop_w / ratio))
    return crop_w, crop_h


def crop_x_offset(center_fraction: float, src_w: int, crop_w: int) -> int:
    """Pixel x-offset for a crop_w-wide window centered at center_fraction of src_w, clamped
    so the window never runs off either edge."""
    center_px = center_fraction * src_w
    x0 = int(round(center_px - crop_w / 2))
    return max(0, min(src_w - crop_w, x0))


def apply_smart_crop(
    video_path: str, output_path: str, target_w: int, target_h: int, keyframes: list[tuple[float, float]]
) -> None:
    """Re-encodes video_path into a target_w x target_h video whose horizontal crop window
    tracks `keyframes` (time -> center-x fraction) instead of staying fixed at center. Streams
    cropped/resized frames into ffmpeg over stdin so the same process also muxes the original
    audio track back in, avoiding a second re-encode pass."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise PipelineError("SMART_CROP_FAILED", "Unable to open trimmed clip for smart crop")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    crop_w, crop_h = compute_crop_box(src_w, src_h, target_w, target_h)
    y0 = max(0, (src_h - crop_h) // 2)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{target_w}x{target_h}", "-r", f"{fps:.4f}",
        "-i", "pipe:0",
        "-i", video_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = frame_idx / fps
            center = interpolate_center(keyframes, t)
            x0 = crop_x_offset(center, src_w, crop_w)
            cropped = frame[y0 : y0 + crop_h, x0 : x0 + crop_w]
            resized = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
            proc.stdin.write(resized.tobytes())
            frame_idx += 1
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        # Already closed manually above — if left set, communicate() below tries to flush/
        # close it again and raises ValueError: flush of closed file.
        proc.stdin = None

    try:
        _, stderr = proc.communicate(timeout=1800)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise PipelineError("SMART_CROP_FAILED", "Smart crop encode timed out") from exc

    if proc.returncode != 0:
        raise PipelineError(
            "SMART_CROP_FAILED", f"Unable to apply smart crop: {stderr.decode(errors='replace')[-2000:]}"
        )


def generate_smart_cropped_clip(trimmed_path: str, output_path: str, target_w: int, target_h: int) -> bool:
    """Crops+scales an already-trimmed clip to target_w x target_h, tracking a detected face
    horizontally when one is found anywhere in the clip; falls back to a plain center-crop
    otherwise (or if the tracking pass itself fails — this is a nice-to-have enhancement over
    the required center-crop baseline, so it must never be the reason clip generation fails).
    Returns True if smart tracking was actually applied, False if it fell back to center-crop."""
    from app.services.video import pipeline

    keyframes = None
    try:
        duration = pipeline.get_video_duration(trimmed_path)
        samples = detect_face_centers(trimmed_path)
        keyframes = fill_and_smooth_centers(samples, duration)
    except Exception:
        logger.exception("Face detection failed for %s; falling back to center-crop", trimmed_path)

    if not keyframes:
        pipeline.crop_and_scale(trimmed_path, output_path, target_w, target_h)
        return False

    try:
        apply_smart_crop(trimmed_path, output_path, target_w, target_h, keyframes)
        return True
    except Exception:
        # Broad on purpose (not just PipelineError) — any failure in this cv2/ffmpeg pipe
        # step (e.g. a subprocess I/O edge case) must fall back rather than fail the clip.
        logger.exception("Smart crop encode failed for %s; falling back to center-crop", trimmed_path)
        pipeline.crop_and_scale(trimmed_path, output_path, target_w, target_h)
        return False
