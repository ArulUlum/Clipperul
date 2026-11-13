"""Final clip selection: range validation against the video, duration limits, overlap, duplicates, diversity."""
import math

import pytest

from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import HighlightCandidate
from app.services.ai.highlight_service import _apply_padding, select_highlights
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import VideoAnalysisInput

# conftest/config defaults: min clip 15 s, max 180 s, padding 3 s, min score 40, 8 clips, penalty 8 per
# nearby clip within 90 s.


def _cand(start, end, score=80.0, text="A moment."):
    return HighlightCandidate(
        start=start, end=end, text=text, score=score, reason="Because it matters.",
        category=VideoCategory.PODCAST, confidence=0.8,
    )


def _video(duration=600.0, segments=None):
    return VideoAnalysisInput(video_duration=duration, transcript_segments=segments or [])


def _bounds(selected):
    return [(round(h.start, 2), round(h.end, 2)) for h in selected]


def _valid(selected, duration):
    return all(0.0 <= h.start < h.end <= duration and math.isfinite(h.start + h.end) for h in selected)


# --------------------------------------------------------------------------- range validation

def test_a_negative_start_is_clamped_to_zero():
    selected = select_highlights([_cand(-12.0, 20.0)], _video())
    assert len(selected) == 1 and selected[0].start == 0.0
    assert selected[0].raw_start == -12.0  # the original is kept for reference


def test_an_end_beyond_the_video_is_clamped_to_its_duration():
    selected = select_highlights([_cand(580.0, 999.0)], _video(duration=600.0))
    assert len(selected) == 1 and selected[0].end == 600.0 and _valid(selected, 600.0)


def test_a_candidate_entirely_beyond_the_video_is_dropped():
    assert select_highlights([_cand(700.0, 730.0)], _video(duration=600.0)) == []


@pytest.mark.parametrize(
    "start,end", [(30.0, 30.0), (40.0, 20.0), (float("nan"), 20.0), (10.0, float("inf")), (float("-inf"), 20.0)]
)
def test_empty_reversed_and_non_finite_ranges_are_dropped(start, end):
    assert select_highlights([_cand(start, end)], _video()) == []


def test_a_non_finite_score_is_dropped():
    assert select_highlights([_cand(10, 30, score=float("nan"))], _video()) == []


def test_below_the_minimum_score_is_dropped():
    assert select_highlights([_cand(10, 30, score=39.9), _cand(100, 120, score=40.0)], _video())[0].score == 40.0


# --------------------------------------------------------------------------- duration limits

def test_a_too_short_candidate_is_stretched_to_the_minimum_clip_duration():
    selected = select_highlights([_cand(100.0, 102.0)], _video())
    length = selected[0].end - selected[0].start
    assert length == pytest.approx(15.0)  # min clip duration
    assert selected[0].start < 100.0 < 102.0 < selected[0].end  # grown around the moment, not sideways


def test_a_too_short_candidate_at_the_start_of_the_video_grows_the_other_way():
    selected = select_highlights([_cand(0.0, 2.0)], _video())
    assert _bounds(selected) == [(0.0, 15.0)]


def test_a_too_short_candidate_at_the_end_of_the_video_grows_backwards():
    selected = select_highlights([_cand(598.0, 600.0)], _video(duration=600.0))
    assert _bounds(selected) == [(585.0, 600.0)]


def test_a_too_long_candidate_is_capped_at_the_maximum():
    selected = select_highlights([_cand(100.0, 500.0)], _video())
    assert selected[0].end - selected[0].start == pytest.approx(180.0)
    assert selected[0].start >= 0 and selected[0].end <= 600.0


def test_a_video_shorter_than_the_minimum_clip_yields_a_clip_as_long_as_the_video():
    selected = select_highlights([_cand(1.0, 9.0)], _video(duration=10.0))
    assert _bounds(selected) == [(0.0, 10.0)]  # not dropped, and never longer than the video


def test_padding_helper_is_safe_for_unknown_duration_and_never_goes_negative():
    start, end = _apply_padding(1.0, 20.0, 0.0)  # duration unknown (0): the end must not collapse to 0
    assert start == 0.0 and end == 23.0
    start, end = _apply_padding(0.0, 5.0, 100.0, min_duration=15.0)
    assert (start, end) == (0.0, 15.0)


def test_segment_boundaries_still_snap_the_range_outward():
    segments = [TranscriptSegmentResult(start=95.0, end=118.0, text="One long sentence.")]
    selected = select_highlights([_cand(100.0, 110.0)], _video(segments=segments))
    assert selected[0].start <= 95.0 and selected[0].end >= 118.0  # the whole sentence, plus padding


