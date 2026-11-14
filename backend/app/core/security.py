import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from cryptography.fernet import Fernet
from jose import JWTError, jwt

from app.core.config import settings


def _bcrypt_input(password: str) -> bytes:
    # bcrypt reads only the first 72 bytes. Cutting there ourselves keeps existing hashes valid and keeps
    # newer bcrypt versions (which raise on longer input) from turning a long login attempt into a 500.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(_bcrypt_input(password), hashed_password.encode("utf-8"))


def create_access_token(user_id: UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> UUID | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        sub = payload.get("sub")
        return UUID(sub) if sub else None
    except (JWTError, ValueError):
        return None


_OAUTH_STATE_EXPIRE_MINUTES = 10


def create_oauth_state(user_id: UUID, provider: str) -> str:
    """Short-lived, signed token carried through an OAuth redirect round-trip so the
    unauthenticated callback (the platform calls it directly — no Authorization header reaches
    it) can recover which user started the connection, without needing server-side session
    storage. Reuses the access-token signing key; a distinct 'purpose' claim (plus binding to
    the specific provider) stops a leaked state value from being replayed as a login token or
    against a different platform's callback."""

    expire = datetime.now(timezone.utc) + timedelta(minutes=_OAUTH_STATE_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "provider": provider, "purpose": "oauth_state", "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_oauth_state(state: str, provider: str) -> UUID | None:
    try:
        payload = jwt.decode(state, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("purpose") != "oauth_state" or payload.get("provider") != provider:
            return None
        sub = payload.get("sub")
        return UUID(sub) if sub else None
    except (JWTError, ValueError):
        return None


def derive_pkce_verifier(state: str) -> str:
    """PKCE code_verifier, deterministically derived from the OAuth `state` token + our
    signing secret — used by providers (TikTok) whose token exchange requires PKCE. The
    callback only ever receives `code` and `state` back from the platform, so deriving the
    verifier FROM `state` (rather than minting a random one at connect-time) lets the callback
    reconstruct the exact same verifier without a separate server-side session store, while
    staying unguessable to anyone who only sees the resulting code_challenge."""
    digest = hmac.new(settings.JWT_SECRET.encode("utf-8"), state.encode("utf-8"), hashlib.sha256).digest()
    # PKCE code_verifier must be 43-128 chars from [A-Za-z0-9-._~] — unpadded base64url of a
    # 32-byte digest is exactly 43 chars and satisfies that alphabet directly.
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _fernet() -> Fernet:
    key = settings.TOKEN_ENCRYPTION_KEY.encode("utf-8")
    # Fernet requires a 32-byte urlsafe base64-encoded key; derive one deterministically
    # from the configured secret so any string of sufficient entropy can be used via env.
    derived = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
    return Fernet(derived)


def encrypt_token(raw_token: str) -> str:
    return _fernet().encrypt(raw_token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: str) -> str:
    return _fernet().decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
