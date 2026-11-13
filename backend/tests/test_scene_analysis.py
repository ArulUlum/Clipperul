"""Scene detection: reliability, completeness metadata, and a baseline-relative magnitude."""
import itertools

import cv2
import numpy as np
import pytest

from app.services.video import analysis
from app.services.video.analysis import analyze_scenes, detect_scene_changes
from app.services.video.analysis_types import VideoAnalysisInput


def _write_video(path, colors, *, fps=10, seconds_each=1, size=(160, 90)):
    """An .avi of constant-colour scenes, `seconds_each` seconds apiece."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened()
    for color in colors:
        frame = np.full((size[1], size[0], 3), color, dtype=np.uint8)
        for _ in range(fps * seconds_each):
            writer.write(frame)
    writer.release()
    return path


class _FakeCapture:
    """cv2.VideoCapture stand-in: hands out `frames` and records whether it was released."""

    instances: list = []

    def __init__(self, path, frames=None, fps=1.0, opened=True, fail_after=None):
        self.frames = frames or []
        self.fps = fps
        self.opened = opened
        self.fail_after = fail_after
        self.released = False
        self._i = 0
        _FakeCapture.instances.append(self)

    def isOpened(self):
        return self.opened

    def get(self, prop):
        return self.fps

    def grab(self):
        if self.fail_after is not None and self._i >= self.fail_after:
            raise cv2.error("simulated decode failure")
        if self._i >= len(self.frames):
            return False
        self._i += 1
        return True

    def retrieve(self):
        return True, self.frames[self._i - 1]

    def release(self):
        self.released = True


def _frames(*values):
    return [np.full((90, 160, 3), v, dtype=np.uint8) for v in values]


def _patch_capture(mocker, **kwargs):
    _FakeCapture.instances = []
    mocker.patch("cv2.VideoCapture", side_effect=lambda path: _FakeCapture(path, **kwargs))


# --------------------------------------------------------------------------- real videos

def test_a_video_with_one_cut_reports_that_cut_and_that_it_completed(tmp_path):
    video = _write_video(tmp_path / "cut.avi", [(20, 20, 20), (230, 230, 230)], seconds_each=3)  # cut at 3 s

    result = analyze_scenes(str(video))

    assert result.completed and not result.timed_out and result.error is None
    assert result.analyzed_until == pytest.approx(5.0)
    by_time = {round(p.time): p.magnitude for p in result.points}
    assert by_time[3] == 1.0  # the cut is the biggest change of the video
    assert all(m == 0.0 for t, m in by_time.items() if t != 3)  # ordinary frames are not "scene changes"
    assert all(0.0 <= p.magnitude <= 1.0 for p in result.points)


def test_a_static_video_has_no_scene_changes_at_all(tmp_path):
    video = _write_video(tmp_path / "static.avi", [(90, 120, 150)], seconds_each=6)

    result = analyze_scenes(str(video))

    assert result.completed and result.points  # it WAS analyzed...
    assert all(p.magnitude == 0.0 for p in result.points)  # ...and nothing in it stands out


def test_detect_scene_changes_still_returns_the_plain_point_list(tmp_path):
    video = _write_video(tmp_path / "cut.avi", [(20, 20, 20), (230, 230, 230)], seconds_each=2)
    points = detect_scene_changes(str(video))
    assert points and max(p.magnitude for p in points) == 1.0


def test_an_unopenable_video_is_reported_not_raised(tmp_path):
    for bad in (tmp_path / "missing.avi", tmp_path / "garbage.avi"):
        if bad.name == "garbage.avi":
            bad.write_bytes(b"not a video" * 50)
        result = analyze_scenes(str(bad))
        assert result.points == [] and not result.completed
        assert result.analyzed_until is None and result.error


def test_a_video_with_no_frames_is_not_reported_as_analyzed(tmp_path):
    empty = tmp_path / "empty.avi"
    writer = cv2.VideoWriter(str(empty), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 90))
    writer.release()

    result = analyze_scenes(str(empty))

    assert result.points == [] and not result.completed


# --------------------------------------------------------------------------- completeness metadata

def test_running_out_of_time_is_recorded_with_the_last_analyzed_timestamp(mocker):
    _patch_capture(mocker, frames=_frames(10, 10, 200, 200, 10), fps=1.0)
    # clock: deadline set at 0, two frames fit in the budget, then it is over
    mocker.patch("app.services.video.analysis.time.monotonic", side_effect=itertools.chain([0, 0, 0], itertools.repeat(100)))

    result = analyze_scenes("x.mp4", max_seconds=5)

    assert result.timed_out and not result.completed  # an incomplete analysis is never reported as complete
    assert result.analyzed_until == 1.0
    assert len(result.points) == 1  # only the diff between the two frames that were looked at


def test_a_decode_error_midway_keeps_what_was_found_and_is_marked_incomplete(mocker):
    _patch_capture(mocker, frames=_frames(10, 10, 200, 200, 10, 10), fps=1.0, fail_after=3)

    result = analyze_scenes("x.mp4")

    assert not result.completed and result.error and "simulated decode failure" in result.error
    assert result.analyzed_until == 2.0
    assert len(result.points) == 2


def test_the_capture_is_released_on_success_on_failure_and_when_it_cannot_open(mocker):
    _patch_capture(mocker, frames=_frames(10, 10, 200), fps=1.0)
    analyze_scenes("ok.mp4")
    assert _FakeCapture.instances[-1].released

    _patch_capture(mocker, frames=_frames(10, 10, 200), fps=1.0, fail_after=1)
    analyze_scenes("boom.mp4")
    assert _FakeCapture.instances[-1].released

    _patch_capture(mocker, opened=False)
    analyze_scenes("closed.mp4")
    assert _FakeCapture.instances[-1].released


def test_an_unusable_frame_rate_falls_back_and_timestamps_stay_correct(mocker):
    _patch_capture(mocker, frames=_frames(*([10] * 30), *([200] * 30)), fps=0)  # container gave no fps -> 30

    result = analyze_scenes("x.mp4")

    assert result.completed
    # 60 frames at the assumed 30 fps is 2 s: frames 0 and 30 are the two samples, so one change, at t = 1 s
    assert [p.time for p in result.points] == [1.0]
    assert result.points[0].magnitude == 1.0  # frame 30 is the first of the bright scene


def test_sampled_frames_are_a_second_apart_for_other_frame_rates(mocker):
    _patch_capture(mocker, frames=_frames(*([10] * 25), *([200] * 25)), fps=25.0)
    result = analyze_scenes("x.mp4")
    assert [p.time for p in result.points] == [1.0]
    assert result.analyzed_until == 1.0


# --------------------------------------------------------------------------- normalization

def test_normalization_is_relative_to_the_videos_own_baseline():
    # ordinary motion ~0.004, one real cut 0.4: the cut is 1, ordinary frames 0, nothing is stretched to 1
    raw = [(1.0, 0.004), (2.0, 0.005), (3.0, 0.4), (4.0, 0.004), (5.0, 0.003)]
    magnitudes = [p.magnitude for p in analysis._normalize_scene_diffs(raw)]
    assert magnitudes[2] == 1.0
    assert max(magnitudes[0], magnitudes[1], magnitudes[3], magnitudes[4]) < 0.05


def test_one_extreme_outlier_does_not_squash_every_other_cut_to_zero():
    """Measured on real videos: the distances are heavy-tailed (median ~0.02, maximum ~1400). Scaling by
    the maximum left one frame at 1 and everything else near 0."""
    raw = [(float(t), 0.02 + 0.001 * (t % 5)) for t in range(1, 1000)]  # ordinary frames
    raw += [(1000.0 + i, 60.0 + 10 * i) for i in range(9)]  # nine real cuts, 60-140
    raw.append((1010.0, 1400.0))  # one extreme outlier
    by_time = {p.time: p.magnitude for p in analysis._normalize_scene_diffs(raw)}

    assert by_time[1010.0] == 1.0
    assert all(by_time[1000.0 + i] > 0.5 for i in range(9))  # the real cuts keep their weight
    assert max(by_time[float(t)] for t in range(1, 1000)) < 0.01  # ordinary frames stay at ~0
    assert all(0.0 <= m <= 1.0 for m in by_time.values())


def test_noise_that_never_stands_out_is_not_stretched_into_cuts():
    raw = [(float(t), 0.004 + 0.002 * (t % 3)) for t in range(1, 20)]  # static camera with sensor noise
    assert {p.magnitude for p in analysis._normalize_scene_diffs(raw)} == {0.0}


def test_normalization_of_nothing_is_nothing():
    assert analysis._normalize_scene_diffs([]) == []


# --------------------------------------------------------------------------- how strategies read it

def _video(**kw):
    return VideoAnalysisInput(video_duration=60.0, **kw)


def test_scene_stats_are_unknown_not_zero_beyond_where_analysis_stopped():
    from app.services.video.analysis_types import SceneChangePoint

    video = _video(
        scene_changes=[SceneChangePoint(time=5.0, magnitude=0.9)],
        scene_analysis_complete=False,
        scene_analyzed_until=20.0,
    )
    assert analysis.scene_stats(video, 0.0, 20.0)["peak"] == 0.9
    assert analysis.scene_stats(video, 30.0, 60.0) is None  # past the analyzed point: unknown
    assert analysis.scene_stats(video, 10.0, 30.0) is None  # straddles it: not fully analyzed

    complete = _video(scene_changes=[SceneChangePoint(time=5.0, magnitude=0.9)])  # the default: complete
    assert analysis.scene_stats(complete, 30.0, 60.0)["peak"] == 0.0  # analyzed, nothing happened


def test_a_failed_analysis_with_no_points_is_unknown_everywhere():
    failed = _video(scene_analysis_complete=False, scene_analyzed_until=None)
    assert analysis.scene_stats(failed, 0.0, 10.0) is None
