import logging
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.config import settings
from app.models.enums import VideoCategory
from app.services.ai.llm import LLMProvider
from app.services.video.analysis import audio_stats, average_in_range, scene_stats
from app.services.video.analysis_types import VideoAnalysisInput

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 30.0
_STRIDE_SECONDS = 15.0
_MAX_CANDIDATES_TO_LLM = 20
# A trailing window shorter than this is skipped when an earlier window exists: the previous one already
# covers that stretch, and a near-duplicate wastes an LLM slot.
_MIN_TAIL_WINDOW_SECONDS = _WINDOW_SECONDS / 2

# Candidate selection (see select_diverse_candidates): a share of the LLM slots is reserved for
# windows the heuristics did NOT rank highest, so the model also sees moments the heuristics undervalue.
_EXPLORATION_SHARE = 0.2
_MIN_SEPARATION_SECONDS = _WINDOW_SECONDS  # first pass: chosen windows don't overlap
_MAX_SECTIONS = 8
_MIN_SECTION_SECONDS = 60.0

# What the LLM is allowed to hand back for a candidate (see VideoCategoryStrategy._validate_highlights).
_MIN_CLAIMED_OVERLAP = 0.5  # share of a returned range that must lie inside its candidate's window
_MIN_HIGHLIGHT_SECONDS = 1.0
_DUPLICATE_OVERLAP = 0.5  # two highlights overlapping by more than this share of the shorter one are one

FALLBACK_CONFIDENCE = 0.3  # always below what a successful LLM evaluation reports
_FALLBACK_MAX_PER_BATCH = 8
_FALLBACK_REASON = "Selected by heuristic scoring because the AI evaluation was unavailable or unusable."

# A real reason/title is a sentence — has spaces/punctuation. A bare identifier like
# "emotion_score" (a weaker local model echoing a hint-dict key instead of writing a
# sentence) does not. Catches the general failure mode without hardcoding known key names.
_LOOKS_LIKE_IDENTIFIER_LEAK = re.compile(r"^[a-zA-Z][a-zA-Z_]{1,39}$")


def _is_usable_text(value: str) -> bool:
    value = value.strip()
    return bool(value) and not _LOOKS_LIKE_IDENTIFIER_LEAK.match(value)


def _finite_number(value) -> float | None:
    """The value as a finite float, or None (booleans, NaN, infinity and non-numeric text included)."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    candidate_id: str  # stable for a given start time: the LLM answers by this, not by free-form timestamps
    start: float
    end: float
    text: str
    heuristic_scores: dict[str, float]
    # The strategy's weighted combination of heuristic_scores, 0-1. Only for ranking candidates.
    heuristic_score: float = 0.0
    # Audio / scene-change levels of the window as plain text for the prompt.
    signals: str = ""

    @property
    def heuristic_total(self) -> float:
        """Kept for compatibility: it used to be the raw sum of the component scores. It is now the
        normalized 0-1 heuristic_score."""
        return self.heuristic_score

    @property
    def has_transcript(self) -> bool:
        return bool(self.text)


# --------------------------------------------------------------------------- text helpers

def _segments_in_range(analysis: VideoAnalysisInput, start: float, end: float) -> str:
    parts = [s.text for s in analysis.transcript_segments if s.end > start and s.start < end]
    return " ".join(parts).strip()


def window_segments(analysis: VideoAnalysisInput, start: float, end: float) -> list:
    """The transcript segments that overlap [start, end)."""
    return [s for s in analysis.transcript_segments if s.end > start and s.start < end]


def _keyword_score(text: str, keywords: list[str]) -> float:
    """Keyword hits per ~20 words, capped at 1 — counts repeats, so a repeated word inflates it. Kept for
    category detection; the strategies' own ranking uses cue_score (distinct cues) instead."""
    if not text:
        return 0.0
    lower = text.lower()
    hits = sum(len(re.findall(rf"\b{re.escape(kw)}\b", lower)) for kw in keywords)
    word_count = max(1, len(lower.split()))
    return min(1.0, hits / max(1.0, word_count / 20))


