from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

import requests

from app.core.config import settings
from app.services.social.provider import AccountInfo, PublishResult, SocialMediaProvider

_AUTH_URL = "https://www.instagram.com/oauth/authorize"
# Short-lived token exchange — this is the only step that isn't graph.instagram.com; Meta
# splits the initial code->token exchange onto api.instagram.com specifically.
_SHORT_LIVED_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_EXCHANGE_URL = "https://graph.instagram.com/access_token"
_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
_ME_URL = "https://graph.instagram.com/me"
# "Instagram API with Instagram Login" — lets a professional (business/creator) account
# authenticate directly at instagram.com, no linked Facebook Page required (unlike the older
# Facebook-Login-based Instagram Graph API flow this replaces).
_SCOPES = "instagram_business_basic,instagram_business_content_publish"
_REQUEST_TIMEOUT = 30


class InstagramProvider(SocialMediaProvider):
    """Instagram Reels adapter — OAuth via Meta's "Instagram API with Instagram Login". Built
    from Meta's documented REST surface (https://developers.facebook.com/docs/instagram-platform/
    instagram-api-with-instagram-login); double-check request/response shapes there against the
    first real connect attempt, same caveat as the from-scratch TikTok adapter."""

    def _redirect_uri(self) -> str:
        return f"{settings.BACKEND_BASE_URL}{settings.API_PREFIX}/social-accounts/instagram/callback"

    def get_authorization_url(self, state: str) -> str:
        params = {
            "client_id": settings.INSTAGRAM_APP_ID,
            "redirect_uri": self._redirect_uri(),
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def _fetch_profile(self, access_token: str) -> dict:
        response = requests.get(
            _ME_URL,
            params={"fields": "id,username,account_type", "access_token": access_token},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def connect(self, authorization_code: str, state: str) -> AccountInfo:
        # Step 1: exchange the authorization code for a short-lived (~1hr) access token.
        short_lived_response = requests.post(
            _SHORT_LIVED_TOKEN_URL,
            data={
                "client_id": settings.INSTAGRAM_APP_ID,
                "client_secret": settings.INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": self._redirect_uri(),
                "code": authorization_code,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        short_lived_response.raise_for_status()
        short_lived_token = short_lived_response.json()["access_token"]

        # Step 2: immediately exchange it for a long-lived (~60 day) token — the short-lived
        # one is too short-lived to be useful for anything we'd store.
        exchange_response = requests.get(
            _EXCHANGE_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.INSTAGRAM_APP_SECRET,
                "access_token": short_lived_token,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        exchange_response.raise_for_status()
        exchange_data = exchange_response.json()
        long_lived_token = exchange_data["access_token"]
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=exchange_data.get("expires_in", 5_184_000))

        profile = self._fetch_profile(long_lived_token)

        return AccountInfo(
            external_account_id=profile["id"],
            account_name=profile.get("username", profile["id"]),
            access_token=long_lived_token,
            # Instagram has no separate refresh token — the long-lived access token refreshes
            # itself in place via ig_refresh_token (see refresh_token_if_needed below).
            refresh_token=None,
            expires_at=expires_at,
        )

    def refresh_token_if_needed(self, social_account_id: UUID) -> None:
        from app.core.db import SessionLocal
        from app.core.security import decrypt_token, encrypt_token
        from app.models.social_account import SocialAccount

        db = SessionLocal()
        try:
            account = db.get(SocialAccount, social_account_id)
            if account is None:
                return
            # Wider buffer than YouTube/TikTok's 5 minutes: long-lived tokens last ~60 days,
            # and Meta only allows a refresh once the token is itself at least 24h old, so
            # there's no benefit to cutting this close.
            if account.token_expires_at and account.token_expires_at > datetime.now(timezone.utc) + timedelta(days=1):
                return
            if not account.encrypted_access_token:
                return

            response = requests.get(
                _REFRESH_URL,
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": decrypt_token(account.encrypted_access_token),
                },
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            account.encrypted_access_token = encrypt_token(data["access_token"])
            account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 5_184_000))
            db.commit()
        finally:
            db.close()

    def get_account_info(self, social_account_id: UUID) -> AccountInfo:
        from app.core.db import SessionLocal
        from app.core.security import decrypt_token
        from app.models.social_account import SocialAccount

        self.refresh_token_if_needed(social_account_id)

        db = SessionLocal()
        try:
            account = db.get(SocialAccount, social_account_id)
            if account is None:
                raise ValueError(f"Social account {social_account_id} not found")
            profile = self._fetch_profile(decrypt_token(account.encrypted_access_token))
            return AccountInfo(
                external_account_id=profile["id"], account_name=profile.get("username", profile["id"])
            )
        finally:
            db.close()

    def publish_video(self, social_account_id: UUID, video_path: str, title: str, caption: str) -> PublishResult:
        raise NotImplementedError(
            "Instagram publishing needs the instagram_business_content_publish permission to "
            "pass Meta's App Review (Advanced Access) — implemented in Phase 4"
        )

    def schedule_video(
        self, social_account_id: UUID, video_path: str, title: str, caption: str, publish_at
    ) -> PublishResult:
        raise NotImplementedError("Instagram scheduling is implemented in Phase 4")
