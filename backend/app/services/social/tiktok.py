from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

import requests

from app.core.config import settings
from app.core.security import derive_pkce_verifier, pkce_code_challenge
from app.services.social.provider import AccountInfo, PublishResult, SocialMediaProvider

_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
# video.publish requires the separate "Content Posting API" product to be added (and enabled
# for sandbox target users) in the TikTok Developer Portal — requesting a scope the app isn't
# provisioned for makes TikTok reject the *entire* authorization request with a generic
# "scope" error, so keep this to what's actually enabled. Add "video.publish" back once that
# product is active; it isn't used until Phase 4 wires up publishing anyway.
_SCOPES = "user.info.basic"
_REQUEST_TIMEOUT = 30


class TikTokProvider(SocialMediaProvider):
    """TikTok adapter — OAuth via TikTok's v2 Login Kit, which (unlike Google's) requires
    PKCE on the authorization-code exchange. Built from TikTok's documented v2 API surface;
    since it hasn't been exercised against a real TikTok app yet, double-check the request/
    response shapes below against https://developers.tiktok.com/doc/login-kit-web if the
    first real connect attempt doesn't behave as expected."""

    def _redirect_uri(self) -> str:
        return f"{settings.BACKEND_BASE_URL}{settings.API_PREFIX}/social-accounts/tiktok/callback"

    def get_authorization_url(self, state: str) -> str:
        code_verifier = derive_pkce_verifier(state)
        params = {
            "client_key": settings.TIKTOK_CLIENT_KEY,
            "response_type": "code",
            "scope": _SCOPES,
            "redirect_uri": self._redirect_uri(),
            "state": state,
            "code_challenge": pkce_code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def _fetch_user_info(self, access_token: str) -> dict:
        response = requests.get(
            _USER_INFO_URL,
            params={"fields": "open_id,display_name"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["data"]["user"]

    def connect(self, authorization_code: str, state: str) -> AccountInfo:
        token_response = requests.post(
            _TOKEN_URL,
            data={
                "client_key": settings.TIKTOK_CLIENT_KEY,
                "client_secret": settings.TIKTOK_CLIENT_SECRET,
                "code": authorization_code,
                "grant_type": "authorization_code",
                "redirect_uri": self._redirect_uri(),
                "code_verifier": derive_pkce_verifier(state),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_REQUEST_TIMEOUT,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data["access_token"]
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 86400))

        user = self._fetch_user_info(access_token)

        return AccountInfo(
            external_account_id=user["open_id"],
            account_name=user.get("display_name", user["open_id"]),
            access_token=access_token,
            refresh_token=token_data.get("refresh_token"),
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
            if account.token_expires_at and account.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                return
            if not account.encrypted_refresh_token:
                return

            response = requests.post(
                _TOKEN_URL,
                data={
                    "client_key": settings.TIKTOK_CLIENT_KEY,
                    "client_secret": settings.TIKTOK_CLIENT_SECRET,
                    "refresh_token": decrypt_token(account.encrypted_refresh_token),
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            token_data = response.json()
            account.encrypted_access_token = encrypt_token(token_data["access_token"])
            if token_data.get("refresh_token"):
                account.encrypted_refresh_token = encrypt_token(token_data["refresh_token"])
            account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 86400))
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
            user = self._fetch_user_info(decrypt_token(account.encrypted_access_token))
            return AccountInfo(external_account_id=user["open_id"], account_name=user.get("display_name", user["open_id"]))
        finally:
            db.close()

    def publish_video(self, social_account_id: UUID, video_path: str, title: str, caption: str) -> PublishResult:
        raise NotImplementedError(
            "TikTok publishing needs TikTok's Content Posting API audit approval — implemented in Phase 4"
        )

    def schedule_video(
        self, social_account_id: UUID, video_path: str, title: str, caption: str, publish_at
    ) -> PublishResult:
        raise NotImplementedError("TikTok scheduling is implemented in Phase 4")
