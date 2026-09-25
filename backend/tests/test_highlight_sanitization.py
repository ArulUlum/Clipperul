from app.services.ai.categories.gaming import GamingCategoryStrategy
from app.services.ai.llm import LLMProvider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput


class _FakeLLMProvider(LLMProvider):
    """Simulates a weaker local model echoing a hint-dict key back as the reason —
    the failure mode observed with a small Ollama model against a real video."""

    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return self.response


def _analysis() -> VideoAnalysisInput:
    return VideoAnalysisInput(
        video_duration=60.0,
        transcript_segments=[
            TranscriptSegmentResult(start=0.0, end=30.0, text="That was an absolutely insane clutch play!")
        ],
        audio_energy=[AudioEnergyPoint(time=t, energy=0.7) for t in range(0, 30)],
        scene_changes=[SceneChangePoint(time=t, magnitude=0.6) for t in range(0, 30)],
    )


def test_field_name_leak_in_reason_is_replaced_not_surfaced():
    llm = _FakeLLMProvider(
        {
            "highlights": [
                {
                    "start": 0.0,
                    "end": 30.0,
                    "text": "Player pulls off a clutch 1v3 win in the final round.",
                    "score": 90,
                    "reason": "emotion_score",  # the exact failure mode observed in production
                    "confidence": 0.9,
                }
            ]
        }
    )

    highlights = GamingCategoryStrategy().find_highlights(_analysis(), llm)

    assert len(highlights) == 1
    assert highlights[0].reason != "emotion_score"
    assert highlights[0].reason == highlights[0].text  # fell back to the usable text field


def test_field_name_leak_in_both_fields_falls_back_to_generic_reason():
    llm = _FakeLLMProvider(
        {
            "highlights": [
                {"start": 0.0, "end": 30.0, "text": "keyword_score", "score": 90, "reason": "event_score", "confidence": 0.9}
            ]
        }
    )

    highlights = GamingCategoryStrategy().find_highlights(_analysis(), llm)

    assert len(highlights) == 1
    assert highlights[0].reason == "AI-selected highlight."
    assert highlights[0].text == "AI-selected highlight."


def test_normal_sentence_reason_passes_through_unchanged():
    llm = _FakeLLMProvider(
        {
            "highlights": [
                {
                    "start": 0.0,
                    "end": 30.0,
                    "text": "Player pulls off a clutch 1v3 win in the final round.",
                    "score": 90,
                    "reason": "The player wins a tense 1v3 clutch, a genuinely exciting moment.",
                    "confidence": 0.9,
                }
            ]
        }
    )

    highlights = GamingCategoryStrategy().find_highlights(_analysis(), llm)

    assert highlights[0].reason == "The player wins a tense 1v3 clutch, a genuinely exciting moment."


def test_sub_scores_are_parsed_and_clamped():
    llm = _FakeLLMProvider(
        {
            "highlights": [
                {
                    "start": 0.0,
                    "end": 30.0,
                    "text": "Clutch play.",
                    "score": 90,
                    "hook_score": 85,
                    "flow_score": 200,  # out of range — should clamp to 100
                    "value_score": -10,  # out of range — should clamp to 0
                    "trend_score": "not a number",  # unparseable — should become None
                    "reason": "A clutch play that grabs attention immediately.",
                    "confidence": 0.9,
                }
            ]
        }
    )

    highlights = GamingCategoryStrategy().find_highlights(_analysis(), llm)

    assert highlights[0].hook_score == 85
    assert highlights[0].flow_score == 100
    assert highlights[0].value_score == 0
    assert highlights[0].trend_score is None


def test_heuristic_fallback_leaves_sub_scores_none():
    llm = _FakeLLMProvider({})  # no "highlights" key -> triggers heuristic fallback

    highlights = GamingCategoryStrategy().find_highlights(_analysis(), llm)

    assert len(highlights) > 0
    assert all(h.hook_score is None for h in highlights)
    assert all(h.flow_score is None for h in highlights)
    assert all(h.value_score is None for h in highlights)
    assert all(h.trend_score is None for h in highlights)


def test_explicit_empty_highlights_list_is_trusted_not_treated_as_failure():
    # Unlike a missing "highlights" key (malformed/unusable response -> heuristic fallback),
    # an explicit empty list means the model followed the schema and legitimately judged
    # nothing in this batch worth clipping — that judgement should be trusted, not overridden.
    llm = _FakeLLMProvider({"highlights": []})

    highlights = GamingCategoryStrategy().find_highlights(_analysis(), llm)

    assert highlights == []


def test_find_highlights_splits_large_candidate_pools_into_multiple_llm_calls(mocker):
    # A weak/local model loses track and starts repeating itself when handed too many
    # candidates in one call — batching keeps each call small regardless of pool size.
    mocker.patch("app.services.ai.categories.strategy.settings.HIGHLIGHT_CANDIDATES_PER_LLM_CALL", 3)

    segments = [
        TranscriptSegmentResult(start=t, end=t + 10.0, text=f"Moment {i} happens here, quite a statement.")
        for i, t in enumerate(range(0, 300, 15))
    ]
    analysis = VideoAnalysisInput(
        video_duration=300.0,
        transcript_segments=segments,
        audio_energy=[AudioEnergyPoint(time=t, energy=0.5) for t in range(0, 300, 5)],
        scene_changes=[SceneChangePoint(time=t, magnitude=0.5) for t in range(0, 300, 5)],
    )

    class _CountingLLMProvider(LLMProvider):
        def __init__(self):
            self.call_count = 0

        def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
            self.call_count += 1
            return {"highlights": []}

    llm = _CountingLLMProvider()
    GamingCategoryStrategy().find_highlights(analysis, llm)

    assert llm.call_count > 1


def test_find_highlights_merges_heuristic_fallback_across_failed_batches(mocker):
    # If every batch's LLM call fails, each batch should independently fall back to heuristic
    # scoring rather than only the first batch producing results.
    mocker.patch("app.services.ai.categories.strategy.settings.HIGHLIGHT_CANDIDATES_PER_LLM_CALL", 3)

    segments = [
        TranscriptSegmentResult(start=t, end=t + 10.0, text=f"Moment {i} happens here, quite a statement.")
        for i, t in enumerate(range(0, 300, 15))
    ]
    analysis = VideoAnalysisInput(
        video_duration=300.0,
        transcript_segments=segments,
        audio_energy=[AudioEnergyPoint(time=t, energy=0.5) for t in range(0, 300, 5)],
        scene_changes=[SceneChangePoint(time=t, magnitude=0.5) for t in range(0, 300, 5)],
    )

    class _AlwaysFailingLLMProvider(LLMProvider):
        def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
            raise RuntimeError("simulated LLM outage")

    highlights = GamingCategoryStrategy().find_highlights(analysis, _AlwaysFailingLLMProvider())

    # More than one batch's worth of heuristic fallback highlights (each batch contributes up
    # to 8), proving the fallback ran per-batch rather than stopping after the first failure.
    assert len(highlights) > 8
