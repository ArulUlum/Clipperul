from app.models.enums import VideoCategory
from app.services.ai.categories.gaming import GamingCategoryStrategy
from app.services.ai.categories.news import NewsCategoryStrategy
from app.services.ai.categories.podcast import PodcastCategoryStrategy
from app.services.ai.categories.registry import get_strategy, list_available_categories
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
    # every component is a 0-1 measurement of something different; none is derived from another
    assert set(scores.keys()) == {"audio_excitement", "vocal_reaction", "keyword_score", "scene_activity"}
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_podcast_strategy_rewards_opinion_language():
    strategy = PodcastCategoryStrategy()
    assert strategy.category == VideoCategory.PODCAST

    analysis = _analysis_with_text("Honestly, in my opinion, here's the thing nobody talks about.")
    scores = strategy.compute_heuristic_scores(analysis, analysis.transcript_segments[0].text, 0.0, 30.0)

    assert scores["statement_strength"] > 0.0
    assert set(scores.keys()) == {
        "statement_strength", "qa_exchange", "content_density", "segment_integrity", "vocal_emphasis",
    }
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_news_strategy_prefers_policy_and_critical_talk_over_ceremony():
    strategy = NewsCategoryStrategy()
    assert strategy.category == VideoCategory.NEWS

    substance = (
        "Ke depan pemerintah akan menaikkan anggaran pangan sebesar 20 persen. "
        "Korupsi tidak boleh lagi dibiarkan, kita harus tegas."
    )
    ceremony = "Assalamualaikum. Yang terhormat hadirin sekalian, selamat pagi, terima kasih atas kehadirannya."
    a = _analysis_with_text(substance)
    b = _analysis_with_text(ceremony)
    good = strategy.compute_heuristic_scores(a, substance, 0.0, 30.0)
    bad = strategy.compute_heuristic_scores(b, ceremony, 0.0, 30.0)

    assert set(good) == {
        "announcement_score", "critical_score", "buzz_score", "numbers_score", "substance_score", "vocal_emphasis",
    }
    assert good["announcement_score"] > 0 and good["critical_score"] > 0 and good["numbers_score"] > 0
    assert bad["announcement_score"] == 0 and bad["critical_score"] == 0 and bad["buzz_score"] == 0
    assert good["substance_score"] > bad["substance_score"]
    assert strategy.combine_heuristics(good) > strategy.combine_heuristics(bad)


def test_news_strategy_rewards_revelations_from_anyone():
    text = "Jujur, ternyata faktanya begini, ini rahasia yang terungkap dan jadi viral."
    analysis = _analysis_with_text(text)
    scores = NewsCategoryStrategy().compute_heuristic_scores(analysis, text, 0.0, 30.0)
    assert scores["buzz_score"] > 0


def test_news_prompt_covers_officials_podcasts_and_influencers_and_skips_protocol():
    prompt = NewsCategoryStrategy().build_system_prompt().lower()
    assert "president" in prompt and "minister" in prompt
    assert "podcast" in prompt and "influencer" in prompt
    assert "skip protocol" in prompt
    assert "never invent" in prompt


def test_news_is_registered_so_detection_can_pick_it():
    assert VideoCategory.NEWS in list_available_categories()
    assert isinstance(get_strategy(VideoCategory.NEWS), NewsCategoryStrategy)


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
