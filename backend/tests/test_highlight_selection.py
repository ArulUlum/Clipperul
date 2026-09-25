from app.models.enums import VideoCategory
from app.services.ai.highlight_service import find_and_select_highlights
from app.services.ai.llm import LLMProvider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput


class _FakeLLMProvider(LLMProvider):
    def __init__(self, highlights: list[dict]):
        self.highlights = highlights

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {"highlights": self.highlights}


def _long_analysis() -> VideoAnalysisInput:
    # 3 minutes of speech spread across the video, long enough for multiple candidate windows.
    segments = [
        TranscriptSegmentResult(start=t, end=t + 8.0, text=f"Moment {i} happens here, quite a statement.")
        for i, t in enumerate(range(0, 180, 20))
    ]
    return VideoAnalysisInput(
        video_duration=180.0,
        transcript_segments=segments,
        audio_energy=[AudioEnergyPoint(time=t, energy=0.6) for t in range(0, 180, 2)],
        scene_changes=[SceneChangePoint(time=t, magnitude=0.5) for t in range(0, 180, 2)],
    )


def test_low_score_highlights_are_filtered_out(mocker):
    mocker.patch(
        "app.services.ai.highlight_service.get_llm_provider",
        return_value=_FakeLLMProvider(
            [
                {"start": 10.0, "end": 25.0, "text": "Strong moment.", "score": 85, "reason": "A strong moment.", "confidence": 0.9},
                {"start": 60.0, "end": 75.0, "text": "Weak filler.", "score": 20, "reason": "Weak filler content.", "confidence": 0.3},
            ]
        ),
    )

    selected = find_and_select_highlights(_long_analysis(), VideoCategory.GAMING)

    assert len(selected) == 1
    assert selected[0].score == 85


def test_multiple_distinct_highlights_are_all_kept(mocker):
    mocker.patch(
        "app.services.ai.highlight_service.get_llm_provider",
        return_value=_FakeLLMProvider(
            [
                {"start": 10.0, "end": 25.0, "text": "First moment.", "score": 90, "reason": "First distinct moment.", "confidence": 0.9},
                {"start": 60.0, "end": 75.0, "text": "Second moment.", "score": 80, "reason": "Second distinct moment.", "confidence": 0.8},
                {"start": 120.0, "end": 135.0, "text": "Third moment.", "score": 70, "reason": "Third distinct moment.", "confidence": 0.7},
            ]
        ),
    )

    selected = find_and_select_highlights(_long_analysis(), VideoCategory.PODCAST)

    assert len(selected) == 3
    assert {round(s.score) for s in selected} == {90, 80, 70}
