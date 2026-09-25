from app.models.enums import VideoCategory
from app.services.ai.categories.gaming import GamingCategoryStrategy
from app.services.ai.categories.podcast import PodcastCategoryStrategy
from app.services.ai.categories.strategy import VideoCategoryStrategy

_STRATEGIES: dict[VideoCategory, type[VideoCategoryStrategy]] = {
    VideoCategory.GAMING: GamingCategoryStrategy,
    VideoCategory.PODCAST: PodcastCategoryStrategy,
}


def get_strategy(category: VideoCategory) -> VideoCategoryStrategy:
    """Adding a new category = one new VideoCategoryStrategy subclass + one line here."""
    strategy_cls = _STRATEGIES.get(category)
    if strategy_cls is None:
        raise ValueError(f"No highlight strategy registered for category: {category}")
    return strategy_cls()


def list_available_categories() -> list[VideoCategory]:
    return list(_STRATEGIES.keys())
