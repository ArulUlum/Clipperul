import re

from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import VideoCategoryStrategy, cue_score
from app.services.video.analysis import audio_stats, scene_stats
from app.services.video.analysis_types import VideoAnalysisInput

_GAMING_KEYWORDS = [
    "clutch", "kill", "kills", "boss", "win", "wins", "victory", "gg", "let's go",
    "no way", "insane", "poggers", "pog", "wtf", "oh my god", "rage", "rare",
    "achievement", "unlocked", "clip that", "did you see that", "headshot",
    "one shot", "comeback", "epic", "unbelievable",
    # Indonesian gaming commentary
    "gila", "anjir", "anjay", "mantap", "menang", "sikat", "gak nyangka", "ga nyangka",
]
_LAUGHTER = {"haha", "hahaha", "hehe", "lol", "lmao", "wkwk", "wkwkwk"}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _vocal_reaction(text: str) -> float:
    """Exclamations, SHOUTED words and laughter per ~8 words, 0-1. A weak signal on its own — speech-to-text
    rarely keeps the punctuation — which is why it carries a small weight."""
    words = text.split()
    shouted = sum(1 for w in words if len(re.sub(r"\W", "", w)) >= 3 and w.isupper())
    laughter = sum(1 for w in words if re.sub(r"\W", "", w).lower() in _LAUGHTER)
    marks = text.count("!") + shouted + laughter
    return _clamp01(marks / max(3.0, len(words) / 8))


class GamingCategoryStrategy(VideoCategoryStrategy):
    """Gaming windows are ranked by what the video's audio does first and what is said second.

    Components (each 0-1, each measuring something the others don't):
    - audio_excitement: how loud the window is relative to the rest of the video — its 90th percentile
      and mean — plus how far its loudest second jumps above its own average (a spike or a shout).
      Present whenever the video has audio, with or without commentary.
    - vocal_reaction: exclamations, shouted words and laughter in the transcript. Transcript only.
    - keyword_score: how many DIFFERENT gaming phrases are said. Transcript only, small weight.
    - scene_activity: how much the picture changed, a weak supporting signal (a cut or a menu is not an
      exciting event). Only where scene analysis actually looked.

    A component that can't be measured for a window is left out and the rest are re-weighted, so quiet
    but impressive gameplay with no commentary is ranked on its audio and picture alone, not penalized
    for having nothing to transcribe. The LLM makes the real judgement."""

    category = VideoCategory.GAMING

    def keyword_list(self) -> list[str]:
        return _GAMING_KEYWORDS

    def heuristic_weights(self) -> dict[str, float]:
        return {"audio_excitement": 0.45, "vocal_reaction": 0.12, "keyword_score": 0.13, "scene_activity": 0.12}

    def allows_silent_windows(self) -> bool:
        return True

    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        scores: dict[str, float] = {}

        audio = audio_stats(analysis, start, end)
        if audio and audio["count"]:
            burst = max(0.0, audio["peak"] - audio["mean"])
            scores["audio_excitement"] = _clamp01(0.45 * audio["p90"] + 0.35 * burst + 0.20 * audio["mean"])

        if window_text:
            scores["vocal_reaction"] = _vocal_reaction(window_text)
            scores["keyword_score"] = cue_score(window_text, self.keyword_list(), saturation=2)

        scene = scene_stats(analysis, start, end)
        if scene and scene["count"]:
            scores["scene_activity"] = _clamp01(0.6 * scene["p90"] + 0.4 * scene["peak"])

        return scores

    def build_system_prompt(self) -> str:
        return (
            "You are Clipperul's highlight detector for GAMING videos. Find moments a gaming audience would "
            "want as a short vertical clip: clutch plays, boss encounters, wins and comebacks, rare rewards "
            "and drops, achievements, skillful gameplay, funny failures, unexpected events, and genuine "
            "player reactions (real excitement, surprise, frustration or laughter — not filler shouting). "
            "A great moment is not always loud or talked about: a quiet, impressive play can beat a noisy "
            "stretch of nothing, so do not require commentary or gaming keywords. "
            "What you can see of a window is limited to its transcript (possibly empty) and its audio and "
            "scene-change signals. Those signals are relative to the rest of this video: a loud burst or "
            "a sudden jump in the picture is a reason to look closer, not proof that something happened — a "
            "cut, a menu or a loading screen changes the picture too. Never state or imply that a specific "
            "gameplay event occurred (a kill, a boss, a win) unless the transcript says so; when you can "
            "only go on the signals, say that ('a sudden loud burst'), do not make up what it was, and lower "
            "your confidence. Judge on hook (does the action or reaction grab attention fast), flow (a "
            "clean self-contained beginning and payoff, not a fragment), value (a genuinely exciting or funny "
            "moment, not filler), and trend (the punchy, shareable shape of clips that actually get watched)."
        )
