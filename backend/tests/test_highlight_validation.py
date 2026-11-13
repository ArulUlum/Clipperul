"""The LLM scoring contract: answers are tied to candidate_ids, validated, clamped to their window, and
never allowed to invent timestamps. Plus the heuristic fallback."""
import logging
import math

import pytest

from app.services.ai.categories.gaming import GamingCategoryStrategy
from app.services.ai.categories.podcast import PodcastCategoryStrategy
from app.services.ai.categories.strategy import FALLBACK_CONFIDENCE
from app.services.ai.llm import LLMProvider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput

STRATEGY = GamingCategoryStrategy()
SENTENCE = "Nobody expected him to survive that, and then he lands the final shot at one health."


class _LLM(LLMProvider):
    """Returns (or raises) whatever the test hands it and remembers the prompts."""

    def __init__(self, response=None, error: Exception | None = None):
        self.response, self.error = response, error
        self.prompts: list[tuple[str, str]] = []

    def complete_json(self, system_prompt, user_prompt):
        self.prompts.append((system_prompt, user_prompt))
        if self.error:
            raise self.error
        return self.response


def _video(duration=60.0, language="en"):
    # speech throughout, so windows c0 [0,30], c15 [15,45], c30 [30,60] all exist
    segments = [TranscriptSegmentResult(start=float(t), end=float(t) + 9.0, text=SENTENCE) for t in range(0, int(duration), 10)]
    return VideoAnalysisInput(
        video_duration=duration, transcript_segments=segments, language=language,
        audio_energy=[AudioEnergyPoint(time=t, energy=0.6) for t in range(int(duration))],
        scene_changes=[SceneChangePoint(time=t, magnitude=0.3) for t in range(int(duration))],
    )


def _item(candidate_id="c0", **overrides):
    item = {
        "candidate_id": candidate_id, "text": "He lands the last shot.", "score": 80,
        "reason": "He survives at one health and lands the final shot.", "confidence": 0.8,
    }
    item.update(overrides)
    return item


def _run(response, video=None, error=None, strategy=STRATEGY):
    llm = _LLM(response, error)
    return strategy.find_highlights(video or _video(), llm), llm


def _fallback_only(highlights):
    return highlights and all(h.confidence == FALLBACK_CONFIDENCE for h in highlights)


# --------------------------------------------------------------------------- the happy path and the prompt

def test_a_valid_highlight_is_tied_to_its_candidate_and_takes_the_windows_bounds_when_no_times_are_given():
    highlights, _ = _run({"highlights": [_item("c15")]})
    assert len(highlights) == 1
    assert (highlights[0].start, highlights[0].end) == (15.0, 45.0)
    assert highlights[0].score == 80 and highlights[0].confidence == 0.8


def test_optional_timestamps_inside_the_window_are_used():
    highlights, _ = _run({"highlights": [_item("c0", start=8.0, end=22.5)]})
    assert (highlights[0].start, highlights[0].end) == (8.0, 22.5)


def test_the_prompt_makes_candidates_the_only_source_and_explains_the_id_contract():
    _, llm = _run({"highlights": []})
    system, user = llm.prompts[0]
    assert "ONLY choose moments from these candidates" in user
    assert "candidate_id" in user and "Never invent a candidate_id" in user
    assert '"candidate_id": string' in user
    assert "clamped" in user  # the timestamp policy is told to the model
    assert "empty list" in user  # "nothing worth clipping" is an allowed answer
    assert "'c0'" in user and "'c15'" in user  # the ids it must answer with are in the payload


def test_candidates_without_a_transcript_are_flagged_and_the_model_is_told_not_to_invent_what_happened():
    silent = VideoAnalysisInput(
        video_duration=25.0, audio_energy=[AudioEnergyPoint(time=t, energy=0.9) for t in range(25)],
    )
    _, llm = _run({"highlights": []}, video=silent)
    user = llm.prompts[0][1]
    assert "'has_transcript': False" in user and "'transcript': ''" in user
    assert "judge it only from its 'signals'" in user and "never describe speech, gameplay or visuals" in user


# --------------------------------------------------------------------------- the four failure kinds are different things

def test_a_valid_empty_list_is_trusted_and_does_not_trigger_the_fallback():
    highlights, _ = _run({"highlights": []})
    assert highlights == []


@pytest.mark.parametrize(
    "response",
    [{}, {"result": []}, {"highlights": "none"}, {"highlights": {"c0": 1}}, {"highlights": None}, ["not", "a", "dict"], "text"],
    ids=["no-key", "wrong-key", "string", "dict", "null", "list-root", "str-root"],
)
def test_a_reply_that_breaks_the_schema_falls_back_to_heuristics(response):
    highlights, _ = _run(response)
    assert _fallback_only(highlights)


@pytest.mark.parametrize(
    "error", [ValueError("LLM did not return valid JSON"), ValueError("LLM returned an empty response"), TimeoutError("timed out"), ConnectionError("down"), RuntimeError("boom")],
    ids=["malformed-json", "empty-response", "timeout", "connection", "other"],
)
def test_api_and_parse_failures_fall_back_to_heuristics(error):
    highlights, _ = _run(None, error=error)
    assert _fallback_only(highlights)


