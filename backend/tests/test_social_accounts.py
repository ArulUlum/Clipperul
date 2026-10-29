from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, create_oauth_state, hash_password
from app.main import app
from app.models.enums import SocialProvider
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.social.provider import AccountInfo


def _make_user(db_session, email: str = "socialtest@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


class _FakeYouTubeProvider:
    def __init__(self, info: AccountInfo | None = None, raise_error: bool = False):
        self._info = info
        self._raise_error = raise_error

    def get_authorization_url(self, state: str) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    def connect(self, authorization_code: str, state: str) -> AccountInfo:
        if self._raise_error:
            raise RuntimeError("token exchange failed")
        return self._info


def test_list_social_accounts_empty_for_new_user(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.get("/api/social-accounts", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()


def test_list_social_accounts_only_returns_current_users_accounts(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        owner = _make_user(db_session, "owner@example.com")
        other = _make_user(db_session, "other@example.com")
        db_session.add(
            SocialAccount(
                user_id=owner.id,
                provider=SocialProvider.YOUTUBE,
                account_name="Owner's Channel",
                external_account_id="UC123",
                encrypted_access_token="enc-token",
                connected_at=datetime.now(timezone.utc),
            )
        )
        db_session.commit()

        token = create_access_token(other.id)
        client = TestClient(app)
        response = client.get("/api/social-accounts", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()


def test_connect_unknown_provider_returns_404(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post("/api/social-accounts/bogus/connect", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_connect_unimplemented_provider_returns_501(db_session):
    # Instagram's adapter is fully implemented but stays gated off (see _IMPLEMENTED_PROVIDERS
    # in app.api.social_accounts) until a Meta Developer account is available.
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post("/api/social-accounts/instagram/connect", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 501
    finally:
        app.dependency_overrides.clear()


def test_connect_tiktok_returns_authorization_url_with_pkce(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        mocker.patch("app.api.social_accounts.settings.TIKTOK_CLIENT_KEY", "test-client-key")
        mocker.patch("app.api.social_accounts.settings.TIKTOK_CLIENT_SECRET", "test-client-secret")
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post("/api/social-accounts/tiktok/connect", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        url = response.json()["authorization_url"]
        assert url.startswith("https://www.tiktok.com/v2/auth/authorize/")
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
    finally:
        app.dependency_overrides.clear()


def test_connect_tiktok_without_credentials_configured_returns_503(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        mocker.patch("app.api.social_accounts.settings.TIKTOK_CLIENT_KEY", None)
        mocker.patch("app.api.social_accounts.settings.TIKTOK_CLIENT_SECRET", None)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post("/api/social-accounts/tiktok/connect", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_connect_youtube_returns_authorization_url(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        mocker.patch("app.api.social_accounts.settings.YOUTUBE_CLIENT_ID", "test-client-id")
        mocker.patch("app.api.social_accounts.settings.YOUTUBE_CLIENT_SECRET", "test-client-secret")
        mocker.patch(
            "app.api.social_accounts.get_social_provider", return_value=_FakeYouTubeProvider()
        )
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post("/api/social-accounts/youtube/connect", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["authorization_url"].startswith("https://accounts.google.com/")
    finally:
        app.dependency_overrides.clear()


def test_connect_youtube_without_credentials_configured_returns_503(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        mocker.patch("app.api.social_accounts.settings.YOUTUBE_CLIENT_ID", None)
        mocker.patch("app.api.social_accounts.settings.YOUTUBE_CLIENT_SECRET", None)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post("/api/social-accounts/youtube/connect", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_callback_creates_social_account_and_redirects_with_success(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        state = create_oauth_state(user.id, "youtube")
        info = AccountInfo(
            external_account_id="UC999",
            account_name="Test Channel",
            access_token="raw-access-token",
            refresh_token="raw-refresh-token",
            expires_at=datetime.now(timezone.utc),
        )
        mocker.patch(
            "app.api.social_accounts.get_social_provider", return_value=_FakeYouTubeProvider(info=info)
        )

        client = TestClient(app)
        response = client.get(
            f"/api/social-accounts/youtube/callback?code=abc123&state={state}", follow_redirects=False
        )
        assert response.status_code in (302, 307)
        assert "connected=1" in response.headers["location"]

        account = db_session.query(SocialAccount).filter_by(user_id=user.id).one()
        assert account.account_name == "Test Channel"
        assert account.external_account_id == "UC999"
        # Tokens must never be stored in plaintext.
        assert account.encrypted_access_token != "raw-access-token"
        assert account.encrypted_refresh_token != "raw-refresh-token"
    finally:
        app.dependency_overrides.clear()


def test_callback_reconnect_updates_existing_row_not_a_duplicate(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        state = create_oauth_state(user.id, "youtube")
        info = AccountInfo(
            external_account_id="UC999",
            account_name="Renamed Channel",
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            expires_at=datetime.now(timezone.utc),
        )
        mocker.patch(
            "app.api.social_accounts.get_social_provider", return_value=_FakeYouTubeProvider(info=info)
        )
        db_session.add(
            SocialAccount(
                user_id=user.id,
                provider=SocialProvider.YOUTUBE,
                account_name="Old Name",
                external_account_id="UC999",
                encrypted_access_token="old-enc-token",
                connected_at=datetime.now(timezone.utc),
            )
        )
        db_session.commit()

        client = TestClient(app)
        client.get(f"/api/social-accounts/youtube/callback?code=abc123&state={state}", follow_redirects=False)

        accounts = db_session.query(SocialAccount).filter_by(user_id=user.id).all()
        assert len(accounts) == 1
        assert accounts[0].account_name == "Renamed Channel"
    finally:
        app.dependency_overrides.clear()


def test_callback_with_invalid_state_redirects_with_error(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        client = TestClient(app)
        response = client.get(
            "/api/social-accounts/youtube/callback?code=abc123&state=garbage", follow_redirects=False
        )
        assert response.status_code in (302, 307)
        assert "error=1" in response.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_callback_when_provider_raises_redirects_with_error(db_session, mocker):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        state = create_oauth_state(user.id, "youtube")
        mocker.patch(
            "app.api.social_accounts.get_social_provider",
            return_value=_FakeYouTubeProvider(raise_error=True),
        )

        client = TestClient(app)
        response = client.get(
            f"/api/social-accounts/youtube/callback?code=abc123&state={state}", follow_redirects=False
        )
        assert response.status_code in (302, 307)
        assert "error=1" in response.headers["location"]
        assert db_session.query(SocialAccount).filter_by(user_id=user.id).count() == 0
    finally:
        app.dependency_overrides.clear()


def test_disconnect_removes_account(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user = _make_user(db_session)
        account = SocialAccount(
            user_id=user.id,
            provider=SocialProvider.YOUTUBE,
            account_name="Test Channel",
            external_account_id="UC1",
            encrypted_access_token="enc",
            connected_at=datetime.now(timezone.utc),
        )
        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)

        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.delete(f"/api/social-accounts/{account.id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 204
        assert db_session.query(SocialAccount).filter_by(id=account.id).first() is None
    finally:
        app.dependency_overrides.clear()


def test_disconnect_other_users_account_returns_404(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        owner = _make_user(db_session, "owner2@example.com")
        other = _make_user(db_session, "other2@example.com")
        account = SocialAccount(
            user_id=owner.id,
            provider=SocialProvider.YOUTUBE,
            account_name="Owner's Channel",
            external_account_id="UC2",
            encrypted_access_token="enc",
            connected_at=datetime.now(timezone.utc),
        )
        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)

        token = create_access_token(other.id)
        client = TestClient(app)
        response = client.delete(f"/api/social-accounts/{account.id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_tiktok_redirect_uses_its_own_base_while_youtube_keeps_the_shared_one(mocker):
    # TikTok needs https (ngrok) while YouTube works fine on localhost — the override must move
    # only TikTok's redirect URI.
    from app.services.social.tiktok import TikTokProvider
    from app.services.social.youtube import YouTubeProvider

    mocker.patch("app.core.config.settings.BACKEND_BASE_URL", "http://localhost:8000")
    mocker.patch("app.core.config.settings.TIKTOK_REDIRECT_BASE_URL", "https://abc.ngrok-free.dev")

    assert TikTokProvider()._redirect_uri() == "https://abc.ngrok-free.dev/api/social-accounts/tiktok/callback"
    assert YouTubeProvider()._redirect_uri() == "http://localhost:8000/api/social-accounts/youtube/callback"


def test_tiktok_redirect_falls_back_to_shared_base_when_no_override(mocker):
    from app.services.social.tiktok import TikTokProvider

    mocker.patch("app.core.config.settings.BACKEND_BASE_URL", "https://prod.example.com")
    mocker.patch("app.core.config.settings.TIKTOK_REDIRECT_BASE_URL", None)

    assert TikTokProvider()._redirect_uri() == "https://prod.example.com/api/social-accounts/tiktok/callback"
