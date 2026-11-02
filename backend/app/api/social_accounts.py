import logging
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.security import create_oauth_state, encrypt_token, verify_oauth_state
from app.models.enums import SocialProvider
from app.models.social_account import SocialAccount
from app.models.user import User
from app.schemas.social import SocialAccountConnectResponse, SocialAccountResponse, build_social_account_response
from app.services.social import zernio
from app.services.social.provider import AccountInfo
from app.services.social.registry import get_social_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])

# Instagram's provider class is fully implemented (see app.services.social.instagram) but is
# gated off here — it needs a Meta Developer account to get INSTAGRAM_APP_ID/SECRET, which
# isn't available right now. Add SocialProvider.INSTAGRAM back to this set once that's sorted.
_IMPLEMENTED_PROVIDERS = {SocialProvider.YOUTUBE, SocialProvider.TIKTOK}

# Which config fields must be set before each implemented provider's connect flow can run,
# checked up front so a missing credential fails with a clear message from our own backend
# instead of building a broken authorization URL that sends the user to a confusing error
# page on the platform's own site.
_REQUIRED_SETTINGS: dict[SocialProvider, tuple[str, str]] = {
    SocialProvider.YOUTUBE: ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
    SocialProvider.TIKTOK: ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
    SocialProvider.INSTAGRAM: ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
}


def _parse_provider(provider: str) -> SocialProvider:
    try:
        return SocialProvider(provider)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown provider: {provider}") from None


@router.get("", response_model=list[SocialAccountResponse])
def list_social_accounts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = db.execute(
        select(SocialAccount).where(SocialAccount.user_id == current_user.id).order_by(SocialAccount.connected_at)
    ).scalars().all()
    return [build_social_account_response(a) for a in accounts]


