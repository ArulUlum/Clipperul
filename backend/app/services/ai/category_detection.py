import logging

from app.models.enums import VideoCategory
from app.services.ai.categories.registry import get_strategy, list_available_categories
from app.services.ai.categories.strategy import _keyword_score
from app.services.ai.llm import LLMProvider

logger = logging.getLogger(__name__)

_TRANSCRIPT_SAMPLE_CHARS = 3000


def _keyword_fallback_category(transcript_text: str, available: list[VideoCategory]) -> VideoCategory:
    """Used when the LLM call fails or returns something unusable — picks whichever
    category's keyword list matches the transcript most, so detection never dead-ends."""
    best_category = available[0]
    best_score = -1.0
    for category in available:
        score = _keyword_score(transcript_text, get_strategy(category).keyword_list())
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


def detect_video_category(transcript_text: str, llm: LLMProvider) -> VideoCategory:
    """Classifies a video into one of the registered VideoCategoryStrategy categories using
    the transcript alone. Pulls the option list from the strategy registry (not hardcoded),
    so a newly registered category is automatically a candidate here too."""
    available = list_available_categories()
    if not available:
        raise ValueError("No video categories are registered")
    if len(available) == 1:
        return available[0]

    option_values = [c.value for c in available]
    try:
        system_prompt = (
            "You classify video transcripts into a content category for a short-form video "
            "clipping tool. Base the decision on subject matter, vocabulary, and tone."
        )
        user_prompt = (
            f"Choose exactly one category from this list: {', '.join(option_values)}.\n\n"
            f"Transcript excerpt:\n{transcript_text[:_TRANSCRIPT_SAMPLE_CHARS]}\n\n"
            'Respond with JSON: {"category": string (one of the listed options), '
            '"confidence": number (0-1)}'
        )
        result = llm.complete_json(system_prompt, user_prompt)
        detected_value = str(result.get("category", "")).strip().lower()
        for category in available:
            if category.value == detected_value:
                return category
        logger.warning("Category detection returned an unrecognized value: %r", detected_value)
    except Exception:
        logger.exception("AI category detection failed; falling back to keyword heuristic")

    return _keyword_fallback_category(transcript_text, available)
