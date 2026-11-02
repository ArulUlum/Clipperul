"""Zernio (zernio.com) — a hosted posting API used to reach TikTok and Instagram.

Zernio publishes through its own approved platform apps, so there is no Meta/TikTok developer
app to register. Our side is: one Zernio *profile* per Clipperul user, a hosted connect page where
the user logs in to the platform, then `POST /posts` to publish. Connected accounts are stored as
ordinary SocialAccount rows with `broker="zernio"` and `external_account_id` = Zernio's account id,
so scheduling, retries, cancelling and the daily auto clip all work unchanged."""
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import SocialProvider
from app.models.social_account import SocialAccount
from app.services.social.provider import AccountInfo, ProgressCallback, PublishResult, SocialMediaProvider

logger = logging.getLogger(__name__)

BROKER = "zernio"
# Our provider -> Zernio's platform name. YouTube stays on its own native adapter.
PLATFORMS: dict[SocialProvider, str] = {SocialProvider.TIKTOK: "tiktok", SocialProvider.INSTAGRAM: "instagram"}

_REQUEST_TIMEOUT = 30
_CREATE_POST_TIMEOUT = 120  # publishing inline can take a while on Zernio's side
_UPLOAD_TIMEOUT = 1800
_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 600
_MAX_CONTENT_CHARS = 2200  # TikTok and Instagram caption limit
_UPLOAD_SHARE = 90  # the file upload fills the progress bar up to here; publishing is the rest


class ZernioError(Exception):
    """Zernio rejected a request or reported a failed post. The message is safe to show the user."""


def is_configured() -> bool:
    return bool(settings.ZERNIO_API_KEY)


def _api(method: str, path: str, **kwargs) -> requests.Response:
    if not settings.ZERNIO_API_KEY:
        raise ZernioError("ZERNIO_API_KEY isn't set in .env")
    kwargs.setdefault("timeout", _REQUEST_TIMEOUT)
    return requests.request(
        method,
        f"{settings.ZERNIO_API_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {settings.ZERNIO_API_KEY}"},
        **kwargs,
    )


