import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import settings
from app.models.enums import VideoCategory
from app.services.ai.llm import LLMProvider
from app.services.video.analysis import average_in_range
from app.services.video.analysis_types import VideoAnalysisInput

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 30.0
_STRIDE_SECONDS = 15.0
_MAX_CANDIDATES_TO_LLM = 20

# A real reason/title is a sentence — has spaces/punctuation. A bare identifier like
# "emotion_score" (a weaker local model echoing a hint-dict key instead of writing a
# sentence) does not. Catches the general failure mode without hardcoding known key names.
_LOOKS_LIKE_IDENTIFIER_LEAK = re.compile(r"^[a-zA-Z][a-zA-Z_]{1,39}$")


def _is_usable_text(value: str) -> bool:
    value = value.strip()
    return bool(value) and not _LOOKS_LIKE_IDENTIFIER_LEAK.match(value)


@dataclass
class HighlightCandidate:
    start: float
    end: float
    text: str
    score: float
    reason: str
    category: VideoCategory
    confidence: float
    # Per-dimension breakdown behind 'score' (0-100 each) — None on the heuristic-only
    # fallback path, since that path has no basis for these beyond the single combined number.
    hook_score: float | None = None
    flow_score: float | None = None
    value_score: float | None = None
    trend_score: float | None = None


@dataclass
class _CandidateWindow:
    start: float
    end: float
    text: str
    heuristic_scores: dict[str, float]

    @property
    def heuristic_total(self) -> float:
        return sum(self.heuristic_scores.values())


def _segments_in_range(analysis: VideoAnalysisInput, start: float, end: float) -> str:
    parts = [s.text for s in analysis.transcript_segments if s.end >= start and s.start <= end]
    return " ".join(parts).strip()


def _keyword_score(text: str, keywords: list[str]) -> float:
    if not text:
        return 0.0
    lower = text.lower()
    hits = sum(len(re.findall(rf"\b{re.escape(kw)}\b", lower)) for kw in keywords)
    word_count = max(1, len(lower.split()))
    return min(1.0, hits / max(1.0, word_count / 20))


