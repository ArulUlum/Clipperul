from app.models.enums import VideoCategory
from app.services.ai.category_detection import detect_video_category
from app.services.ai.llm import LLMProvider


class _FakeLLMProvider(LLMProvider):
    def __init__(self, response: dict | None = None, raise_error: bool = False):
        self.response = response
        self.raise_error = raise_error

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if self.raise_error:
            raise RuntimeError("simulated LLM failure")
        return self.response or {}


_GAMING_TRANSCRIPT = (
    "Oh my god, clutch! No way! That was an insane comeback kill, GG everyone, "
    "what a rare achievement unlocked right there."
)
_PODCAST_TRANSCRIPT = (
    "Honestly, in my opinion, here's the thing nobody talks about — I remember when "
    "I made the biggest mistake of my career, and the reason is surprisingly simple."
)


def test_detect_video_category_uses_llm_response():
    llm = _FakeLLMProvider(response={"category": "podcast", "confidence": 0.9})
    assert detect_video_category("some transcript text", llm) == VideoCategory.PODCAST


def test_detect_video_category_falls_back_to_keywords_on_llm_error():
    llm = _FakeLLMProvider(raise_error=True)
    assert detect_video_category(_GAMING_TRANSCRIPT, llm) == VideoCategory.GAMING
    assert detect_video_category(_PODCAST_TRANSCRIPT, llm) == VideoCategory.PODCAST


def test_detect_video_category_falls_back_to_keywords_on_unrecognized_value():
    llm = _FakeLLMProvider(response={"category": "cooking", "confidence": 0.5})
    assert detect_video_category(_GAMING_TRANSCRIPT, llm) == VideoCategory.GAMING
