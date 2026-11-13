import logging
import math
from dataclasses import dataclass

from app.core.config import settings
from app.models.enums import VideoCategory
from app.services.ai.categories.registry import get_strategy
from app.services.ai.categories.strategy import HighlightCandidate, _effective_duration, _overlap_ratio
from app.services.ai.llm import get_llm_provider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import VideoAnalysisInput

logger = logging.getLogger(__name__)

# Selected clips never overlap: even a second or two of repeated footage between neighbouring clips looks
# like a glitch. (Ranges that only touch are fine.)
_MAX_CLIP_OVERLAP = 0.0


@dataclass
class SelectedHighlight:
    start: float  # padded, clamped
    end: float  # padded, clamped
    raw_start: float
    raw_end: float
    text: str
    score: float
    reason: str
    category: VideoCategory
    confidence: float
    hook_score: float | None = None
    flow_score: float | None = None
    value_score: float | None = None
    trend_score: float | None = None


def _snap_to_segment_boundaries(
    start: float, end: float, segments: list[TranscriptSegmentResult]
) -> tuple[float, float]:
    """Nudges start/end out to the nearest transcript segment boundaries so a clip begins and
    ends at a natural sentence/phrase break instead of cutting mid-sentence — the concrete,
    algorithmic half of 'flow' (the LLM is also asked to prefer natural boundaries itself,
    but its raw start/end numbers are estimates, not guaranteed to land exactly on one)."""
    if not segments:
        return start, end

    snapped_start = start
    for seg in segments:
        if seg.end > start:
            snapped_start = min(start, seg.start)
            break

    snapped_end = end
    for seg in reversed(segments):
        if seg.start < end:
            snapped_end = max(end, seg.end)
            break

    if snapped_end <= snapped_start or (snapped_end - snapped_start) > (end - start) + 20.0:
        # Snapping produced something degenerate or blew up the range — not worth the risk.
        return start, end
    return snapped_start, snapped_end


def _apply_padding(
    start: float, end: float, video_duration: float, min_duration: float | None = None
) -> tuple[float, float]:
    """Pads both sides, stretches to the minimum duration, caps at the maximum — all inside the video.
    When one side has no room left (the clip is at the start or end of the video) the other side
    takes the rest of the stretch. A non-positive `video_duration` means the duration is unknown: the
    end is then not clamped."""
    padding = settings.CLIP_PADDING_SECONDS
    upper = video_duration if video_duration and video_duration > 0 else end + padding + settings.MAX_CLIP_DURATION_SECONDS
    start = max(0.0, start - padding)
    end = min(upper, end + padding)

    minimum = settings.MIN_CLIP_DURATION_SECONDS if min_duration is None else min_duration
    deficit = minimum - (end - start)
    if deficit > 0:
        grow_start = min(start, deficit / 2)
        grow_end = min(upper - end, deficit - grow_start)
        grow_start = min(start, deficit - grow_end)
        start -= grow_start
        end += grow_end

    if end - start > settings.MAX_CLIP_DURATION_SECONDS:
        end = start + settings.MAX_CLIP_DURATION_SECONDS
    return start, end


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _final_range(
    candidate: HighlightCandidate, analysis: VideoAnalysisInput, duration: float
) -> tuple[float, float] | None:
    """The clip's final (start, end) for a candidate, or None if it can't be a valid clip: the range is
    validated against the video, aligned to transcript segment boundaries, padded, and held to the
    configured minimum and maximum duration."""
    if not (math.isfinite(candidate.start) and math.isfinite(candidate.end)):
        return None
    start = max(0.0, candidate.start)
    end = candidate.end if duration <= 0 else min(candidate.end, duration)
    start = min(start, end)
    if end <= start:
        return None

    # A video shorter than the minimum clip can only ever yield a clip as long as itself.
    minimum = settings.MIN_CLIP_DURATION_SECONDS if duration <= 0 else min(settings.MIN_CLIP_DURATION_SECONDS, duration)
    snapped_start, snapped_end = _snap_to_segment_boundaries(start, end, analysis.transcript_segments)
    start, end = _apply_padding(snapped_start, snapped_end, duration, min_duration=minimum)
    if duration > 0:  # snapping and padding never leave the video
        start, end = max(0.0, start), min(duration, end)
    length = end - start
    if length < minimum - 1e-6 or length > settings.MAX_CLIP_DURATION_SECONDS + 1e-6:
        return None
    return start, end


def select_highlights(candidates: list[HighlightCandidate], analysis: VideoAnalysisInput) -> list[SelectedHighlight]:
    """The final clips from scored candidates.

    Candidates below MIN_HIGHLIGHT_SCORE, with an invalid range or too short/long once aligned and
    padded, are dropped. The rest are chosen best-first, but each already-chosen clip near a candidate
    (within HIGHLIGHT_DIVERSITY_RADIUS_SECONDS, centre to centre) lowers its rank by
    HIGHLIGHT_DIVERSITY_PENALTY points, so a strong cluster doesn't crowd out good moments elsewhere in the
    video; a clip overlapping one already chosen is skipped. At most MAX_CLIPS_PER_VIDEO. The result is
    ordered by score."""
    duration = _effective_duration(analysis)

    eligible: list[tuple[HighlightCandidate, tuple[float, float]]] = []
    for candidate in candidates:
        if not math.isfinite(candidate.score) or candidate.score < settings.MIN_HIGHLIGHT_SCORE:
            continue
        final = _final_range(candidate, analysis, duration)
        if final is not None:
            eligible.append((candidate, final))
    eligible.sort(key=lambda item: -item[0].score)

    def rank(item, chosen) -> float:
        centre = sum(item[1]) / 2
        nearby = sum(1 for _, r in chosen if abs(centre - sum(r) / 2) < settings.HIGHLIGHT_DIVERSITY_RADIUS_SECONDS)
        return item[0].score - settings.HIGHLIGHT_DIVERSITY_PENALTY * nearby

    chosen: list[tuple[HighlightCandidate, tuple[float, float]]] = []
    pool = list(eligible)
    while pool and len(chosen) < settings.MAX_CLIPS_PER_VIDEO:
        best = max(pool, key=lambda item: rank(item, chosen))  # ties keep score order
        pool.remove(best)
        if any(_overlap_ratio(*best[1], *r) > _MAX_CLIP_OVERLAP for _, r in chosen):
            continue
        chosen.append(best)

    chosen.sort(key=lambda item: -item[0].score)
    logger.info(
        "highlights_selected candidates=%d eligible=%d selected=%d", len(candidates), len(eligible), len(chosen)
    )
    return [
        SelectedHighlight(
            start=start,
            end=end,
            raw_start=c.start,
            raw_end=c.end,
            text=c.text,
            score=c.score,
            reason=c.reason,
            category=c.category,
            confidence=c.confidence,
            hook_score=c.hook_score,
            flow_score=c.flow_score,
            value_score=c.value_score,
            trend_score=c.trend_score,
        )
        for c, (start, end) in chosen
    ]


def find_and_select_highlights(analysis: VideoAnalysisInput, category: VideoCategory) -> list[SelectedHighlight]:
    strategy = get_strategy(category)
    llm = get_llm_provider()
    candidates = strategy.find_highlights(analysis, llm)
    return select_highlights(candidates, analysis)