@router.post("/{provider}/connect", response_model=SocialAccountConnectResponse)
def connect_social_account(
    provider: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Returns the platform's OAuth consent-screen URL — the frontend navigates the browser
    there directly (window.location.href), it isn't fetched via XHR, since Google/TikTok/
    Instagram's login UI can't be shown inside a fetch response."""
    provider_enum = _parse_provider(provider)
    if provider_enum in zernio.PLATFORMS:
        # TikTok/Instagram connect through Zernio whenever it's configured; there is no key-less
        # route for Instagram (its native adapter stays gated off), TikTok falls back to its own.
        if zernio.is_configured():
            return SocialAccountConnectResponse(
                authorization_url=_zernio_connect_url(db, current_user.id, provider_enum)
            )
        if provider_enum == SocialProvider.INSTAGRAM:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Instagram connects through Zernio — set ZERNIO_API_KEY in .env",
            )
    if provider_enum not in _IMPLEMENTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{provider_enum.value} OAuth connection ships in a later phase",
        )
    required = _REQUIRED_SETTINGS.get(provider_enum, ())
    if required and not all(getattr(settings, field) for field in required):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider_enum.value} isn't configured yet — set {' and '.join(required)} in .env",
        )

    state = create_oauth_state(current_user.id, provider_enum.value)
    url = get_social_provider(provider_enum).get_authorization_url(state)
    return SocialAccountConnectResponse(authorization_url=url)


def _zernio_connect_url(db: Session, user_id: UUID, provider: SocialProvider) -> str:
    """Zernio's hosted connect page for this platform. It sends the browser back to our callback
    with `state` and `platform` (our own query params are kept) plus whatever it adds itself.

    With a pinned profile (ZERNIO_PROFILE_ID) the accounts may already be connected on Zernio: they
    are imported right away and the browser goes straight back to the accounts page, so nothing is
    connected twice."""
    callback = (
        f"{settings.BACKEND_BASE_URL}{settings.API_PREFIX}/social-accounts/zernio/callback?"
        + urlencode({"state": create_oauth_state(user_id, zernio.BROKER), "platform": provider.value})
    )
    try:
        if settings.ZERNIO_PROFILE_ID:
            imported = zernio.sync_user_accounts(db, user_id)
            if any(a.provider == provider for a in imported):
                return f"{settings.FRONTEND_URL}/accounts?connected=1&provider={provider.value}"
        return zernio.start_connect(zernio.PLATFORMS[provider], zernio.ensure_profile(user_id), callback)
    except (zernio.ZernioError, requests.RequestException) as exc:
        logger.exception("Could not start the Zernio connect flow for user=%s", user_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not reach Zernio: {exc}") from exc


# Declared before /{provider}/callback so "zernio" isn't mistaken for a provider name.
@router.get("/zernio/callback")
def zernio_callback(request: Request, db: Session = Depends(get_db)):
    """Where Zernio's connect page returns the browser. Nothing in the URL is trusted beyond the
    signed `state` (which user this is): the accounts themselves are read back from Zernio."""
    query = request.query_params
    platform = query.get("platform")
    provider = next((p for p in zernio.PLATFORMS if p.value == platform), None)
    label = f"&provider={provider.value}" if provider else ""
    error_redirect = f"{settings.FRONTEND_URL}/accounts?error=1{label}"

    user_id = verify_oauth_state(query.get("state") or "", zernio.BROKER)
    if user_id is None or provider is None or query.get("error"):
        return RedirectResponse(error_redirect)

    try:
        accounts = zernio.sync_user_accounts(db, user_id)
    except Exception:
        logger.exception("Could not read accounts back from Zernio for user=%s", user_id)
        return RedirectResponse(error_redirect)
    if not any(a.provider == provider for a in accounts):
        return RedirectResponse(error_redirect)  # the user backed out before connecting anything

    return RedirectResponse(f"{settings.FRONTEND_URL}/accounts?connected=1{label}")


@router.get("/{provider}/callback")
def social_account_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    """The platform redirects the user's browser here after they approve/deny access — this
    request carries no Authorization header (it's a plain browser navigation from Google, not
    an API call from our own frontend), so `state` is what recovers which user initiated the
    connection. Always ends in a redirect back to the frontend, success or failure, since
    there's no JSON caller waiting on this response — it's the user's browser itself."""
    provider_enum = _parse_provider(provider)
    error_redirect = f"{settings.FRONTEND_URL}/accounts?error=1&provider={provider_enum.value}"

    query = request.query_params
    if query.get("error"):
        return RedirectResponse(error_redirect)

    code = query.get("code")
    state = query.get("state")
    if not code or not state:
        return RedirectResponse(error_redirect)

    user_id = verify_oauth_state(state, provider_enum.value)
    if user_id is None:
        return RedirectResponse(error_redirect)

    try:
        info: AccountInfo = get_social_provider(provider_enum).connect(code, state)
    except Exception:
        logger.exception("OAuth connect failed for provider=%s user=%s", provider_enum.value, user_id)
        return RedirectResponse(error_redirect)

    if not info.access_token:
        logger.error("Provider %s returned no access_token for user %s", provider_enum.value, user_id)
        return RedirectResponse(error_redirect)

    existing = db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user_id,
            SocialAccount.provider == provider_enum,
            SocialAccount.external_account_id == info.external_account_id,
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.account_name = info.account_name
        existing.encrypted_access_token = encrypt_token(info.access_token)
        if info.refresh_token:
            existing.encrypted_refresh_token = encrypt_token(info.refresh_token)
        existing.token_expires_at = info.expires_at
        existing.connected_at = now
    else:
        db.add(
            SocialAccount(
                user_id=user_id,
                provider=provider_enum,
                account_name=info.account_name,
                external_account_id=info.external_account_id,
                encrypted_access_token=encrypt_token(info.access_token),
                encrypted_refresh_token=encrypt_token(info.refresh_token) if info.refresh_token else None,
                token_expires_at=info.expires_at,
                connected_at=now,
            )
        )
    db.commit()

    return RedirectResponse(f"{settings.FRONTEND_URL}/accounts?connected=1&provider={provider_enum.value}")


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_social_account(
    account_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    account = db.execute(
        select(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.user_id == current_user.id)
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Social account not found")
    if account.broker == zernio.BROKER:
        # Best effort: removing it on Zernio's side frees the slot, but the local disconnect
        # must work even when Zernio is unreachable.
        try:
            zernio.delete_account(account.external_account_id)
        except Exception:
            logger.warning("Could not remove Zernio account %s", account.external_account_id, exc_info=True)
    db.delete(account)
    db.commit()
