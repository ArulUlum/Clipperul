from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import VideoCategoryStrategy, cue_score, window_segments
from app.services.video.analysis import audio_stats
from app.services.video.analysis_types import VideoAnalysisInput

# Phrases that mark an insight, an opinion, a story or a surprise (English and Indonesian). They are
# counted as DIFFERENT cues per window, so repeating one phrase doesn't raise the score.
_STATEMENT_CUES = [
    "i think", "in my opinion", "honestly", "the truth is", "here's the thing",
    "unpopular opinion", "controversial", "surprisingly", "the biggest mistake",
    "what if", "funny story", "i remember when", "the reason is", "here's why",
    "nobody talks about", "the secret is", "the thing is", "turns out", "what people don't realize",
    "i learned", "my take", "the real problem", "the difference is", "changed my mind",
    # Indonesian
    "menurut saya", "jujur", "sebenarnya", "faktanya", "yang orang nggak sadar", "yang jarang dibahas",
    "rahasianya", "alasannya", "ternyata", "kesalahan terbesar", "cerita lucu", "dulu pernah",
]
_SPEECH_WORDS_PER_SECOND = 2.5  # ordinary conversational pace
_MIN_ANSWER_WORDS = 15


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _has_answered_question(segments: list) -> bool:
    """A question followed, later in the window, by a real answer (at least ~15 words) — a meaningful
    exchange, not just question marks."""
    for i, seg in enumerate(segments):
        if "?" in seg.text:
            answer_words = sum(len(s.text.split()) for s in segments[i + 1 :])
            if answer_words >= _MIN_ANSWER_WORDS:
                return True
    return False


class PodcastCategoryStrategy(VideoCategoryStrategy):
    """Podcast windows are ranked by what is said; loudness barely counts, because a quiet, thoughtful
    statement can be a better clip than a loud, empty exchange.

    Components (each 0-1, each measuring something the others don't):
    - statement_strength: how many DIFFERENT insight/opinion/story/surprise phrases appear (the old
      insight and statement signals were the same measurement and are now one).
    - qa_exchange: 1 if a question in the window gets a substantial answer, else 0.
    - content_density: words per second against ordinary speech pace, capped — a window full of speech
      versus mostly pauses. A longer transcript does not score higher.
    - segment_integrity: the share of the window's transcript segments that lie fully inside it, a
      cheap sign the window is a self-contained stretch rather than cut mid-thought at both ends.
    - vocal_emphasis: how far the loudest second rises above the window's average, small weight and
      only when there is audio.

    The LLM makes the real judgement; these only decide which windows it sees."""

    category = VideoCategory.PODCAST

    def keyword_list(self) -> list[str]:
        return _STATEMENT_CUES

    def heuristic_weights(self) -> dict[str, float]:
        return {
            "statement_strength": 0.40,
            "qa_exchange": 0.12,
            "content_density": 0.15,
            "segment_integrity": 0.15,
            "vocal_emphasis": 0.08,
        }

    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        scores: dict[str, float] = {}
        duration = max(end - start, 1e-6)

        if window_text:
            scores["statement_strength"] = cue_score(window_text, self.keyword_list(), saturation=3)
            scores["content_density"] = _clamp01(len(window_text.split()) / (duration * _SPEECH_WORDS_PER_SECOND))

            segments = window_segments(analysis, start, end)
            scores["qa_exchange"] = 1.0 if _has_answered_question(segments) else 0.0
            if segments:
                inside = sum(1 for s in segments if s.start >= start and s.end <= end)
                scores["segment_integrity"] = inside / len(segments)

        audio = audio_stats(analysis, start, end)
        if audio and audio["count"]:
            scores["vocal_emphasis"] = _clamp01(audio["peak"] - audio["mean"])

        return scores

    def build_system_prompt(self) -> str:
        return (
            "You are Clipperul's highlight detector for PODCAST videos. Find moments worth clipping: strong "
            "insights, interesting opinions, compelling short stories, funny moments, surprising facts, "
            "memorable statements, meaningful question-and-answer exchanges, and clear self-contained "
            "explanations. Prefer a moment that makes sense on its own, without the rest of the "
            "conversation. How loud or animated it is does not matter: a quiet, thoughtful statement can be "
            "a far better highlight than a loud conversation with nothing in it, so judge the content. Rely "
            "on the transcript text as ground truth — don't invent content, and don't reward a window for "
            "merely being long or having many sentences. Judge on hook (does the opening line land "
            "immediately), flow (a complete thought with a real beginning and conclusion, not a "
            "mid-sentence fragment), value (a genuine insight, laugh, or story payoff — not throat-clearing), "
            "and trend (the quotable, shareable shape of clips people actually stop scrolling for)."
        )
