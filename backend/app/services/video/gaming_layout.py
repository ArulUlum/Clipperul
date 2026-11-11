"""The "gaming" clip layout for streams with a facecam: the streamer's face across the top 40% of a
9:16 video and the game across the bottom 60%, both full width, so nothing is left over.

Each part is cropped from the source at its own shape — the face at 1080:768 (a little wider than
4:3, which suits a 16:9 or 4:3 facecam overlay), the game at 1080:1152 (nearly square, which keeps
most of a 16:9 game's middle) — and scaled to fill it.

The facecam is found by face detection on sampled frames. A facecam overlay doesn't move, so the
detections are grouped by position and the largest steady group wins — a face that only shows up
now and then (a character in the game, a cutscene) can't take its place. The result is stored on the
clip, and the editor can override it when the detection picked the wrong spot."""
import logging
import statistics
import time
from dataclasses import dataclass

from app.services.video import pipeline
from app.services.video.exceptions import PipelineError

logger = logging.getLogger(__name__)

CANVAS_W, CANVAS_H = 1080, 1920
FACE_H = 768  # 40% of the canvas
GAME_H = CANVAS_H - FACE_H  # 60%
FACE_ASPECT = CANVAS_W / FACE_H
GAME_ASPECT = CANVAS_W / GAME_H

_SAMPLE_FPS = 1.0
_DETECTION_MAX_SECONDS = 60.0
_MIN_FACE_SIZE = 32
_CLUSTER_RADIUS = 0.08  # a detection within this much (fraction of the frame) of a group joins it
_MIN_FRAME_SHARE = 0.25  # the facecam has to be seen in at least this share of the sampled frames
_CROP_PER_FACE = 2.2  # crop height per face height: head and shoulders, not just the face
_FACE_ABOVE_CENTER = 0.2  # of the crop's height: the face sits in the upper third, like a portrait — the face
# is usually near the top of a facecam, so a crop centered on it would reach past the cam into the game
MIN_SIZE_PCT, MAX_SIZE_PCT = 10.0, 100.0


@dataclass(frozen=True)
class Facecam:
    """The face crop: its center (percent of the source width / height) and its height (percent of
    the source height; its width follows from the face area's shape)."""

    x: float
    y: float
    size: float


def _cluster(detections: list[tuple[int, float, float, float]]) -> list[list[tuple[int, float, float, float]]]:
    groups: list[list[tuple[int, float, float, float]]] = []
    for det in detections:
        _, cx, cy, _ = det
        for group in groups:
            gx = statistics.median(d[1] for d in group)
            gy = statistics.median(d[2] for d in group)
            if abs(cx - gx) <= _CLUSTER_RADIUS and abs(cy - gy) <= _CLUSTER_RADIUS:
                group.append(det)
                break
        else:
            groups.append([det])
    return groups


def facecam_from_detections(detections: list[tuple[int, float, float, float]], sampled_frames: int) -> Facecam | None:
    """detections: (frame index, face center x, face center y, face height) — fractions of the frame.
    Picks the group seen in the most frames, if it's steady enough to be a facecam."""
    if not detections or sampled_frames <= 0:
        return None
    best = max(_cluster(detections), key=lambda g: len({d[0] for d in g}))
    if len({d[0] for d in best}) < max(2, _MIN_FRAME_SHARE * sampled_frames):
        return None
    cx = float(statistics.median(d[1] for d in best))
    cy = float(statistics.median(d[2] for d in best))
    face_h = float(statistics.median(d[3] for d in best))
    height = min(1.0, max(MIN_SIZE_PCT / 100, face_h * _CROP_PER_FACE))
    return Facecam(x=round(cx * 100, 2), y=round(min(1.0, cy + height * _FACE_ABOVE_CENTER) * 100, 2),
                   size=round(height * 100, 2))


def detect_facecam(video_path: str) -> Facecam | None:
    import cv2

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    interval = max(1, int(round(fps / _SAMPLE_FPS)))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    detections: list[tuple[int, float, float, float]] = []
    sampled = 0
    frame_idx = 0
    deadline = time.monotonic() + _DETECTION_MAX_SECONDS
    try:
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % interval == 0:
                sampled += 1
                h, w = frame.shape[:2]
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.15, minNeighbors=6, minSize=(_MIN_FACE_SIZE, _MIN_FACE_SIZE)
                )
                for x, y, fw, fh in faces:
                    detections.append((frame_idx, (x + fw / 2) / w, (y + fh / 2) / h, fh / h))
            frame_idx += 1
    finally:
        cap.release()
    return facecam_from_detections(detections, sampled)


Rect = tuple[int, int, int, int]  # width, height, x, y


def _rect(src_w: int, src_h: int, center_x_pct: float, center_y_pct: float, height: float, aspect: float) -> Rect:
    """A window of the given shape, `height` tall (less if the frame is too narrow for it), centered
    at the given point and kept inside the frame. Even sizes, for x264."""
    h = min(height, src_h)
    w = h * aspect
    if w > src_w:
        w, h = src_w, src_w / aspect
    w, h = max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)
    x = round(src_w * center_x_pct / 100 - w / 2)
    y = round(src_h * center_y_pct / 100 - h / 2)
    return w, h, max(0, min(src_w - w, x)), max(0, min(src_h - h, y))


def crop_boxes(src_w: int, src_h: int, facecam: Facecam, game_x_pct: float) -> tuple[Rect, Rect]:
    """The face crop and the game crop (as tall as the frame, slid horizontally to game_x_pct)."""
    face = _rect(src_w, src_h, facecam.x, facecam.y, src_h * facecam.size / 100, FACE_ASPECT)
    game = _rect(src_w, src_h, game_x_pct, 50.0, src_h, GAME_ASPECT)
    return face, game


def compose(trimmed_path: str, output_path: str, facecam: Facecam, game_x_pct: float) -> None:
    src_w, src_h = pipeline.get_video_dimensions(trimmed_path)
    (fw, fh, fx, fy), (gw, gh, gx, gy) = crop_boxes(src_w, src_h, facecam, game_x_pct)
    graph = (
        f"[0:v]split=2[f][g];"
        f"[f]crop={fw}:{fh}:{fx}:{fy},scale={CANVAS_W}:{FACE_H},setsar=1[face];"
        f"[g]crop={gw}:{gh}:{gx}:{gy},scale={CANVAS_W}:{GAME_H},setsar=1[game];"
        f"[face][game]vstack[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", trimmed_path,
        "-filter_complex", graph, "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    pipeline._run(cmd, "LAYOUT_FAILED", "Unable to compose the gaming layout")


def render(trimmed_path: str, output_path: str, facecam: Facecam | None, game_x_pct: float | None) -> Facecam | None:
    """Composes the layout from the trimmed (uncropped) clip, detecting the facecam unless one is
    given. Returns the facecam used, or None when no face was found — the caller then renders the
    clip some other way."""
    if facecam is None:
        try:
            facecam = detect_facecam(trimmed_path)
        except Exception:
            logger.exception("Facecam detection failed for %s", trimmed_path)
            facecam = None
        if facecam is None:
            logger.info("No steady face found in %s; not using the gaming layout", trimmed_path)
            return None
    try:
        compose(trimmed_path, output_path, facecam, 50.0 if game_x_pct is None else game_x_pct)
    except PipelineError:
        logger.exception("Gaming layout failed for %s; rendering the clip another way", trimmed_path)
        return None
    return facecam
