"""Audio energy: WAV handling, normalization, window statistics."""
import struct
import wave

import numpy as np
import pytest

from app.services.video import analysis
from app.services.video.analysis import SignalIndex, average_in_range, compute_audio_energy, feature_stats
from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput

RATE = 8000


def _write_wav(path, levels, *, width=2, rate=RATE, channels=1, seconds_each=1.0):
    """A WAV whose consecutive `seconds_each`-second pieces are constant (DC) signals at the given
    fractions of full scale — so each window's RMS is exactly that fraction."""
    full_scale = 2 ** (8 * width - 1) - 1
    chunks = []
    for level in levels:
        n = int(rate * seconds_each)
        value = int(round(level * full_scale))
        chunks.append(np.full(n, value, dtype=np.int64))
    samples = np.concatenate(chunks) if chunks else np.array([], dtype=np.int64)
    if channels == 2:
        samples = np.repeat(samples, 2)

    if width == 1:
        raw = (samples + 128).astype(np.uint8).tobytes()  # 8-bit WAV is unsigned
    elif width == 2:
        raw = samples.astype("<i2").tobytes()
    elif width == 3:
        raw = b"".join(struct.pack("<i", int(v))[:3] for v in samples)
    else:
        raw = samples.astype("<i4").tobytes()

    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)
    return path


def _energies(points):
    return [round(p.energy, 3) for p in points]


def test_normal_audio_is_normalized_against_the_loudest_second_with_accurate_timestamps(tmp_path):
    wav = _write_wav(tmp_path / "a.wav", [0.1, 0.8, 0.4])

    points = compute_audio_energy(str(wav))

    assert [p.time for p in points] == [0.0, 1.0, 2.0]
    assert _energies(points) == [0.125, 1.0, 0.5]  # relative to the loudest second, not absolute loudness
    assert all(0.0 <= p.energy <= 1.0 for p in points)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_every_supported_sample_width_gives_the_same_relative_energy(tmp_path, width):
    wav = _write_wav(tmp_path / f"w{width}.wav", [0.2, 0.8, 0.4], width=width)

    points = compute_audio_energy(str(wav))

    assert len(points) == 3
    assert [round(p.energy, 2) for p in points] == [0.25, 1.0, 0.5]


def test_stereo_is_mixed_down(tmp_path):
    wav = _write_wav(tmp_path / "s.wav", [0.2, 0.6], channels=2)
    assert _energies(compute_audio_energy(str(wav))) == [0.333, 1.0]


def test_an_empty_wav_gives_no_points(tmp_path):
    wav = _write_wav(tmp_path / "empty.wav", [])
    assert compute_audio_energy(str(wav)) == []


def test_unreadable_audio_gives_no_points_instead_of_failing_the_video(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"this is not audio at all" * 10)
    assert compute_audio_energy(str(junk)) == []


def test_a_missing_file_is_still_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        compute_audio_energy(str(tmp_path / "nope.wav"))


def test_silent_audio_is_all_zero_with_no_division_by_zero(tmp_path):
    wav = _write_wav(tmp_path / "silent.wav", [0.0, 0.0, 0.0])

    points = compute_audio_energy(str(wav))

    assert _energies(points) == [0.0, 0.0, 0.0]
    assert not any(np.isnan(p.energy) for p in points)


def test_a_trailing_sliver_is_dropped_but_a_real_partial_window_is_kept(tmp_path):
    sliver = _write_wav(tmp_path / "a.wav", [0.5, 0.5], seconds_each=1.05)  # 2.1 s: 0.1 s tail
    half = _write_wav(tmp_path / "b.wav", [0.5, 0.5, 0.5], seconds_each=0.85)  # 2.55 s: 0.55 s tail

    assert len(compute_audio_energy(str(sliver))) == 2
    assert len(compute_audio_energy(str(half))) == 3


def test_long_audio_is_processed_in_blocks_without_changing_the_result(tmp_path, mocker):
    mocker.patch.object(analysis, "_ENERGY_BLOCK_WINDOWS", 7)  # force many blocks on a short file
    levels = [0.2, 0.9, 0.5] * 10
    wav = _write_wav(tmp_path / "long.wav", levels, rate=800)

    points = compute_audio_energy(str(wav))

    assert len(points) == 30
    assert [round(p.energy, 3) for p in points[:6]] == [0.222, 1.0, 0.556, 0.222, 1.0, 0.556]
    assert points[29].time == 29.0


# --------------------------------------------------------------------------- window statistics

def _points(values, start=0.0):
    return [AudioEnergyPoint(time=start + i, energy=v) for i, v in enumerate(values)]


def test_feature_stats_report_mean_peak_p90_and_count():
    stats = feature_stats(_points([0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 0.1, 0.3, 0.5, 0.7]), 0.0, 10.0)

    assert stats["mean"] == pytest.approx(0.46)
    assert stats["peak"] == 1.0
    assert stats["p90"] == pytest.approx(np.percentile([0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 0.1, 0.3, 0.5, 0.7], 90))
    assert stats["count"] == 10


def test_stats_of_an_empty_window_are_safe_zeros():
    assert feature_stats([], 0.0, 10.0) == {"mean": 0.0, "peak": 0.0, "p90": 0.0, "count": 0.0}
    assert feature_stats(_points([0.5, 0.5]), 50.0, 60.0)["peak"] == 0.0


def test_windows_are_half_open_so_a_boundary_point_belongs_to_the_next_window():
    pts = _points([0.1, 0.2, 0.3, 0.4])  # at t = 0, 1, 2, 3
    assert feature_stats(pts, 0.0, 2.0)["count"] == 2
    assert feature_stats(pts, 2.0, 4.0)["count"] == 2
    assert feature_stats(pts, 1.0, 1.0)["count"] == 0


def test_stats_work_the_same_for_scene_change_points_and_unsorted_input():
    scene = [SceneChangePoint(time=t, magnitude=m) for t, m in [(3.0, 0.9), (1.0, 0.1), (2.0, 0.5)]]
    stats = SignalIndex(scene).stats(0.0, 10.0)
    assert stats["peak"] == 0.9 and stats["mean"] == pytest.approx(0.5) and stats["count"] == 3


def test_average_in_range_is_still_the_mean():
    assert average_in_range(_points([0.2, 0.4, 0.6]), 0.0, 3.0) == pytest.approx(0.4)
    assert average_in_range([], 0.0, 3.0) == 0.0


def test_the_signal_index_is_cached_on_the_analysis():
    video = VideoAnalysisInput(video_duration=10.0, audio_energy=_points([0.5, 0.6]))
    first = analysis.signal_index(video, "audio")
    assert analysis.signal_index(video, "audio") is first
    assert analysis.audio_stats(video, 0.0, 10.0)["count"] == 2
    assert analysis.audio_stats(VideoAnalysisInput(video_duration=10.0), 0.0, 10.0) is None  # no audio at all
