from app.models.enums import VideoCategory
from app.services.ai.categories.gaming import GamingCategoryStrategy
from app.services.ai.categories.podcast import PodcastCategoryStrategy
from app.services.ai.llm import LLMProvider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput


def _analysis_with_text(text: str, language: str | None = None) -> VideoAnalysisInput:
    return VideoAnalysisInput(
        video_duration=60.0,
        transcript_segments=[TranscriptSegmentResult(start=0.0, end=30.0, text=text)],
        audio_energy=[AudioEnergyPoint(time=t, energy=0.7) for t in range(0, 30)],
        scene_changes=[SceneChangePoint(time=t, magnitude=0.6) for t in range(0, 30)],
        language=language,
    )


class _PromptCapturingLLMProvider(LLMProvider):
    """Records the prompts it was called with instead of hitting a real model — used to assert
    on prompt *content* (e.g. the language instruction) rather than scoring behavior."""

    def __init__(self):
        self.system_prompt: str | None = None
        self.user_prompt: str | None = None

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return {"highlights": []}


def test_gaming_strategy_rewards_gaming_keywords():
    strategy = GamingCategoryStrategy()
    assert strategy.category == VideoCategory.GAMING

    analysis = _analysis_with_text("Oh my god, clutch! No way! GG, that was an insane comeback kill!")
    scores = strategy.compute_heuristic_scores(analysis, analysis.transcript_segments[0].text, 0.0, 30.0)

    assert scores["keyword_score"] > 0.0
    assert set(scores.keys()) == {
        "speech_energy", "emotion_score", "scene_change_score", "event_score", "keyword_score", "reaction_score",
    }


def test_podcast_strategy_rewards_opinion_language():
    strategy = PodcastCategoryStrategy()
    assert strategy.category == VideoCategory.PODCAST

    analysis = _analysis_with_text("Honestly, in my opinion, here's the thing nobody talks about.")
    scores = strategy.compute_heuristic_scores(analysis, analysis.transcript_segments[0].text, 0.0, 30.0)

    assert scores["insight_score"] > 0.0
    assert set(scores.keys()) == {
        "insight_score", "emotion_score", "topic_relevance", "statement_strength", "context_score",
    }


def test_gaming_and_podcast_score_the_same_text_differently():
    """Categories must not share a single scoring algorithm."""
    text = "Honestly that was an insane clutch, no way, in my opinion the best play ever."
    analysis = _analysis_with_text(text)

    gaming_scores = GamingCategoryStrategy().compute_heuristic_scores(analysis, text, 0.0, 30.0)
    podcast_scores = PodcastCategoryStrategy().compute_heuristic_scores(analysis, text, 0.0, 30.0)

    assert set(gaming_scores.keys()) != set(podcast_scores.keys())


def test_highlight_prompt_instructs_matching_language_when_detected():
    """A clip's title/caption are derived from the LLM's 'reason' field (see _auto_title in
    video_tasks.py) — without an explicit instruction, models tend to answer in English
    regardless of the transcript's own language, so the prompt must say so explicitly."""
    analysis = _analysis_with_text("Ini adalah momen yang sangat penting dalam video ini.", language="id")
    llm = _PromptCapturingLLMProvider()

    GamingCategoryStrategy().find_highlights(analysis, llm)

    assert llm.user_prompt is not None
    assert "'id'" in llm.user_prompt
    assert "same language" in llm.user_prompt.lower()
    assert "do not translate" in llm.user_prompt.lower()


def test_highlight_prompt_still_instructs_language_matching_when_undetected():
    """No detected language code shouldn't drop the instruction entirely — fall back to
    'whatever language the transcript itself is in' instead of silently defaulting to English."""
    analysis = _analysis_with_text("Some transcript text.", language=None)
    llm = _PromptCapturingLLMProvider()

    GamingCategoryStrategy().find_highlights(analysis, llm)

    assert llm.user_prompt is not None
    assert "same language" in llm.user_prompt.lower()
