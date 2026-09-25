import re

from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import VideoCategoryStrategy, _keyword_score
from app.services.video.analysis import average_in_range
from app.services.video.analysis_types import VideoAnalysisInput

_PODCAST_KEYWORDS = [
    "i think", "in my opinion", "honestly", "the truth is", "here's the thing",
    "unpopular opinion", "controversial", "surprisingly", "the biggest mistake",
    "what if", "funny story", "i remember when", "the reason is", "here's why",
    "nobody talks about", "the secret is",
]


class PodcastCategoryStrategy(VideoCategoryStrategy):
    """podcast_score = insight_score + emotion_score + topic_relevance + statement_strength
    + context_score — implemented as heuristic hints fed to the LLM, which judges insight,
    controversy, storytelling, and memorable quotes from the transcript itself."""

    category = VideoCategory.PODCAST

    def keyword_list(self) -> list[str]:
        return _PODCAST_KEYWORDS

    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        speech_energy = average_in_range(analysis.audio_energy, start, end)
        statement_strength = _keyword_score(window_text, self.keyword_list())
        question_count = len(re.findall(r"\?", window_text))
        word_count = max(1, len(window_text.split()))
        topic_relevance = min(1.0, question_count / max(1.0, word_count / 40))
        # Sentence-length variance is a weak proxy for storytelling / emphasis shifts.
        sentences = [s for s in re.split(r"[.!?]", window_text) if s.strip()]
        lengths = [len(s.split()) for s in sentences] or [0]
        variance = (max(lengths) - min(lengths)) / max(1, max(lengths))
        emotion_score = min(1.0, variance * 0.5 + speech_energy * 0.5)
        context_score = min(1.0, len(sentences) / 6)

        return {
            "insight_score": statement_strength,
            "emotion_score": emotion_score,
            "topic_relevance": topic_relevance,
            "statement_strength": statement_strength,
            "context_score": context_score,
        }

    def build_system_prompt(self) -> str:
        return (
            "You are Clipperul's highlight detector for PODCAST videos. Find moments worth "
            "clipping: strong insights, controversial or bold opinions, funny statements, "
            "compelling short stories, surprising facts, memorable quotes, and clear "
            "question/answer exchanges. Prefer self-contained statements that make sense without "
            "extra context, and rely on the transcript text as ground truth — don't invent content. "
            "Judge on hook (does the opening line land immediately), flow (a complete thought with "
            "a real beginning and conclusion, not a mid-sentence fragment), value (a genuine "
            "insight, laugh, or story payoff — not throat-clearing), and trend (the quotable, "
            "shareable shape of clips people actually stop scrolling for)."
        )
