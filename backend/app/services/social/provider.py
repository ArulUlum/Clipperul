from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

# Called with the upload's completion percentage (0-100) as bytes reach the platform.
ProgressCallback = Callable[[float], None]


@dataclass
class AccountInfo:
    external_account_id: str
    account_name: str
    # Raw (unencrypted) tokens from the OAuth exchange — the caller (API layer) is
    # responsible for encrypting via app.core.security before persisting. Kept off this
    # dataclass's constructor-required fields since get_account_info() (a re-fetch using an
    # already-stored, already-encrypted token) has no new tokens to report.
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: datetime | None = None


@dataclass
class PublishResult:
    external_post_id: str
    url: str | None = None


class SocialMediaProvider(ABC):
    """Adapter interface each platform implements. Publishing/scheduler code depends only
    on this interface, never on a specific platform's SDK — implemented in Phase 3."""

    # Whether publish_video is really implemented. Automation (e.g. the daily auto-clip) skips
    # providers where this is False instead of queueing posts that can only fail.
    can_publish: bool = False

    @abstractmethod
    def get_authorization_url(self, state: str) -> str:
        """Builds the platform's OAuth consent-screen URL the browser should be sent to.
        `state` is an opaque, server-verifiable token (see app.core.security.create_oauth_state)
        that the platform echoes back on the callback — how the callback recovers which user
        initiated the connection, since the callback request itself carries no auth header."""
        raise NotImplementedError

    @abstractmethod
    def connect(self, authorization_code: str, state: str) -> AccountInfo:
        """`state` is the same value passed to get_authorization_url() for this flow — most
        providers can ignore it, but one whose token exchange needs PKCE (TikTok) uses it to
        deterministically re-derive the code_verifier it committed to at authorization time."""
        raise NotImplementedError

    @abstractmethod
    def refresh_token_if_needed(self, social_account_id: UUID) -> None:
        raise NotImplementedError

    @abstractmethod
    def publish_video(
        self,
        social_account_id: UUID,
        video_path: str,
        title: str,
        caption: str,
        on_progress: ProgressCallback | None = None,
    ) -> PublishResult:
        """`on_progress`, if given, is called as the upload advances — adapters that can't
        observe their own upload progress may simply never call it."""
        raise NotImplementedError

    @abstractmethod
    def schedule_video(
        self, social_account_id: UUID, video_path: str, title: str, caption: str, publish_at
    ) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def get_account_info(self, social_account_id: UUID) -> AccountInfo:
        raise NotImplementedError
