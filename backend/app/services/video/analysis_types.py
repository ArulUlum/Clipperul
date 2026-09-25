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
