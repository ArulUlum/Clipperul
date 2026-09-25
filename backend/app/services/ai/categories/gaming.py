from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import VideoCategoryStrategy, _keyword_score
from app.services.video.analysis import average_in_range
from app.services.video.analysis_types import VideoAnalysisInput

_GAMING_KEYWORDS = [
    "clutch", "kill", "kills", "boss", "win", "wins", "victory", "gg", "let's go",
    "no way", "insane", "poggers", "pog", "wtf", "oh my god", "rage", "rare",
    "achievement", "unlocked", "clip that", "did you see that", "headshot",
    "one shot", "comeback", "epic", "unbelievable",
]


class GamingCategoryStrategy(VideoCategoryStrategy):
    """gaming_score = speech_energy + emotion_score + scene_change_score + event_score
    + keyword_score + reaction_score — implemented as heuristic hints fed to the LLM,
    which does the final semantic judgement (kills, clutches, funny reactions, etc.)."""

    category = VideoCategory.GAMING

    def keyword_list(self) -> list[str]:
        return _GAMING_KEYWORDS

    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        speech_energy = average_in_range(analysis.audio_energy, start, end)
        scene_change_score = average_in_range(analysis.scene_changes, start, end)
        keyword_score = _keyword_score(window_text, self.keyword_list())
        # Emotion/reaction/event are hard to compute heuristically without a classifier;
        # approximate with exclamation density and short high-energy bursts, and let the
        # LLM do the real semantic judgement using these as weak signals.
        exclamations = window_text.count("!") + window_text.count("?!")
        emotion_score = min(1.0, exclamations / 5)
        reaction_score = min(1.0, speech_energy * 0.6 + emotion_score * 0.4)
        event_score = min(1.0, keyword_score * 0.7 + scene_change_score * 0.3)

        return {
            "speech_energy": speech_energy,
            "emotion_score": emotion_score,
            "scene_change_score": scene_change_score,
            "event_score": event_score,
            "keyword_score": keyword_score,
            "reaction_score": reaction_score,
        }

    def build_system_prompt(self) -> str:
        return (
            "You are Clipperul's highlight detector for GAMING videos. Find moments a gaming "
            "audience would want as a short vertical clip: kills, clutches, boss fights, rare "
            "events, funny reactions, rage moments, achievements, and high-energy commentary. "
            "Weigh windows with high scene_change_score and keyword_score heavily, but use the "
            "transcript text as the source of truth — don't invent moments that aren't supported "
            "by it. Judge on hook (does the action/reaction grab attention fast), flow (a clean "
            "self-contained beginning and payoff, not a fragment), value (a genuinely exciting or "
            "funny moment, not filler), and trend (the punchy, shareable shape of clips that "
            "actually get watched)."
        )
