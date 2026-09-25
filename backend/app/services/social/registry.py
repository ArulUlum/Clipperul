from app.models.enums import SocialProvider
from app.services.social.instagram import InstagramProvider
from app.services.social.provider import SocialMediaProvider
from app.services.social.tiktok import TikTokProvider
from app.services.social.youtube import YouTubeProvider

_PROVIDERS: dict[SocialProvider, type[SocialMediaProvider]] = {
    SocialProvider.YOUTUBE: YouTubeProvider,
    SocialProvider.TIKTOK: TikTokProvider,
    SocialProvider.INSTAGRAM: InstagramProvider,
}


def get_social_provider(provider: SocialProvider) -> SocialMediaProvider:
    provider_cls = _PROVIDERS.get(provider)
    if provider_cls is None:
        raise ValueError(f"No social provider registered for: {provider}")
    return provider_cls()