def test_failure_kinds_are_told_apart_in_the_log(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.ai.categories.strategy"):
        _run(None, error=ValueError("bad json"))
        _run(None, error=TimeoutError("slow"))
        _run({"highlights": "nope"})
    assert "reason=bad_json" in caplog.text and "reason=api_error" in caplog.text and "highlight_llm_invalid_schema" in caplog.text


def test_a_list_whose_every_entry_is_unusable_falls_back_instead_of_returning_nothing():
    highlights, _ = _run({"highlights": [_item("c999"), "garbage", _item("c0", score="high")]})
    assert _fallback_only(highlights)


def test_one_bad_entry_does_not_spoil_the_good_ones():
    highlights, _ = _run({"highlights": [_item("c0"), "garbage", 42, None, _item("nope"), _item("c30")]})
    assert sorted((h.start, h.end) for h in highlights) == [(0.0, 30.0), (30.0, 60.0)]
    assert not _fallback_only(highlights)


# --------------------------------------------------------------------------- candidate ids

def test_an_unknown_candidate_id_is_rejected():
    highlights, _ = _run({"highlights": [_item("c7"), _item("c0")]})
    assert [h.start for h in highlights] == [0.0]


def test_a_candidate_from_another_batch_is_unknown_in_this_one(mocker):
    mocker.patch("app.services.ai.categories.strategy.settings.HIGHLIGHT_CANDIDATES_PER_LLM_CALL", 1)

    class _AnswersWithC0(LLMProvider):
        def complete_json(self, system_prompt, user_prompt):
            return {"highlights": [_item("c0")]}  # every batch answers "c0", but only the first batch has it

    highlights = STRATEGY.find_highlights(_video(), _AnswersWithC0())

    assert [h.start for h in highlights if h.confidence != FALLBACK_CONFIDENCE] == [0.0]  # accepted once, not three times


@pytest.mark.parametrize("candidate_id", [42, ["c0"], {"id": "c0"}, True])
def test_a_candidate_id_of_the_wrong_type_is_rejected(candidate_id):
    highlights, _ = _run({"highlights": [_item(candidate_id), _item("c30")]})
    assert [h.start for h in highlights] == [30.0]


def test_a_missing_id_is_inferred_from_the_timestamps_only_when_they_fit_one_window():
    legacy = {k: v for k, v in _item().items() if k != "candidate_id"}
    inside, _ = _run({"highlights": [{**legacy, "start": 2.0, "end": 12.0}]})
    assert [(h.start, h.end) for h in inside] == [(2.0, 12.0)]

    nowhere, _ = _run({"highlights": [{**legacy, "start": 200.0, "end": 210.0}]})
    assert _fallback_only(nowhere)  # fits no window: nothing is attributed, the whole answer is unusable

    no_id_no_times, _ = _run({"highlights": [legacy, _item("c30")]})
    assert [h.start for h in no_id_no_times] == [30.0]


# --------------------------------------------------------------------------- timestamps

@pytest.mark.parametrize(
    "times",
    [
        {"start": 20.0, "end": 10.0},  # start after end
        {"start": 10.0, "end": 10.0},  # empty
        {"start": "soon", "end": 20.0},
        {"start": float("nan"), "end": 20.0},
        {"start": 5.0, "end": float("inf")},
        {"start": 5.0},  # only one end
        {"end": 25.0},
        {"start": True, "end": 20.0},
    ],
    ids=["reversed", "empty", "text", "nan", "inf", "no-end", "no-start", "bool"],
)
def test_invalid_timestamps_reject_the_entry(times):
    highlights, _ = _run({"highlights": [_item("c0", **times), _item("c30")]})
    assert [h.start for h in highlights] == [30.0]


def test_a_range_mostly_outside_the_window_is_rejected():
    highlights, _ = _run({"highlights": [_item("c0", start=25.0, end=55.0), _item("c30")]})  # 5 of 30 s inside c0
    assert [h.start for h in highlights] == [30.0]


def test_a_range_that_spills_over_the_window_edge_is_clamped_to_it():
    highlights, _ = _run({"highlights": [_item("c0", start=-8.0, end=34.0)]})  # 34 of 42 s inside
    assert (highlights[0].start, highlights[0].end) == (0.0, 30.0)  # the model cannot take the clip out of its window


def test_no_accepted_range_ever_leaves_its_window_or_the_video():
    response = {"highlights": [_item("c15", start=-100.0, end=500.0), _item("c30", start=29.0, end=80.0)]}
    highlights, _ = _run(response)
    for h in highlights:
        assert 0.0 <= h.start < h.end <= 60.0


def test_times_hanging_over_both_edges_of_the_window_are_confined_to_it():
    # c30 is [30, 60]: a claim of [20, 70] is 60% inside it, so it is clamped, not rejected
    highlights, _ = _run({"highlights": [_item("c30", start=20.0, end=70.0)]})
    assert (highlights[0].start, highlights[0].end) == (30.0, 60.0)


def test_absurd_negative_or_far_beyond_the_end_times_are_rejected_not_clamped():
    # [-3, 999] puts 3% of the claim inside c30: that is not a refinement of the window, it is invented
    highlights, _ = _run({"highlights": [_item("c30", start=-3.0, end=999.0), _item("c0")]})
    assert [h.start for h in highlights] == [0.0]


# --------------------------------------------------------------------------- scores, confidence, text

def test_scores_are_clamped_to_0_100_and_non_numeric_scores_are_rejected():
    highlights, _ = _run({"highlights": [_item("c0", score=250), _item("c15", score=-20), _item("c30", score="great")]})
    assert {h.start: h.score for h in highlights} == {0.0: 100.0, 15.0: 0.0}


@pytest.mark.parametrize("score", [None, float("nan"), float("inf"), [80], {"v": 80}, True])
def test_unusable_scores_reject_the_entry(score):
    highlights, _ = _run({"highlights": [_item("c0", score=score), _item("c30")]})
    assert [h.start for h in highlights] == [30.0]


def test_confidence_is_clamped_to_0_1_and_defaults_when_missing_or_invalid():
    items = [
        _item("c0", confidence=7), _item("c15", confidence=-1),
        _item("c30", confidence="sure"),
    ]
    highlights, _ = _run({"highlights": items})
    assert {h.start: h.confidence for h in highlights} == {0.0: 1.0, 15.0: 0.0, 30.0: 0.5}  # invalid -> the default

    missing = {k: v for k, v in _item("c0").items() if k != "confidence"}
    assert _run({"highlights": [missing]})[0][0].confidence == 0.5
    assert _run({"highlights": [_item("c0", confidence=float("nan"))]})[0][0].confidence == 0.5


def test_sub_scores_are_clamped_and_bad_ones_become_none():
    h = _run({"highlights": [_item("c0", hook_score=120, flow_score=-5, value_score="x", trend_score=55)]})[0][0]
    assert (h.hook_score, h.flow_score, h.value_score, h.trend_score) == (100.0, 0.0, None, 55.0)


def test_text_and_reason_must_be_strings_and_fall_back_to_each_other():
    h = _run({"highlights": [_item("c0", text=["not", "text"], reason=123)]})[0][0]
    assert h.reason == h.text == "AI-selected highlight."

    h = _run({"highlights": [_item("c0", text=None, reason="A real sentence about the play.")]})[0][0]
    assert h.text == "A real sentence about the play."


# --------------------------------------------------------------------------- duplicates

def test_the_same_candidate_twice_keeps_the_higher_score():
    highlights, _ = _run({"highlights": [_item("c0", score=60), _item("c0", score=90, reason="The better take on it.")]})
    assert len(highlights) == 1 and highlights[0].score == 90 and highlights[0].reason == "The better take on it."


def test_two_candidates_describing_the_same_stretch_are_reduced_to_the_better_one():
    highlights, _ = _run({"highlights": [_item("c0", score=70, start=0.0, end=30.0), _item("c15", score=85, start=16.0, end=30.0)]})
    assert [(h.start, h.score) for h in highlights] == [(16.0, 85.0)]  # c15's 14 s sits wholly inside c0's range


def test_neighbouring_windows_that_barely_touch_are_both_kept():
    highlights, _ = _run({"highlights": [_item("c0", start=0.0, end=20.0), _item("c30", start=30.0, end=50.0)]})
    assert len(highlights) == 2


# --------------------------------------------------------------------------- fallback

def test_fallback_scores_are_normalized_0_to_100_and_confidence_is_below_an_llm_evaluation():
    highlights, _ = _run(None, error=RuntimeError("down"))
    assert highlights
    for h in highlights:
        assert 0.0 <= h.score <= 100.0 and math.isfinite(h.score)
        assert h.confidence == FALLBACK_CONFIDENCE < 0.5  # a successful LLM answer defaults to 0.5 or the model's own
        assert "heuristic" in h.reason.lower() and "unavailable or unusable" in h.reason
        assert h.hook_score is h.flow_score is h.value_score is h.trend_score is None


def test_fallback_score_is_the_normalized_heuristic_not_a_sum(mocker):
    windows = STRATEGY._generate_candidate_windows(_video())
    highlights, _ = _run(None, error=RuntimeError("down"))
    best = max(w.heuristic_score for w in windows)
    assert max(h.score for h in highlights) == pytest.approx(best * 100)
    assert best <= 1.0  # what the old sum of six components could exceed


def test_fallback_carries_each_windows_own_bounds_and_respects_the_per_batch_cap():
    highlights, _ = _run(None, error=RuntimeError("down"))
    assert {(h.start, h.end) for h in highlights} <= {(0.0, 30.0), (15.0, 45.0), (30.0, 60.0), (45.0, 60.0)}

    long_video = _video(duration=600.0)
    highlights, _ = _run(None, video=long_video, error=RuntimeError("down"))
    assert 0 < len(highlights) <= 8  # one batch of up to 20 candidates -> at most 8 fall back


def test_fallback_works_for_other_categories_too():
    podcast = PodcastCategoryStrategy()
    highlights, _ = _run(None, error=RuntimeError("down"), strategy=podcast)
    assert highlights and all(h.category == podcast.category for h in highlights)
