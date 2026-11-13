import time

import numpy as np

from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint

_ENERGY_WINDOW_SECONDS = 1.0
_SCENE_SAMPLE_FPS = 1.0  # sample one frame per second — cheap and enough to spot cuts/highlights
_SCENE_DETECTION_MAX_SECONDS = 120.0  # wall-clock budget — a slow/undecodable codec shouldn't hang the pipeline


def compute_audio_energy(wav_path: str) -> list[AudioEnergyPoint]:
    """RMS energy per 1s window, normalized to 0-1. Used as a 'speech/reaction energy' signal."""
    from pydub import AudioSegment

    audio = AudioSegment.from_wav(wav_path).set_channels(1)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    if audio.sample_width == 2:
        samples /= 32768.0
    frame_rate = audio.frame_rate
    window_size = max(1, int(frame_rate * _ENERGY_WINDOW_SECONDS))

    points: list[AudioEnergyPoint] = []
    max_rms = 1e-9
    raw: list[tuple[float, float]] = []
    for start in range(0, len(samples), window_size):
        chunk = samples[start : start + window_size]
        if len(chunk) == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        t = start / frame_rate
        raw.append((t, rms))
        max_rms = max(max_rms, rms)

    for t, rms in raw:
        points.append(AudioEnergyPoint(time=t, energy=min(1.0, rms / max_rms)))
    return points


def detect_scene_changes(video_path: str) -> list[SceneChangePoint]:
    """Cheap histogram-diff scene detection via OpenCV, sampled at ~1 fps."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(round(fps / _SCENE_SAMPLE_FPS)))

    points: list[SceneChangePoint] = []
    prev_hist = None
    frame_idx = 0
    max_diff = 1e-9
    raw: list[tuple[float, float]] = []
    deadline = time.monotonic() + _SCENE_DETECTION_MAX_SECONDS

    while True:
        if time.monotonic() > deadline:
            break
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % frame_interval == 0:
            small = cv2.resize(frame, (160, 90))
            hist = cv2.calcHist([small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            if prev_hist is not None:
                diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
                t = frame_idx / fps
                raw.append((t, diff))
                max_diff = max(max_diff, diff)
            prev_hist = hist
        frame_idx += 1

    cap.release()
    for t, diff in raw:
        points.append(SceneChangePoint(time=t, magnitude=min(1.0, diff / max_diff)))
    return points


def average_in_range(points: list[AudioEnergyPoint] | list[SceneChangePoint], start: float, end: float) -> float:
    values = [p.energy if isinstance(p, AudioEnergyPoint) else p.magnitude for p in points if start <= p.time <= end]
    return sum(values) / len(values) if values else 0.0

def feature_values_in_range(points, start: float, end: float) -> list[float]:
    return [
        p.energy if isinstance(p, AudioEnergyPoint) else p.magnitude
        for p in points
        if start <= p.time <= end
    ]


def feature_stats(points, start: float, end: float) -> dict[str, float]:
    values = feature_values_in_range(points, start, end)

    if not values:
        return {"mean": 0.0, "peak": 0.0, "p90": 0.0}

    return {
        "mean": float(np.mean(values)),
        "peak": float(np.max(values)),
        "p90": float(np.percentile(values, 90)),
    }


def average_in_range(points, start: float, end: float) -> float:
    return feature_stats(points, start, end)["mean"]