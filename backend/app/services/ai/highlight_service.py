from dataclasses import dataclass

from app.core.config import settings
from app.models.enums import VideoCategory
from app.services.ai.categories.registry import get_strategy
from app.services.ai.llm import get_llm_provider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import VideoAnalysisInput


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


def _apply_padding(start: float, end: float, video_duration: float) -> tuple[float, float]:
    padding = settings.CLIP_PADDING_SECONDS
    start = max(0.0, start - padding)
    end = min(video_duration, end + padding)
    duration = end - start
    if duration < settings.MIN_CLIP_DURATION_SECONDS:
        deficit = settings.MIN_CLIP_DURATION_SECONDS - duration
        start = max(0.0, start - deficit / 2)
        end = min(video_duration, end + deficit / 2)
    if end - start > settings.MAX_CLIP_DURATION_SECONDS:
        end = start + settings.MAX_CLIP_DURATION_SECONDS
    return start, end


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def find_and_select_highlights(analysis: VideoAnalysisInput, category: VideoCategory) -> list[SelectedHighlight]:
    strategy = get_strategy(category)
    llm = get_llm_provider()
    candidates = strategy.find_highlights(analysis, llm)
    candidates.sort(key=lambda c: c.score, reverse=True)

    selected: list[SelectedHighlight] = []
    chosen_ranges: list[tuple[float, float]] = []

    for candidate in candidates:
        if len(selected) >= settings.MAX_CLIPS_PER_VIDEO:
            break
        if candidate.score < settings.MIN_HIGHLIGHT_SCORE:
            continue
        snapped_start, snapped_end = _snap_to_segment_boundaries(
            candidate.start, candidate.end, analysis.transcript_segments
        )
        padded_start, padded_end = _apply_padding(snapped_start, snapped_end, analysis.video_duration)
        if padded_end - padded_start < settings.MIN_CLIP_DURATION_SECONDS:
            continue
        if any(_overlaps((padded_start, padded_end), r) for r in chosen_ranges):
            continue
        chosen_ranges.append((padded_start, padded_end))
        selected.append(
            SelectedHighlight(
                start=padded_start,
                end=padded_end,
                raw_start=candidate.start,
                raw_end=candidate.end,
                text=candidate.text,
                score=candidate.score,
                reason=candidate.reason,
                category=candidate.category,
                confidence=candidate.confidence,
                hook_score=candidate.hook_score,
                flow_score=candidate.flow_score,
                value_score=candidate.value_score,
                trend_score=candidate.trend_score,
            )
        )

    return selected