def _json(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _check(response: requests.Response) -> dict:
    """The JSON body of a successful response; a ZernioError (with Zernio's own message) otherwise."""
    if response.status_code >= 400:
        message = _json(response).get("error") or f"HTTP {response.status_code}"
        raise ZernioError(f"Zernio: {message}")
    return _json(response)


# --- profiles, connecting and listing accounts -------------------------------------------------


def ensure_profile(user_id: UUID) -> str:
    """The user's Zernio profile id, creating it the first time. Looked up by our own user id as
    Zernio's `externalId`, so nothing about it needs storing here. ZERNIO_PROFILE_ID pins one
    existing profile for everyone instead."""
    if settings.ZERNIO_PROFILE_ID:
        return settings.ZERNIO_PROFILE_ID
    response = _api("POST", "/profiles", json={"name": f"clipperul-{user_id}", "externalId": str(user_id)})
    if response.status_code == 409:
        existing = (_json(response).get("details") or {}).get("existingProfileId")
        if existing:
            return existing
    return _check(response)["profile"]["_id"]


def start_connect(platform: str, profile_id: str, redirect_url: str) -> str:
    """The URL of Zernio's hosted connect page — the browser is sent there to log in to the
    platform, then returned to `redirect_url`."""
    body = _check(
        _api("GET", f"/connect/{platform}", params={"profileId": profile_id, "redirect_url": redirect_url})
    )
    url = body.get("authUrl")
    if not url:
        raise ZernioError("Zernio didn't return a connect URL")
    return url


def list_accounts(profile_id: str) -> list[dict]:
    return _check(_api("GET", "/accounts", params={"profileId": profile_id})).get("accounts", [])


def delete_account(zernio_account_id: str) -> None:
    """Removes the account on Zernio's side too, so it stops counting toward the plan's account limit."""
    _check(_api("DELETE", f"/accounts/{zernio_account_id}"))


def sync_user_accounts(db: Session, user_id: UUID) -> list[SocialAccount]:
    """Mirrors the user's connected TikTok/Instagram accounts from Zernio into our table. Reads
    the list from Zernio itself rather than trusting what the connect redirect carried in its URL."""
    by_platform = {name: provider for provider, name in PLATFORMS.items()}
    synced: list[SocialAccount] = []
    for remote in list_accounts(ensure_profile(user_id)):
        provider = by_platform.get(remote.get("platform"))
        if provider is None or not remote.get("_id") or remote.get("isActive") is False:
            continue
        name = remote.get("username") or remote.get("displayName") or remote["platform"]
        taken = db.scalar(
            select(SocialAccount.id).where(
                SocialAccount.broker == BROKER,
                SocialAccount.external_account_id == remote["_id"],
                SocialAccount.user_id != user_id,
            )
        )
        if taken is not None:
            continue  # already imported by another user (only possible with a shared profile)
        account = db.scalar(
            select(SocialAccount).where(
                SocialAccount.user_id == user_id,
                SocialAccount.provider == provider,
                SocialAccount.external_account_id == remote["_id"],
            )
        )
        if account is None:
            account = SocialAccount(
                user_id=user_id, provider=provider, external_account_id=remote["_id"], broker=BROKER, account_name=name
            )
            db.add(account)
        account.account_name = name
        account.broker = BROKER
        # The UI treats an account without connected_at as not connected (the Post form hides it).
        account.connected_at = account.connected_at or datetime.now(timezone.utc)
        synced.append(account)
    db.commit()
    return synced


# --- publishing --------------------------------------------------------------------------------


class _ProgressReader:
    """File wrapper for the upload body: reports how much has been sent (throttled — every report
    is a database write in the caller) and gives requests a length so it sends a plain
    Content-Length instead of chunked encoding, which presigned storage URLs reject."""

    def __init__(self, file, size: int, on_progress: ProgressCallback | None):
        self._file, self._size, self._on_progress = file, size, on_progress
        self._sent = 0
        self._reported = 0

    def __len__(self) -> int:
        return self._size

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(size)
        self._sent += len(chunk)
        if self._on_progress and self._size:
            percent = min(100, int(self._sent / self._size * 100))
            if percent - self._reported >= 2 or (percent == 100 and self._reported < 100):
                self._reported = percent
                self._on_progress(percent * _UPLOAD_SHARE / 100)
        return chunk


def _upload_file(video_path: str, on_progress: ProgressCallback | None) -> str:
    """Puts the clip into Zernio's storage and returns the public URL a post can reference."""
    size = Path(video_path).stat().st_size
    presign = _check(
        _api("POST", "/media/presign", json={"filename": "clip.mp4", "contentType": "video/mp4", "size": size})
    )
    with open(video_path, "rb") as f:
        response = requests.put(
            presign["uploadUrl"],
            data=_ProgressReader(f, size, on_progress),
            headers={"Content-Type": "video/mp4"},
            timeout=_UPLOAD_TIMEOUT,
        )
    response.raise_for_status()
    return presign["publicUrl"]


def _build_post(platform: str, account_id: str, media_url: str, title: str, caption: str) -> dict:
    content = f"{title}\n\n{caption}".strip() if caption else title
    entry: dict = {"platform": platform, "accountId": account_id}
    payload: dict = {
        "content": content[:_MAX_CONTENT_CHARS],
        "mediaItems": [{"type": "video", "url": media_url}],
        "platforms": [entry],
        "publishNow": True,
    }
    if platform == "instagram":
        entry["platformSpecificData"] = {"shareToFeed": True}  # a single video becomes a Reel
    else:
        # TikTok refuses a post without these, and a business-app connection can only post public
        # videos. The two confirmation flags are TikTok's "the creator saw the content and agreed"
        # declarations: they stand for the user's own Post click (or, for the auto clip, their
        # having switched it on).
        payload["tiktokSettings"] = {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        }
    return payload


def _outcome(post: dict, platform: str) -> PublishResult | None:
    """PublishResult once the platform has published, None while it is still in progress, and a
    ZernioError when the post failed."""
    entry = next((p for p in post.get("platforms", []) if p.get("platform") == platform), {})
    if entry.get("status") == "published":
        return PublishResult(external_post_id=post.get("_id", ""), url=entry.get("platformPostUrl") or None)
    if entry.get("status") in ("failed", "cancelled") or post.get("status") in ("failed", "cancelled"):
        raise ZernioError(f"Zernio: {entry.get('errorMessage') or 'the platform rejected the post'}")
    return None


class ZernioProvider(SocialMediaProvider):
    """Publishes through Zernio for accounts with `broker="zernio"`. Connecting is a separate flow
    (see app.api.social_accounts), so the OAuth methods of the interface don't apply."""

    can_publish = True

    def get_authorization_url(self, state: str) -> str:
        raise NotImplementedError("Zernio accounts connect through Zernio's own connect page")

    def connect(self, authorization_code: str, state: str) -> AccountInfo:
        raise NotImplementedError("Zernio accounts connect through Zernio's own connect page")

    def refresh_token_if_needed(self, social_account_id: UUID) -> None:
        return None  # Zernio keeps the platform tokens fresh

    def schedule_video(self, social_account_id: UUID, video_path: str, title: str, caption: str, publish_at):
        raise NotImplementedError("Scheduling is handled by Clipperul's own scheduler")

    def _load(self, social_account_id: UUID) -> tuple[str, str]:
        from app.core.db import SessionLocal

        db = SessionLocal()
        try:
            account = db.get(SocialAccount, social_account_id)
            if account is None:
                raise ValueError(f"Social account {social_account_id} not found")
            platform = PLATFORMS.get(account.provider)
            if platform is None:
                raise ValueError(f"{account.provider.value} can't be published through Zernio")
            return account.external_account_id, platform
        finally:
            db.close()

    def get_account_info(self, social_account_id: UUID) -> AccountInfo:
        from app.core.db import SessionLocal

        db = SessionLocal()
        try:
            account = db.get(SocialAccount, social_account_id)
            if account is None:
                raise ValueError(f"Social account {social_account_id} not found")
            return AccountInfo(external_account_id=account.external_account_id, account_name=account.account_name)
        finally:
            db.close()

    def publish_video(
        self,
        social_account_id: UUID,
        video_path: str,
        title: str,
        caption: str,
        on_progress: ProgressCallback | None = None,
    ) -> PublishResult:
        account_id, platform = self._load(social_account_id)

        media_url = _upload_file(video_path, on_progress)
        response = _api(
            "POST", "/posts", json=_build_post(platform, account_id, media_url, title, caption),
            timeout=_CREATE_POST_TIMEOUT,
        )
        body = _check(response)
        if response.status_code == 207:  # published on some platforms but not all — here, not ours
            failed = next((r for r in body.get("platformResults", []) if r.get("status") == "failed"), None)
            if failed:
                raise ZernioError(f"Zernio: {failed.get('error') or 'the platform rejected the post'}")
        post = body.get("post") or {}
        if not post.get("_id"):
            raise ZernioError("Zernio didn't return the created post")
        if on_progress:
            on_progress(_UPLOAD_SHARE + 2)

        # The platform may still be processing the upload: wait for it so "published" is true and
        # a failure is reported (and retried) instead of silently lost. Every poll doubles as the
        # cancel checkpoint, since on_progress raises when the user cancels.
        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        while True:
            result = _outcome(post, platform)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise ZernioError("Zernio is still publishing this post after 10 minutes")
            time.sleep(_POLL_INTERVAL_SECONDS)
            if on_progress:
                on_progress(95)
            post = _check(_api("GET", f"/posts/{post['_id']}")).get("post") or post