@lru_cache(maxsize=64)
def _cue_pattern(cues: tuple[str, ...]) -> re.Pattern:
    alternatives = "|".join(re.escape(c) for c in sorted(set(cues), key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


def distinct_cues(text: str, cues) -> int:
    """How many DIFFERENT cues from the list appear in the text (a cue said five times counts once)."""
    if not text or not cues:
        return 0
    return len({m.group(0).lower() for m in _cue_pattern(tuple(cues)).finditer(text)})


def cue_score(text: str, cues, saturation: int = 3) -> float:
    """0-1: distinct cues found / `saturation`. Several different signals of the same kind mean more
    than one word repeated, and `saturation` of them is already as strong as this signal gets."""
    return min(1.0, distinct_cues(text, cues) / max(1, saturation))


# --------------------------------------------------------------------------- candidate windows

def _effective_duration(analysis: VideoAnalysisInput) -> float:
    """The video's duration, or — when it wasn't recorded — what the transcript and audio imply."""
    if analysis.video_duration and analysis.video_duration > 0:
        return float(analysis.video_duration)
    ends = [s.end for s in analysis.transcript_segments]
    ends += [p.time + 1.0 for p in analysis.audio_energy]
    return float(max(ends)) if ends else 0.0


def _has_signal(analysis: VideoAnalysisInput, start: float, end: float) -> bool:
    audio = audio_stats(analysis, start, end)
    scene = scene_stats(analysis, start, end)
    return bool((audio and audio["peak"] > 0) or (scene and scene["peak"] > 0))


def _describe_signals(analysis: VideoAnalysisInput, start: float, end: float) -> str:
    parts = []
    audio = audio_stats(analysis, start, end)
    if audio and audio["count"]:
        parts.append(f"audio mean={audio['mean']:.2f} peak={audio['peak']:.2f} p90={audio['p90']:.2f}")
    scene = scene_stats(analysis, start, end)
    if scene and scene["count"]:
        parts.append(f"scene_change peak={scene['peak']:.2f}")
    return "; ".join(parts) or "no audio or scene signal"


def select_diverse_candidates(
    windows: list[_CandidateWindow], limit: int = _MAX_CANDIDATES_TO_LLM, duration: float | None = None
) -> list[_CandidateWindow]:
    """Picks up to `limit` windows for the LLM, spread across the video instead of piled where the
    heuristics happen to peak:

    1. QUALITY slots (most of them): best heuristic score first, but never two windows overlapping and
       never more than a fair share from one section of the video (the video is cut into up to 8 equal
       sections).
    2. EXPLORATION slots (about 20%): the best remaining window from whichever section has the fewest
       picks so far, so stretches the heuristics ranked low still get looked at.
    3. If that still leaves slots (a short video has few non-overlapping windows), the best remaining
       windows, overlapping or not.

    Returned in time order."""
    if len(windows) <= limit:
        return sorted(windows, key=lambda w: w.start)
    duration = duration or max(w.end for w in windows)
    sections = max(1, min(_MAX_SECTIONS, int(duration // _MIN_SECTION_SECONDS)))

    def section_of(w: _CandidateWindow) -> int:
        return min(sections - 1, int(w.start / duration * sections))

    exploration = max(1, round(limit * _EXPLORATION_SHARE)) if limit >= 5 else 0
    quality = limit - exploration
    section_cap = max(2, math.ceil(quality * 2 / sections))
    ranked = sorted(windows, key=lambda w: (-w.heuristic_score, w.start))

    chosen: list[_CandidateWindow] = []
    chosen_ids: set[str] = set()
    per_section: Counter = Counter()

    def apart(w: _CandidateWindow) -> bool:
        return all(abs(w.start - c.start) >= _MIN_SEPARATION_SECONDS for c in chosen)

    def take(w: _CandidateWindow) -> None:
        chosen.append(w)
        chosen_ids.add(w.candidate_id)
        per_section[section_of(w)] += 1

    for w in ranked:
        if len(chosen) >= quality:
            break
        if per_section[section_of(w)] < section_cap and apart(w):
            take(w)

    while len(chosen) < limit:
        rest = [w for w in ranked if w.candidate_id not in chosen_ids and apart(w)]
        if not rest:
            break
        fewest = min(per_section[section_of(w)] for w in rest)
        take(next(w for w in rest if per_section[section_of(w)] == fewest))  # `rest` keeps score order

    for w in ranked:
        if len(chosen) >= limit:
            break
        if w.candidate_id not in chosen_ids:
            take(w)

    return sorted(chosen, key=lambda w: w.start)


def _overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Overlap as a share of the SHORTER range (0 = disjoint, 1 = one contains the other)."""
    overlap = min(a_end, b_end) - max(a_start, b_start)
    shorter = min(a_end - a_start, b_end - b_start)
    return max(0.0, overlap) / shorter if shorter > 0 else 0.0


# --------------------------------------------------------------------------- strategy

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
        """Named component scores, each 0-1. A component that can't be measured for this window (no
        transcript, no audio, scene analysis that stopped short) is LEFT OUT rather than reported as 0:
        a missing measurement is not a bad one, and combine_heuristics re-weights over what is there.
        Components must measure different things — a signal feeding two components is counted twice."""
        raise NotImplementedError

    @abstractmethod
    def build_system_prompt(self) -> str:
        raise NotImplementedError

    def heuristic_weights(self) -> dict[str, float]:
        """Weight per component name for combine_heuristics. Empty = every returned component equal."""
        return {}

    def allows_silent_windows(self) -> bool:
        """Whether a window with no transcript can still be a candidate if its audio/scene signal is
        usable (gameplay with no commentary). Spoken-content categories can't use one."""
        return False

    def combine_heuristics(self, scores: dict[str, float]) -> float:
        """Weighted mean of the components that are present, 0-1. Every value is clamped to 0-1 and a
        missing or non-finite one is skipped, so categories with different numbers of components rank on
        the same scale. Used to rank candidate windows — never as a clip's final quality score."""
        weights = self.heuristic_weights()
        total = weight_sum = 0.0
        for name, raw in scores.items():
            value = _finite_number(raw)
            weight = weights.get(name, 0.0) if weights else 1.0
            if value is None or weight <= 0:
                continue
            total += _clamp(value, 0.0, 1.0) * weight
            weight_sum += weight
        return total / weight_sum if weight_sum > 0 else 0.0

    def _generate_candidate_windows(self, analysis: VideoAnalysisInput) -> list[_CandidateWindow]:
        """Every 30 s window (stride 15 s) worth scoring, scored. Windows with no transcript are kept only
        for categories that can use them and only when there is an audio/scene signal to go on."""
        duration = _effective_duration(analysis)
        windows: list[_CandidateWindow] = []
        t = 0.0
        while t < duration:
            start, end = t, min(t + _WINDOW_SECONDS, duration)
            t += _STRIDE_SECONDS
            if end - start < _MIN_TAIL_WINDOW_SECONDS and windows:
                continue
            text = _segments_in_range(analysis, start, end)
            if not text and not (self.allows_silent_windows() and _has_signal(analysis, start, end)):
                continue
            scores = self.compute_heuristic_scores(analysis, text, start, end)
            windows.append(
                _CandidateWindow(
                    candidate_id=f"c{int(round(start))}",
                    start=start,
                    end=end,
                    text=text,
                    heuristic_scores=scores,
                    heuristic_score=self.combine_heuristics(scores),
                    signals=_describe_signals(analysis, start, end),
                )
            )
        return windows

    def find_highlights(self, analysis: VideoAnalysisInput, llm: LLMProvider) -> list[HighlightCandidate]:
        windows = self._generate_candidate_windows(analysis)
        if not windows:
            return []
        candidates = select_diverse_candidates(windows, _MAX_CANDIDATES_TO_LLM, _effective_duration(analysis))

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

    # ----------------------------------------------------------------- LLM scoring

    def _build_user_prompt(self, batch: list[_CandidateWindow], language: str | None) -> str:
        payload = [
            {
                "candidate_id": c.candidate_id,
                "start": round(c.start, 2),
                "end": round(c.end, 2),
                "has_transcript": c.has_transcript,
                "transcript": c.text[:800],
                "signals": c.signals,
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
        return (
            language_instruction
            + "Here are candidate windows from ONE PART of a video (other parts are scored separately). Each has "
            "a candidate_id, its start/end in seconds, a transcript excerpt (empty if nothing was said), "
            "'signals' and 'hint_scores'. 'signals' are audio and scene-change levels RELATIVE to the rest of "
            "this video — not absolute loudness, and a scene change is not necessarily exciting. 'hint_scores' "
            "are 0-1 heuristic hints, informational only, not something to repeat back.\n\n"
            "You may ONLY choose moments from these candidates, and you must identify each one by its "
            "candidate_id. Never invent a candidate_id, a moment or a timestamp. If a window's transcript is "
            "empty you can judge it only from its 'signals': say what they show (for example a sudden loud "
            "burst), never describe speech, gameplay or visuals that the transcript and signals do not "
            "establish, and give such a window a lower confidence.\n\n"
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
            "score them low rather than omitting them, unless they're pure filler with nothing usable). "
            "If NONE is worth clipping, return an empty list.\n\n"
            "'start' and 'end' are optional. Leave them out to use the whole window. If you give them they "
            "must lie inside that candidate's own window — they are clamped to it, and the application then "
            "aligns the clip to sentence boundaries itself, so do not try to be exact. Write 'reason' as "
            "one natural-language sentence, IN THE TRANSCRIPT'S OWN LANGUAGE, describing what actually "
            "happens in that moment. 'reason' "
            "must never be a field/variable name, a bare number, or the word 'score' — always a real "
            "sentence grounded in the transcript. Estimate your confidence (0-1).\n\n"
            "Every 'reason' must quote or closely paraphrase a specific word, phrase, or number that "
            "actually appears in THAT window's own transcript excerpt above — not a summary that could "
            "apply to any window in this batch. If you can't point to something in that specific "
            "excerpt that justifies the reason, the reason is too generic — go back and look at what "
            "that window's transcript actually says.\n\n"
            f"Candidates (JSON): {payload}\n\n"
            'Respond with JSON: {"highlights": [{"candidate_id": string (one of the ids above), '
            '"start": number (optional), "end": number (optional), "text": string, '
            '"score": number (0-100), "hook_score": number (0-100), "flow_score": number (0-100), '
            '"value_score": number (0-100), "trend_score": number (0-100), '
            '"reason": string (a full sentence), "confidence": number (0-1)}]}'
        )

    def _score_batch(
        self, batch: list[_CandidateWindow], llm: LLMProvider, language: str | None = None
    ) -> list[HighlightCandidate]:
        try:
            result = llm.complete_json(self.build_system_prompt(), self._build_user_prompt(batch, language))
        except Exception as exc:
            # ValueError = a reply that wasn't usable JSON; anything else = the request itself failed.
            reason = "bad_json" if isinstance(exc, ValueError) else "api_error"
            logger.warning(
                "highlight_llm_failed category=%s reason=%s error_type=%s candidates=%d; using heuristic scoring",
                self.category.value, reason, type(exc).__name__, len(batch),
            )
            return self._fallback_candidates(batch)

        # Three outcomes are different things: a reply that doesn't follow the schema (-> heuristic
        # fallback), a valid empty list (the model judged nothing here worth clipping — trusted), and a
        # list whose every entry fails validation (unusable -> heuristic fallback).
        raw = result.get("highlights") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            logger.warning(
                "highlight_llm_invalid_schema category=%s got=%s candidates=%d; using heuristic scoring",
                self.category.value, type(raw).__name__ if isinstance(result, dict) else type(result).__name__, len(batch),
            )
            return self._fallback_candidates(batch)
        if not raw:
            return []

        highlights, rejected = self._validate_highlights(raw, batch)
        logger.info(
            "highlight_batch_validated category=%s candidates=%d returned=%d accepted=%d rejected=%s",
            self.category.value, len(batch), len(raw), len(highlights), dict(rejected) or "none",
        )
        if not highlights:
            logger.warning(
                "highlight_llm_all_rejected category=%s candidates=%d; using heuristic scoring",
                self.category.value, len(batch),
            )
            return self._fallback_candidates(batch)
        return highlights

    @staticmethod
    def _infer_window(start: float, end: float, batch: list[_CandidateWindow]) -> _CandidateWindow | None:
        """Which candidate a reply WITHOUT a candidate_id means, judged by its timestamps: among the windows
        that contain at least 80% of the range, the one containing the most, then the one whose centre is
        nearest, then the earliest. Neighbouring windows overlap by half, so a short range can fit in two;
        either way the range ends up clamped inside the window it is attributed to, so the choice doesn't
        move the clip. Weaker models drop the id; anything that fits no window is not attributed."""
        length = end - start
        centre = (start + end) / 2
        fits = []
        for w in batch:
            contained = max(0.0, min(end, w.end) - max(start, w.start)) / length
            if contained >= 0.8:
                fits.append((-contained, abs(centre - (w.start + w.end) / 2), w.start, w))
        return min(fits, key=lambda f: f[:3])[3] if fits else None

    def _validate_highlights(
        self, raw_items: list, batch: list[_CandidateWindow]
    ) -> tuple[list[HighlightCandidate], Counter]:
        """Turns the model's entries into HighlightCandidates, rejecting what can't be trusted.

        Timestamp policy: the candidate_id decides which window a highlight belongs to, and a
        highlight can never leave its window. 'start'/'end' are optional; when present they must be
        finite numbers with start < end and at least half of that range must lie inside the window
        (otherwise the entry is rejected), and they are then CLAMPED to the window. Without them the
        window's own bounds are used. Downstream, highlight_service aligns the range to transcript
        segment boundaries, so the model never controls a cut point beyond its candidate."""
        by_id = {c.candidate_id: c for c in batch}
        rejected: Counter = Counter()
        accepted: dict[str, HighlightCandidate] = {}

        for item in raw_items:
            if not isinstance(item, dict):
                rejected["not_an_object"] += 1
                continue

            raw_start, raw_end = item.get("start"), item.get("end")
            has_range = raw_start is not None or raw_end is not None
            start = end = None
            if has_range:
                start, end = _finite_number(raw_start), _finite_number(raw_end)
                if start is None or end is None or start >= end:
                    rejected["bad_timestamps"] += 1
                    continue

            candidate_id = item.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id.strip():
                window = by_id.get(candidate_id.strip())
                if window is None:
                    rejected["unknown_candidate_id"] += 1
                    continue
            elif candidate_id is None and has_range:
                window = self._infer_window(start, end, batch)
                if window is None:
                    rejected["no_candidate_id"] += 1
                    continue
            else:
                rejected["no_candidate_id" if candidate_id is None else "unknown_candidate_id"] += 1
                continue

            if has_range:
                if max(0.0, min(end, window.end) - max(start, window.start)) / (end - start) < _MIN_CLAIMED_OVERLAP:
                    rejected["outside_candidate_window"] += 1
                    continue
                start, end = max(start, window.start), min(end, window.end)
            else:
                start, end = window.start, window.end
            if end - start < _MIN_HIGHLIGHT_SECONDS:
                rejected["too_short"] += 1
                continue

            score = _finite_number(item.get("score"))
            if score is None:
                rejected["bad_score"] += 1
                continue
            confidence = _finite_number(item.get("confidence"))

            def sub_score(key: str) -> float | None:
                value = _finite_number(item.get(key))
                return None if value is None else _clamp(value, 0.0, 100.0)

            text = item.get("text")
            text = text[:500] if isinstance(text, str) else ""
            reason = item.get("reason")
            reason = reason if isinstance(reason, str) else ""
            # Smaller/local models occasionally echo a hint-dict key (e.g. "emotion_score")
            # back as "reason" instead of writing a sentence — degrade to the other field,
            # or a generic-but-honest fallback, rather than surfacing that to users verbatim.
            if not _is_usable_text(reason):
                reason = text if _is_usable_text(text) else "AI-selected highlight."
            if not _is_usable_text(text):
                text = reason

            highlight = HighlightCandidate(
                start=start,
                end=end,
                text=text,
                score=_clamp(score, 0.0, 100.0),
                reason=reason,
                category=self.category,
                confidence=0.5 if confidence is None else _clamp(confidence, 0.0, 1.0),
                hook_score=sub_score("hook_score"),
                flow_score=sub_score("flow_score"),
                value_score=sub_score("value_score"),
                trend_score=sub_score("trend_score"),
            )
            previous = accepted.get(window.candidate_id)
            if previous is not None:
                rejected["duplicate_candidate"] += 1
                if previous.score >= highlight.score:
                    continue
            accepted[window.candidate_id] = highlight

        # Two different candidates can still describe the same stretch (neighbouring windows overlap).
        kept: list[HighlightCandidate] = []
        for h in sorted(accepted.values(), key=lambda h: -h.score):
            if any(_overlap_ratio(h.start, h.end, k.start, k.end) > _DUPLICATE_OVERLAP for k in kept):
                rejected["overlaps_better_highlight"] += 1
                continue
            kept.append(h)
        return kept, rejected

    def _fallback_candidates(self, batch: list[_CandidateWindow]) -> list[HighlightCandidate]:
        """Heuristic-only highlights for a batch whose LLM evaluation isn't available: the best-ranked
        windows with their normalized heuristic score (0-100) and a confidence below any the LLM
        reports. They carry the window's own bounds and go through the same range checks as the
        model's entries, then through the same final selection."""
        ranked = sorted(batch, key=lambda c: (-c.heuristic_score, c.start))[:_FALLBACK_MAX_PER_BATCH]
        out: list[HighlightCandidate] = []
        for c in ranked:
            if not (math.isfinite(c.start) and math.isfinite(c.end)) or c.end - c.start < _MIN_HIGHLIGHT_SECONDS:
                continue
            out.append(
                HighlightCandidate(
                    start=c.start,
                    end=c.end,
                    text=c.text[:500] or _FALLBACK_REASON,
                    score=_clamp(c.heuristic_score * 100, 0.0, 100.0),
                    reason=_FALLBACK_REASON,
                    category=self.category,
                    confidence=FALLBACK_CONFIDENCE,
                )
            )
        return out


__all__ = [
    "VideoCategoryStrategy", "HighlightCandidate", "average_in_range", "_keyword_score",
    "cue_score", "distinct_cues", "window_segments", "select_diverse_candidates", "FALLBACK_CONFIDENCE",
]
