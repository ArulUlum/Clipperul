from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import settings


@dataclass
class TranscriptSegmentResult:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    language: str | None
    full_text: str
    segments: list[TranscriptSegmentResult]


class TranscriptionProvider(ABC):
    """Speech-to-text abstraction so the STT engine can be swapped without touching callers."""

    @abstractmethod
    def transcribe(self, audio_path: str) -> TranscriptionResult:
        raise NotImplementedError


class WhisperLocalProvider(TranscriptionProvider):
    """Runs OpenAI Whisper locally via faster-whisper (CTranslate2) inside the worker container."""

    def __init__(self) -> None:
        self._model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                settings.WHISPER_MODEL,
                device=settings.WHISPER_DEVICE,
                compute_type=settings.WHISPER_COMPUTE_TYPE,
            )
        return self._model

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        model = self._get_model()
        segments_iter, info = model.transcribe(audio_path, vad_filter=True)

        segments: list[TranscriptSegmentResult] = []
        full_text_parts: list[str] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if not text:
                continue
            segments.append(TranscriptSegmentResult(start=seg.start, end=seg.end, text=text))
            full_text_parts.append(text)

        return TranscriptionResult(
            language=getattr(info, "language", None),
            full_text=" ".join(full_text_parts),
            segments=segments,
        )


def get_transcription_provider() -> TranscriptionProvider:
    return WhisperLocalProvider()
