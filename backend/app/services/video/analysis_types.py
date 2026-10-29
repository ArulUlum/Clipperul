from dataclasses import dataclass, field

from app.services.ai.transcription import TranscriptSegmentResult


@dataclass
class AudioEnergyPoint:
    time: float
    energy: float  # normalized 0-1


@dataclass
class SceneChangePoint:
    time: float
    magnitude: float  # normalized 0-1


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
