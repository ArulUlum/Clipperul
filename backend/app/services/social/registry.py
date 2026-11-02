from app.models.enums import SocialProvider
from app.services.social.instagram import InstagramProvider
from app.services.social.provider import SocialMediaProvider
from app.services.social.tiktok import TikTokProvider
from app.services.social.youtube import YouTubeProvider
from app.services.social.zernio import BROKER as ZERNIO_BROKER
from app.services.social.zernio import ZernioProvider

_PROVIDERS: dict[SocialProvider, type[SocialMediaProvider]] = {
    SocialProvider.YOUTUBE: YouTubeProvider,
    SocialProvider.TIKTOK: TikTokProvider,
    SocialProvider.INSTAGRAM: InstagramProvider,
}


def get_social_provider(provider: SocialProvider, broker: str | None = None) -> SocialMediaProvider:
    """The adapter that talks to the platform for an account: `broker` is SocialAccount.broker —
    a brokered account is handled by its broker whatever the platform, otherwise it's the
    platform's own adapter."""
    if broker == ZERNIO_BROKER:
        return ZernioProvider()
    provider_cls = _PROVIDERS.get(provider)
    if provider_cls is None:
        raise ValueError(f"No social provider registered for: {provider}")
    return provider_cls()
