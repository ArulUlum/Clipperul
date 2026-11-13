"""Cheap, relative signals about a video used to rank candidate moments: audio energy per second and
histogram-based scene changes at ~1 fps, plus the window statistics the highlight strategies read.

Both signals are normalized WITHIN the video they come from (0-1): they say "louder / bigger change
than the rest of this video", never how loud or how large anything is in absolute terms. They rank
candidates; they are not evidence that a moment is a highlight."""
import logging
import time
import wave
from dataclasses import dataclass

import numpy as np

from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput

logger = logging.getLogger(__name__)

_ENERGY_WINDOW_SECONDS = 1.0
_ENERGY_BLOCK_WINDOWS = 600  # windows per numpy block (10 min): bounds memory on long videos
_SILENCE_RMS = 1e-6  # relative to full scale: below this the whole track is treated as silent
_MIN_TAIL_FRACTION = 0.25  # a trailing sliver shorter than this share of a window is dropped: too noisy
_INT_DTYPES = {1: np.int8, 2: np.int16, 4: np.int32}

_SCENE_SAMPLE_FPS = 1.0  # sample one frame per second — cheap and enough to spot cuts/highlights
_SCENE_DETECTION_MAX_SECONDS = 120.0  # wall-clock budget — a slow/undecodable codec shouldn't hang the pipeline
# A video whose biggest frame-to-frame change is below this multiple of its typical change has no
# real cuts (a static podcast, a locked-off camera): its noise must not be stretched to look like cuts.
_MIN_CUT_TO_BASELINE = 3.0
_MIN_SAMPLES_FOR_BASELINE = 5
_SCENE_SCALE_PERCENTILE = 99.0
_MIN_SCALE_OF_MAX = 0.05
_MIN_CUT_DIFF = 0.02  # absolute floor on the histogram distance that can count as a cut (measured: noise on a static video peaks near 0.006, a hard cut is about 1.0)


# --------------------------------------------------------------------------- audio energy

def compute_audio_energy(wav_path: str) -> list[AudioEnergyPoint]:
    """RMS energy per 1 s window, normalized by the loudest window of the same video (0-1).

    Handles 8/16/24/32-bit WAVs (24-bit is widened by pydub), mixes stereo down, never divides by
    zero (an all-silent track is all 0), and returns [] for empty or undecodable audio — the pipeline
    then simply has no audio signal instead of failing."""
    from pydub import AudioSegment
    from pydub.exceptions import CouldntDecodeError

    try:
        audio = AudioSegment.from_wav(wav_path)
    except FileNotFoundError:
        raise
    except (CouldntDecodeError, wave.Error, EOFError, ValueError, IndexError) as exc:
        logger.warning("audio_energy_unreadable error_type=%s", type(exc).__name__)
        return []
    if audio.channels != 1:
        audio = audio.set_channels(1)
    if audio.sample_width not in _INT_DTYPES:
        audio = audio.set_sample_width(2)
    if not audio.raw_data or audio.frame_rate <= 0:
        return []

    samples = np.frombuffer(audio.raw_data, dtype=_INT_DTYPES[audio.sample_width])
    full_scale = float(2 ** (8 * audio.sample_width - 1))
    return _energy_points(samples, full_scale, audio.frame_rate)


def _energy_points(samples: np.ndarray, full_scale: float, frame_rate: int) -> list[AudioEnergyPoint]:
    window = max(1, int(round(frame_rate * _ENERGY_WINDOW_SECONDS)))
    n = int(samples.size)
    starts = np.arange(0, n, window)
    counts = np.minimum(window, n - starts)
    mean_square = np.empty(starts.size, dtype=np.float64)

    for b0 in range(0, starts.size, _ENERGY_BLOCK_WINDOWS):
        b1 = min(b0 + _ENERGY_BLOCK_WINDOWS, starts.size)
        lo = int(starts[b0])
        hi = int(min(n, starts[b1 - 1] + window))
        block = samples[lo:hi].astype(np.float32) / full_scale
        sums = np.add.reduceat(block * block, starts[b0:b1] - lo, dtype=np.float64)
        mean_square[b0:b1] = sums / counts[b0:b1]

    rms = np.sqrt(mean_square)
    if starts.size > 1 and counts[-1] < window * _MIN_TAIL_FRACTION:
        starts, rms = starts[:-1], rms[:-1]

    loudest = float(rms.max()) if rms.size else 0.0
    if loudest < _SILENCE_RMS:
        energy = np.zeros_like(rms)
    else:
        energy = np.clip(rms / loudest, 0.0, 1.0)
    return [AudioEnergyPoint(time=float(t) / frame_rate, energy=float(e)) for t, e in zip(starts, energy)]