# --------------------------------------------------------------------------- overlap and duplicates

def test_overlapping_candidates_keep_only_the_better_one():
    selected = select_highlights([_cand(100, 130, score=70), _cand(110, 140, score=90)], _video())
    assert len(selected) == 1 and selected[0].score == 90


def test_identical_candidates_are_one_clip():
    selected = select_highlights([_cand(100, 130, score=80), _cand(100, 130, score=80), _cand(100, 130, score=75)], _video())
    assert len(selected) == 1


def test_a_candidate_inside_a_better_one_is_a_duplicate():
    selected = select_highlights([_cand(100, 160, score=85), _cand(120, 140, score=88)], _video())
    assert len(selected) == 1 and selected[0].score == 88


def test_clips_that_only_touch_through_padding_are_both_kept():
    selected = select_highlights([_cand(100, 120, score=85), _cand(127, 147, score=80)], _video())
    assert len(selected) == 2  # padded ranges [97,123] and [124,150] do not overlap


def test_no_two_selected_clips_overlap():
    candidates = [_cand(t, t + 20, score=90 - t / 10) for t in range(0, 500, 12)]
    selected = select_highlights(candidates, _video())
    ordered = sorted(selected, key=lambda h: h.start)
    assert all(b.start >= a.end - 1e-6 for a, b in zip(ordered, ordered[1:]))


# --------------------------------------------------------------------------- temporal diversity and ordering

def test_a_strong_cluster_does_not_crowd_out_a_good_moment_elsewhere(mocker):
    mocker.patch("app.services.ai.highlight_service.settings.MAX_CLIPS_PER_VIDEO", 3)
    cluster = [_cand(100, 115, 85), _cand(125, 140, 84), _cand(150, 165, 83), _cand(175, 190, 82)]
    elsewhere = _cand(500, 515, 70)

    selected = select_highlights(cluster + [elsewhere], _video(duration=1000.0))

    starts = {round(h.raw_start) for h in selected}
    assert 500 in starts  # the 70-point moment far away beats the third and fourth cluster members
    assert {100, 125} <= starts  # while the best of the cluster are still in


def test_with_the_penalty_off_the_selection_is_plain_score_order(mocker):
    mocker.patch("app.services.ai.highlight_service.settings.MAX_CLIPS_PER_VIDEO", 3)
    mocker.patch("app.services.ai.highlight_service.settings.HIGHLIGHT_DIVERSITY_PENALTY", 0.0)
    cluster = [_cand(100, 115, 85), _cand(125, 140, 84), _cand(150, 165, 83), _cand(175, 190, 82)]

    selected = select_highlights(cluster + [_cand(500, 515, 70)], _video(duration=1000.0))

    assert {round(h.raw_start) for h in selected} == {100, 125, 150}


def test_diversity_never_lets_a_low_score_in_and_the_output_stays_in_score_order(mocker):
    mocker.patch("app.services.ai.highlight_service.settings.MAX_CLIPS_PER_VIDEO", 4)
    candidates = [_cand(100, 115, 90), _cand(125, 140, 85), _cand(400, 415, 60), _cand(700, 715, 30)]  # 30 < min score

    selected = select_highlights(candidates, _video(duration=1000.0))

    assert [h.score for h in selected] == [90, 85, 60]  # the 30-point one is out whatever the spread
    assert [h.score for h in selected] == sorted((h.score for h in selected), reverse=True)


def test_the_number_of_clips_is_capped(mocker):
    mocker.patch("app.services.ai.highlight_service.settings.MAX_CLIPS_PER_VIDEO", 5)
    candidates = [_cand(t, t + 20, score=90) for t in range(0, 1200, 60)]
    assert len(select_highlights(candidates, _video(duration=1300.0))) == 5


def test_an_empty_candidate_list_selects_nothing():
    assert select_highlights([], _video()) == []


def test_selected_highlights_keep_every_field_downstream_code_reads():
    c = _cand(100, 130)
    c.hook_score, c.flow_score, c.value_score, c.trend_score = 80.0, 70.0, 60.0, 50.0
    [h] = select_highlights([c], _video())
    assert (h.raw_start, h.raw_end, h.text, h.score, h.reason, h.category, h.confidence) == (
        100, 130, "A moment.", 80.0, "Because it matters.", VideoCategory.PODCAST, 0.8,
    )
    assert (h.hook_score, h.flow_score, h.value_score, h.trend_score) == (80.0, 70.0, 60.0, 50.0)
    assert h.start < h.raw_start and h.end > h.raw_end  # padded
