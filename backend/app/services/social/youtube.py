from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import requests

from app.core.config import settings
from app.services.social.provider import AccountInfo, ProgressCallback, PublishResult, SocialMediaProvider

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
_SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
)
_REQUEST_TIMEOUT = 30
_UPLOAD_TIMEOUT = 1800  # video PUT can legitimately take a while on a slow connection
# Must be a multiple of 256 KiB (YouTube's resumable protocol) — 1 MiB keeps progress updates
# frequent enough for a clip-sized file to move the bar in small steps.
_UPLOAD_CHUNK_SIZE = 4 * 256 * 1024


class YouTubeProvider(SocialMediaProvider):
    """YouTube Shorts adapter — OAuth via Google's standard authorization-code flow, uploads
    via the YouTube Data API v3 resumable upload protocol. Uses `requests` directly rather
    than the google-api-python-client SDK to avoid adding a heavy new dependency for what's
    a handful of well-documented REST calls."""

    can_publish = True

    def _redirect_uri(self) -> str:
        # MUST match a URI registered exactly (scheme+host+port+path) in the Google Cloud
        # Console OAuth client, or Google rejects the request before showing the consent screen.
        return f"{settings.BACKEND_BASE_URL}{settings.API_PREFIX}/social-accounts/youtube/callback"

    def get_authorization_url(self, state: str) -> str:
        params = {
            "client_id": settings.YOUTUBE_CLIENT_ID,
            "redirect_uri": self._redirect_uri(),
            "response_type": "code",
            "scope": _SCOPES,
            "access_type": "offline",
            # Forces Google to include a refresh_token even for a user who's authorized this
            # app before — without it, a reconnect can silently omit one, leaving us with an
            # access token that just expires with nothing to renew it.
            "prompt": "consent",
            "state": state,
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def _fetch_channel(self, access_token: str) -> dict:
        response = requests.get(
            _CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            raise ValueError("No YouTube channel found for this Google account")
        return items[0]

    def connect(self, authorization_code: str, state: str) -> AccountInfo:
        # Google's flow doesn't use PKCE, so `state` (beyond already being verified by the
        # caller before connect() is invoked) isn't needed again here.
        token_response = requests.post(
            _TOKEN_URL,
            data={
                "client_id": settings.YOUTUBE_CLIENT_ID,
                "client_secret": settings.YOUTUBE_CLIENT_SECRET,
                "code": authorization_code,
                "grant_type": "authorization_code",
                "redirect_uri": self._redirect_uri(),
            },
            timeout=_REQUEST_TIMEOUT,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data["access_token"]
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 3600))

        channel = self._fetch_channel(access_token)

        return AccountInfo(
            external_account_id=channel["id"],
            account_name=channel["snippet"]["title"],
            access_token=access_token,
            # Google only sends this on the FIRST consent (or when prompt=consent forces a
            # fresh one, as above) — genuinely absent, not a bug, if it's ever missing here.
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
            # 5-minute buffer so a token that's about to expire mid-request still gets renewed.
            if account.token_expires_at and account.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                return
            if not account.encrypted_refresh_token:
                return

            response = requests.post(
                _TOKEN_URL,
                data={
                    "client_id": settings.YOUTUBE_CLIENT_ID,
                    "client_secret": settings.YOUTUBE_CLIENT_SECRET,
                    "refresh_token": decrypt_token(account.encrypted_refresh_token),
                    "grant_type": "refresh_token",
                },
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            token_data = response.json()
            account.encrypted_access_token = encrypt_token(token_data["access_token"])
            account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 3600))
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
            channel = self._fetch_channel(decrypt_token(account.encrypted_access_token))
            return AccountInfo(external_account_id=channel["id"], account_name=channel["snippet"]["title"])
        finally:
            db.close()

    def _upload_in_chunks(
        self, upload_url: str, video_path: str, file_size: int, on_progress: ProgressCallback | None
    ) -> dict:
        """Sends the file through YouTube's resumable-upload protocol one chunk at a time
        (rather than one giant PUT) so progress is observable: each non-final chunk is answered
        with 308 + a `Range` header saying how much the server actually has, which is both the
        progress signal and where the next chunk must start."""
        offset = 0
        stalls = 0
        with open(video_path, "rb") as f:
            while True:
                f.seek(offset)
                chunk = f.read(_UPLOAD_CHUNK_SIZE)
                response = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{file_size}",
                    },
                    data=chunk,
                    timeout=_UPLOAD_TIMEOUT,
                    # 308 here means "resume incomplete", not a redirect — it carries no Location.
                    allow_redirects=False,
                )
                if response.status_code in (200, 201):
                    return response.json()
                if response.status_code != 308:
                    response.raise_for_status()
                    raise ValueError(f"Unexpected YouTube upload response: {response.status_code}")

                range_header = response.headers.get("Range")  # e.g. "bytes=0-1048575"
                new_offset = int(range_header.rsplit("-", 1)[-1]) + 1 if range_header else 0
                stalls = stalls + 1 if new_offset <= offset else 0
                if stalls >= 3:
                    raise RuntimeError("YouTube upload stopped making progress")
                offset = new_offset
                if on_progress:
                    on_progress(min(100.0, offset / file_size * 100))

    def publish_video(
        self,
        social_account_id: UUID,
        video_path: str,
        title: str,
        caption: str,
        on_progress: ProgressCallback | None = None,
    ) -> PublishResult:
        from app.core.db import SessionLocal
        from app.core.security import decrypt_token
        from app.models.social_account import SocialAccount

        self.refresh_token_if_needed(social_account_id)

        db = SessionLocal()
        try:
            account = db.get(SocialAccount, social_account_id)
            if account is None:
                raise ValueError(f"Social account {social_account_id} not found")
            access_token = decrypt_token(account.encrypted_access_token)
        finally:
            db.close()

        metadata = {
            # YouTube limits: title <=100 chars, description <=5000. "22" = People & Blogs,
            # a reasonable default category for short-form clips.
            "snippet": {"title": title[:100], "description": caption[:5000], "categoryId": "22"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        file_size = Path(video_path).stat().st_size

        start = requests.post(
            _UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(file_size),
            },
            json=metadata,
            timeout=_REQUEST_TIMEOUT,
        )
        start.raise_for_status()
        upload_url = start.headers["Location"]

        video_id = self._upload_in_chunks(upload_url, video_path, file_size, on_progress)["id"]

        return PublishResult(external_post_id=video_id, url=f"https://youtube.com/shorts/{video_id}")

    def schedule_video(
        self, social_account_id: UUID, video_path: str, title: str, caption: str, publish_at
    ) -> PublishResult:
        raise NotImplementedError("YouTube scheduling is implemented in Phase 4")