# --------------------------------------------------------------------------- scene changes

@dataclass
class SceneAnalysis:
    """What scene detection found AND how far it got — an incomplete analysis must never be read as
    'nothing happened after this point'."""

    points: list[SceneChangePoint]
    completed: bool
    timed_out: bool = False
    analyzed_until: float | None = None  # timestamp of the last sampled frame that was analyzed
    error: str | None = None


def analyze_scenes(video_path: str, *, max_seconds: float = _SCENE_DETECTION_MAX_SECONDS) -> SceneAnalysis:
    """Cheap histogram-diff scene detection via OpenCV, sampled at ~1 fps.

    Never raises for an unreadable video or a mid-stream decode error: the result says what was
    analyzed and why it stopped. The capture is always released."""
    import cv2

    cap = None
    raw: list[tuple[float, float]] = []
    analyzed_until: float | None = None
    timed_out = False
    error: str | None = None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return SceneAnalysis(points=[], completed=False, error="video could not be opened")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps != fps or fps <= 0:  # 0, NaN or negative: container didn't say
            fps = 30.0
        frame_interval = max(1, int(round(fps / _SCENE_SAMPLE_FPS)))

        prev_hist = None
        frame_idx = 0
        deadline = time.monotonic() + max_seconds
        while True:
            if time.monotonic() > deadline:
                timed_out = True
                break
            # grab() advances without the colour conversion retrieve() does: the frames between
            # samples are only skipped.
            if not cap.grab():
                break
            if frame_idx % frame_interval == 0:
                ok, frame = cap.retrieve()
                if ok and frame is not None:
                    small = cv2.resize(frame, (160, 90))
                    hist = cv2.calcHist([small], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
                    cv2.normalize(hist, hist)
                    t = frame_idx / fps
                    if prev_hist is not None:
                        raw.append((t, float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR))))
                    prev_hist = hist
                    analyzed_until = t
            frame_idx += 1
    except Exception as exc:  # cv2.error and friends: keep what was analyzed so far
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("scene_analysis_failed error_type=%s analyzed_until=%s", type(exc).__name__, analyzed_until)
    finally:
        if cap is not None:
            cap.release()

    points = _normalize_scene_diffs(raw)
    completed = analyzed_until is not None and not timed_out and error is None
    if timed_out:
        logger.warning("scene_analysis_timed_out analyzed_until=%s budget_seconds=%s", analyzed_until, max_seconds)
    return SceneAnalysis(
        points=points, completed=completed, timed_out=timed_out, analyzed_until=analyzed_until, error=error
    )


def _normalize_scene_diffs(raw: list[tuple[float, float]]) -> list[SceneChangePoint]:
    """Maps histogram distances to 0-1 against the video's OWN statistics: the median change is 0 and the
    99th percentile (and above) is 1. Without the baseline, a static video's tiny noise would be stretched
    to 1 and every sampled frame would look like a cut. A video with no change that stands out stays all 0.

    The scale is the 99th percentile, not the maximum, because the distances are extremely heavy-tailed
    (measured on real videos: median ~0.02, maximum ~1400): scaling by the maximum squashed everything but
    one frame to ~0 and left no usable signal."""
    if not raw:
        return []
    diffs = np.array([d for _, d in raw], dtype=np.float64)
    # A median needs a few samples to mean "typical": with fewer, only the absolute floor applies.
    baseline = float(np.median(diffs)) if diffs.size >= _MIN_SAMPLES_FOR_BASELINE else 0.0
    top = float(diffs.max())
    if top < _MIN_CUT_DIFF or top < baseline * _MIN_CUT_TO_BASELINE:
        magnitudes = np.zeros_like(diffs)
    else:
        # Never below a fraction of the maximum: when real cuts are rarer than 1% of the frames the 99th
        # percentile sits inside ordinary noise, and noise would be scaled up to "cuts".
        scale = max(float(np.percentile(diffs, _SCENE_SCALE_PERCENTILE)), top * _MIN_SCALE_OF_MAX)
        if scale <= baseline:  # too few samples for a percentile to differ from the baseline
            scale = top
        magnitudes = np.clip((diffs - baseline) / (scale - baseline), 0.0, 1.0)
    return [SceneChangePoint(time=t, magnitude=float(m)) for (t, _), m in zip(raw, magnitudes)]


