from dataclasses import dataclass, field

from app.services.ai.transcription import TranscriptSegmentResult


@dataclass
class AudioEnergyPoint:
    time: float
    # RMS of the 1 s window starting at `time`, divided by the loudest window in the SAME video:
    # 0-1 and relative within the video — not absolute loudness.
    energy: float


@dataclass
class SceneChangePoint:
    time: float
    # How much the frame at `time` differs from the previous sample, relative to this video's own
    # typical frame-to-frame change (0 = ordinary motion, 1 = the biggest cut). 0-1.
    magnitude: float


@dataclass
class VideoAnalysisInput:
    video_duration: float
    transcript_segments: list[TranscriptSegmentResult] = field(default_factory=list)
    audio_energy: list[AudioEnergyPoint] = field(default_factory=list)
    scene_changes: list[SceneChangePoint] = field(default_factory=list)
    # ISO 639-1-ish code from Whisper's own language detection (e.g. "id", "en") — None if
    # detection failed. Threaded through to the highlight-scoring LLM prompt so clip
    # titles/captions (derived from 'reason') come out in the source video's language instead
    # of defaulting to English.
    language: str | None = None
    # Scene analysis can stop early (its time budget ran out) or fail. Beyond `scene_analyzed_until`
    # the scene signal is UNKNOWN, not "no change" — strategies leave it out of the score there.
    # The defaults describe callers that never set them: a complete analysis.
    scene_analysis_complete: bool = True
    scene_analyzed_until: float | None = None
    # Lazily built lookup structures for the signal lists above (see analysis.signal_index). The lists
    # are treated as immutable once analysis starts.
    _signal_cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)