class VideoCategoryStrategy(ABC):
    """One implementation per video category. Each category defines its own heuristic
    feature set and its own LLM prompt/weighting — categories intentionally do NOT share
    a single scoring algorithm (per product requirement)."""

    category: VideoCategory

    @abstractmethod
    def keyword_list(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        """Returns named component scores (e.g. speech_energy, event_score, keyword_score...)."""
        raise NotImplementedError

    @abstractmethod
    def build_system_prompt(self) -> str:
        raise NotImplementedError

    def _generate_candidate_windows(self, analysis: VideoAnalysisInput) -> list[_CandidateWindow]:
        windows: list[_CandidateWindow] = []
        t = 0.0
        while t < analysis.video_duration:
            start, end = t, min(t + _WINDOW_SECONDS, analysis.video_duration)
            text = _segments_in_range(analysis, start, end)
            if text:
                scores = self.compute_heuristic_scores(analysis, text, start, end)
                windows.append(_CandidateWindow(start=start, end=end, text=text, heuristic_scores=scores))
            t += _STRIDE_SECONDS
        windows.sort(key=lambda w: w.heuristic_total, reverse=True)
        return windows[:_MAX_CANDIDATES_TO_LLM]

    def find_highlights(self, analysis: VideoAnalysisInput, llm: LLMProvider) -> list[HighlightCandidate]:
        candidates = self._generate_candidate_windows(analysis)
        if not candidates:
            return []

        # Scored in smaller batches rather than one big call — a weak/local model loses track
        # partway through a long candidate list and starts recycling the same generic reason
        # across several windows instead of describing each one specifically. Each batch falls
        # back to heuristic-only scoring independently if its own LLM call fails, so one bad
        # batch doesn't degrade the whole video to heuristics.
        batch_size = max(1, settings.HIGHLIGHT_CANDIDATES_PER_LLM_CALL)
        highlights: list[HighlightCandidate] = []
        for i in range(0, len(candidates), batch_size):
            highlights.extend(self._score_batch(candidates[i : i + batch_size], llm, analysis.language))
        return highlights

    def _score_batch(
        self, batch: list[_CandidateWindow], llm: LLMProvider, language: str | None = None
    ) -> list[HighlightCandidate]:
        candidate_payload = [
            {
                "start": round(c.start, 2),
                "end": round(c.end, 2),
                "transcript": c.text[:800],
                # A plain descriptive string, not a nested JSON object — smaller local models
                # have been observed literally echoing a dict *key* (e.g. "emotion_score") back
                # as the "reason" field when the hints are shaped like the expected output.
                "hint_scores": ", ".join(f"{k}={v:.2f}" for k, v in c.heuristic_scores.items()),
            }
            for c in batch
        ]

        language_instruction = (
            f"Write every text field ('text' and 'reason') in the SAME language as the transcript "
            f"excerpts below — they are in language code '{language}'. Do not translate to English "
            f"or any other language, even though these instructions are written in English.\n\n"
            if language
            else (
                "Write every text field ('text' and 'reason') in the SAME language the transcript "
                "excerpts below are written in — do not translate to English, even though these "
                "instructions are written in English.\n\n"
            )
        )

        user_prompt = (
            language_instruction +
            "Here are candidate windows from ONE PART of a video's transcript (other parts are scored "
            "separately), each with a transcript excerpt and heuristic hint scores (0-1, higher = more "
            "likely to be a strong highlight for this category — informational only, not something to "
            "repeat back).\n\n"
            "Score EVERY window individually on four dimensions (0-100 each) — this is a viral-"
            "potential rating, so score honestly across the full range, not just high numbers:\n"
            "- hook_score: does it open with something that grabs attention in the first couple "
            "seconds (a bold claim, a question, a surprising moment) rather than a slow/unclear start?\n"
            "- flow_score: does it play as a complete, self-contained moment — a real beginning and "
            "payoff, not a fragment that starts or ends mid-thought and needs the rest of the video?\n"
            "- value_score: does it actually give the viewer something — insight, emotion, "
            "entertainment, useful information — worth the seconds it costs them?\n"
            "- trend_score: does it have the shape of a clip that performs well as a short-form "
            "video — punchy, quotable, shareable — rather than flat filler?\n"
            "Set 'score' (0-100) as your overall judgement weighing all four — usually close to "
            "their average, but use your judgement, don't just average mechanically.\n\n"
            "IMPORTANT — return MULTIPLE highlights, not just the single best one: this batch has "
            f"{len(batch)} candidate window(s). If several are genuinely distinct, worthwhile moments "
            "(different jokes, different points, different plays), include ALL of them — don't stop at "
            "one just because it's the strongest. Only return fewer than the candidates offer if the "
            "rest are truly not worth clipping (low scores are fine and expected for weak candidates — "
            "score them low rather than omitting them, unless they're pure filler with nothing usable).\n\n"
            "Prefer start/end times that align to natural sentence/thought boundaries in the transcript "
            "over the exact window edges, so the clip doesn't cut off mid-sentence. Write 'reason' as "
            "one natural-language sentence, IN THE TRANSCRIPT'S OWN LANGUAGE, describing what actually "
            "happens in that moment. 'reason' "
            "must never be a field/variable name, a bare number, or the word 'score' — always a real "
            "sentence grounded in the transcript. Estimate your confidence (0-1).\n\n"
            "Every 'reason' must quote or closely paraphrase a specific word, phrase, or number that "
            "actually appears in THAT window's own transcript excerpt above — not a summary that could "
            "apply to any window in this batch. If you can't point to something in that specific "
            "excerpt that justifies the reason, the reason is too generic — go back and look at what "
            "that window's transcript actually says.\n\n"
            f"Candidates (JSON): {candidate_payload}\n\n"
            'Respond with JSON: {"highlights": [{"start": number, "end": number, "text": string, '
            '"score": number (0-100), "hook_score": number (0-100), "flow_score": number (0-100), '
            '"value_score": number (0-100), "trend_score": number (0-100), '
            '"reason": string (a full sentence), "confidence": number (0-1)}]}'
        )

        try:
            result = llm.complete_json(self.build_system_prompt(), user_prompt)
            # Distinguish "no highlights key at all" (malformed/unusable response — the model
            # didn't follow the schema) from "highlights: []" (the model followed the schema and
            # legitimately found nothing worth clipping in this batch) — only the former should
            # fall back to heuristics; the latter is a real judgement worth trusting.
            if "highlights" not in result:
                raise ValueError("LLM response missing 'highlights' key")
            raw_highlights = result["highlights"]
            llm_call_failed = False
        except Exception:
            logger.exception("LLM highlight scoring failed for category=%s; falling back to heuristics", self.category)
            raw_highlights = []
            llm_call_failed = True

        if not raw_highlights:
            if not llm_call_failed:
                return []
            # Fallback: heuristic ranking alone for this batch's candidates so the pipeline
            # never dead-ends just because this one call failed.
            return [
                HighlightCandidate(
                    start=c.start,
                    end=c.end,
                    text=c.text[:500],
                    score=round(c.heuristic_total * 100, 2),
                    reason="Selected via heuristic scoring (LLM unavailable).",
                    category=self.category,
                    confidence=0.4,
                )
                for c in batch[:8]
            ]

        highlights: list[HighlightCandidate] = []
        for h in raw_highlights:
            try:
                text = str(h.get("text", ""))[:500]
                reason = str(h.get("reason", ""))
                # Smaller/local models occasionally echo a hint-dict key (e.g. "emotion_score")
                # back as "reason" instead of writing a sentence — degrade to the other field,
                # or a generic-but-honest fallback, rather than surfacing that to users verbatim.
                if not _is_usable_text(reason):
                    reason = text if _is_usable_text(text) else "AI-selected highlight."
                if not _is_usable_text(text):
                    text = reason

                def _sub_score(key: str) -> float | None:
                    value = h.get(key)
                    try:
                        return max(0.0, min(100.0, float(value)))
                    except (TypeError, ValueError):
                        return None

                highlights.append(
                    HighlightCandidate(
                        start=float(h["start"]),
                        end=float(h["end"]),
                        text=text,
                        score=float(h.get("score", 0)),
                        reason=reason,
                        category=self.category,
                        confidence=float(h.get("confidence", 0.5)),
                        hook_score=_sub_score("hook_score"),
                        flow_score=_sub_score("flow_score"),
                        value_score=_sub_score("value_score"),
                        trend_score=_sub_score("trend_score"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return highlights


__all__ = ["VideoCategoryStrategy", "HighlightCandidate", "average_in_range", "_keyword_score"]