def detect_scene_changes(video_path: str) -> list[SceneChangePoint]:
    """The scene-change points only (kept for callers that don't need the completeness metadata)."""
    return analyze_scenes(video_path).points


# --------------------------------------------------------------------------- window features

def _value(point) -> float:
    return point.energy if isinstance(point, AudioEnergyPoint) else point.magnitude


_ZERO_STATS = {"mean": 0.0, "peak": 0.0, "p90": 0.0, "count": 0.0}


class SignalIndex:
    """Time-sorted values of one signal, so a window's statistics cost O(log n + points in window).
    Windows are half-open, [start, end): a point exactly at `end` belongs to the next window."""

    def __init__(self, points) -> None:
        ordered = sorted(points, key=lambda p: p.time)
        self.times = np.array([p.time for p in ordered], dtype=np.float64)
        self.values = np.array([_value(p) for p in ordered], dtype=np.float64)

    def __len__(self) -> int:
        return int(self.times.size)

    def values_in(self, start: float, end: float) -> np.ndarray:
        lo = int(np.searchsorted(self.times, start, side="left"))
        hi = int(np.searchsorted(self.times, end, side="left"))
        return self.values[lo:hi]

    def stats(self, start: float, end: float) -> dict[str, float]:
        """mean / peak / 90th percentile / count of the values in [start, end); zeros if there are none."""
        values = self.values_in(start, end)
        if values.size == 0:
            return dict(_ZERO_STATS)
        return {
            "mean": float(values.mean()),
            "peak": float(values.max()),
            "p90": float(np.percentile(values, 90)),
            "count": float(values.size),
        }


def feature_values_in_range(points, start: float, end: float) -> list[float]:
    return SignalIndex(points).values_in(start, end).tolist()


def feature_stats(points, start: float, end: float) -> dict[str, float]:
    """mean / peak / p90 / count of audio-energy or scene-change points within [start, end)."""
    return SignalIndex(points).stats(start, end)


def average_in_range(points, start: float, end: float) -> float:
    return feature_stats(points, start, end)["mean"]


def signal_index(analysis: VideoAnalysisInput, signal: str) -> SignalIndex:
    """The cached SignalIndex of analysis.audio_energy ('audio') or analysis.scene_changes ('scene')."""
    cache = analysis._signal_cache
    if signal not in cache:
        cache[signal] = SignalIndex(analysis.audio_energy if signal == "audio" else analysis.scene_changes)
    return cache[signal]


def audio_stats(analysis: VideoAnalysisInput, start: float, end: float) -> dict[str, float] | None:
    """Window statistics of the audio energy, or None when the video has no audio signal at all."""
    if not analysis.audio_energy:
        return None
    return signal_index(analysis, "audio").stats(start, end)


def scene_covers(analysis: VideoAnalysisInput, end: float) -> bool:
    """Whether scene analysis actually looked at everything up to `end`."""
    if analysis.scene_analysis_complete:
        return True
    return analysis.scene_analyzed_until is not None and end <= analysis.scene_analyzed_until


def scene_stats(analysis: VideoAnalysisInput, start: float, end: float) -> dict[str, float] | None:
    """Window statistics of the scene changes, or None when scene analysis didn't reach this window
    (unknown is not the same as 'no change')."""
    if not scene_covers(analysis, end):
        return None
    return signal_index(analysis, "scene").stats(start, end)
