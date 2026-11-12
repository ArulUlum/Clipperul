"""Zernio-brokered TikTok/Instagram accounts: connecting, syncing, disconnecting and publishing.

Zernio itself is faked at the `requests` layer (a router keyed on method + path), so these run the
real request building and response handling without any network."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import settings
from app.core.security import create_access_token, create_oauth_state, hash_password
from app.main import app
from app.models.enums import SocialProvider
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.auto_clip.service import publishable_accounts
from app.services.social import zernio
from app.services.social.registry import get_social_provider
from app.services.social.youtube import YouTubeProvider
from app.services.social.zernio import ZernioError, ZernioProvider


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeZernio:
    """Routes the calls made through `requests.request`; unknown routes fail the test loudly."""

    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def on(self, method, path, response):
        self.routes[(method, path)] = response

    def __call__(self, method, url, **kwargs):
        path = url.removeprefix(settings.ZERNIO_API_BASE_URL)
        self.calls.append((method, path, kwargs))
        handler = self.routes.get((method, path))
        assert handler is not None, f"unexpected Zernio call: {method} {path}"
        return handler(kwargs) if callable(handler) else handler

    def sent(self, method, path):
        return [kw for m, p, kw in self.calls if (m, p) == (method, path)]


@pytest.fixture()
def fake(mocker):
    mocker.patch.object(settings, "ZERNIO_API_KEY", "sk_test")
    router = FakeZernio()
    mocker.patch("app.services.social.zernio.requests.request", side_effect=router)
    mocker.patch("app.services.social.zernio.time.sleep")
    return router


def _user(db_session, email="zernio@example.com") -> User:
    user = User(email=email, hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _account(db_session, user, provider=SocialProvider.TIKTOK, broker="zernio", external="acc_1") -> SocialAccount:
    account = SocialAccount(
        user_id=user.id, provider=provider, account_name="@me", external_account_id=external, broker=broker,
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(account)
    db_session.commit()
    return account


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


# --- connecting ----------------------------------------------------------------------------------


def test_connect_sends_the_user_to_zernios_connect_page(client, db_session, fake):
    user = _user(db_session)
    fake.on("POST", "/profiles", Resp(201, {"profile": {"_id": "prof_1"}}))
    fake.on("GET", "/connect/instagram", Resp(200, {"authUrl": "https://zernio.com/connect/abc"}))

    response = client.post("/api/social-accounts/instagram/connect", headers=_auth(user))

    assert response.status_code == 200
    assert response.json()["authorization_url"] == "https://zernio.com/connect/abc"
    (call,) = fake.sent("GET", "/connect/instagram")
    assert call["params"]["profileId"] == "prof_1"
    callback = urlparse(call["params"]["redirect_url"])
    assert callback.path == "/api/social-accounts/zernio/callback"
    query = parse_qs(callback.query)
    assert query["platform"] == ["instagram"]
    assert query["state"]  # the signed state that identifies this user on the way back
    assert fake.sent("POST", "/profiles")[0]["json"]["externalId"] == str(user.id)


def test_connect_reuses_the_existing_profile(client, db_session, fake):
    user = _user(db_session)
    fake.on("POST", "/profiles", Resp(409, {"error": "exists", "details": {"existingProfileId": "prof_old"}}))
    fake.on("GET", "/connect/tiktok", Resp(200, {"authUrl": "https://zernio.com/connect/x"}))

    assert client.post("/api/social-accounts/tiktok/connect", headers=_auth(user)).status_code == 200
    assert fake.sent("GET", "/connect/tiktok")[0]["params"]["profileId"] == "prof_old"


def test_connect_reports_a_zernio_failure_as_502(client, db_session, fake):
    user = _user(db_session)
    fake.on("POST", "/profiles", Resp(401, {"error": "Invalid API key"}))

    response = client.post("/api/social-accounts/tiktok/connect", headers=_auth(user))

    assert response.status_code == 502
    assert "Invalid API key" in response.json()["detail"]


def test_pinned_profile_imports_accounts_already_connected_on_zernio(client, db_session, fake, mocker):
    user = _user(db_session)
    mocker.patch.object(settings, "ZERNIO_PROFILE_ID", "prof_default")
    fake.on("GET", "/accounts", Resp(200, _ACCOUNTS))

    response = client.post("/api/social-accounts/instagram/connect", headers=_auth(user))

    assert response.json()["authorization_url"].endswith("/settings?connected=1&provider=instagram")
    assert fake.sent("GET", "/accounts")[0]["params"] == {"profileId": "prof_default"}
    assert fake.sent("POST", "/profiles") == [] and fake.sent("GET", "/connect/instagram") == []
    assert {r.provider for r in db_session.query(SocialAccount).filter_by(user_id=user.id)} == {
        SocialProvider.TIKTOK, SocialProvider.INSTAGRAM,
    }


def test_pinned_profile_without_the_platform_falls_back_to_the_connect_page(client, db_session, fake, mocker):
    user = _user(db_session)
    mocker.patch.object(settings, "ZERNIO_PROFILE_ID", "prof_default")
    fake.on("GET", "/accounts", Resp(200, {"accounts": [_ACCOUNTS["accounts"][0]]}))  # only TikTok is connected
    fake.on("GET", "/connect/instagram", Resp(200, {"authUrl": "https://zernio.com/connect/ig"}))

    response = client.post("/api/social-accounts/instagram/connect", headers=_auth(user))

    assert response.json()["authorization_url"] == "https://zernio.com/connect/ig"
    assert fake.sent("GET", "/connect/instagram")[0]["params"]["profileId"] == "prof_default"


def test_a_shared_profile_account_is_only_imported_by_the_first_user(client, db_session, fake, mocker):
    first, second = _user(db_session), _user(db_session, "second@example.com")
    mocker.patch.object(settings, "ZERNIO_PROFILE_ID", "prof_default")
    fake.on("GET", "/accounts", Resp(200, _ACCOUNTS))
    fake.on("GET", "/connect/tiktok", Resp(200, {"authUrl": "https://zernio.com/connect/tt"}))

    client.post("/api/social-accounts/tiktok/connect", headers=_auth(first))
    response = client.post("/api/social-accounts/tiktok/connect", headers=_auth(second))

    assert db_session.query(SocialAccount).filter_by(user_id=second.id).count() == 0
    assert response.json()["authorization_url"] == "https://zernio.com/connect/tt"  # nothing to import for them


def test_youtube_never_goes_through_zernio(client, db_session, fake, mocker):
    user = _user(db_session)
    mocker.patch.object(settings, "YOUTUBE_CLIENT_ID", "id")
    mocker.patch.object(settings, "YOUTUBE_CLIENT_SECRET", "secret")

    response = client.post("/api/social-accounts/youtube/connect", headers=_auth(user))

    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith("https://accounts.google.com/")
    assert fake.calls == []


# --- callback ------------------------------------------------------------------------------------

_ACCOUNTS = {
    "accounts": [
        {"_id": "acc_tt", "platform": "tiktok", "username": "clipper", "isActive": True},
        {"_id": "acc_ig", "platform": "instagram", "username": "clipper.ig", "isActive": True},
        {"_id": "acc_off", "platform": "tiktok", "username": "old", "isActive": False},
        {"_id": "acc_x", "platform": "twitter", "username": "nope", "isActive": True},
    ]
}


def _callback(client, user, platform="tiktok", **extra):
    state = create_oauth_state(user.id, "zernio")
    params = {"state": state, "platform": platform, **extra}
    return client.get("/api/social-accounts/zernio/callback", params=params)


def test_callback_syncs_accounts_from_zernio(client, db_session, fake):
    user = _user(db_session)
    fake.on("POST", "/profiles", Resp(201, {"profile": {"_id": "prof_1"}}))
    fake.on("GET", "/accounts", Resp(200, _ACCOUNTS))

    response = _callback(client, user, connected="true", accountId="forged")

    assert response.status_code == 307
    assert response.headers["location"].endswith("/settings?connected=1&provider=tiktok")
    rows = db_session.query(SocialAccount).filter_by(user_id=user.id).all()
    assert {(r.provider, r.external_account_id, r.account_name, r.broker) for r in rows} == {
        (SocialProvider.TIKTOK, "acc_tt", "clipper", "zernio"),
        (SocialProvider.INSTAGRAM, "acc_ig", "clipper.ig", "zernio"),
    }  # inactive and unsupported-platform accounts are skipped; the forged id in the URL is ignored
    assert all(r.encrypted_access_token is None for r in rows)
    assert all(r.connected_at is not None for r in rows)  # the Post form only lists connected accounts


def test_callback_twice_does_not_duplicate_accounts(client, db_session, fake):
    user = _user(db_session)
    fake.on("POST", "/profiles", Resp(201, {"profile": {"_id": "prof_1"}}))
    fake.on("GET", "/accounts", Resp(200, _ACCOUNTS))

    _callback(client, user)
    _callback(client, user)

    assert db_session.query(SocialAccount).filter_by(user_id=user.id).count() == 2


def test_callback_without_a_connected_account_is_an_error(client, db_session, fake):
    user = _user(db_session)
    fake.on("POST", "/profiles", Resp(201, {"profile": {"_id": "prof_1"}}))
    fake.on("GET", "/accounts", Resp(200, {"accounts": []}))  # the user closed the connect page

    response = _callback(client, user)

    assert response.headers["location"].endswith("/settings?error=1&provider=tiktok")


@pytest.mark.parametrize("extra", [{"error": "oauth_denied"}])
def test_callback_error_from_zernio_is_an_error(client, db_session, fake, extra):
    user = _user(db_session)
    response = _callback(client, user, **extra)
    assert response.headers["location"].endswith("/settings?error=1&provider=tiktok")
    assert fake.calls == []


def test_callback_rejects_a_bad_or_foreign_state(client, db_session, fake):
    user = _user(db_session)
    youtube_state = create_oauth_state(user.id, "youtube")  # valid signature, wrong flow

    for state in ("garbage", youtube_state):
        response = client.get(
            "/api/social-accounts/zernio/callback", params={"state": state, "platform": "tiktok"}
        )
        assert response.headers["location"].endswith("/settings?error=1&provider=tiktok")
    assert fake.calls == []
    assert db_session.query(SocialAccount).count() == 0


# --- disconnecting -------------------------------------------------------------------------------


def test_disconnect_also_removes_the_account_on_zernio(client, db_session, fake):
    user = _user(db_session)
    account = _account(db_session, user)
    fake.on("DELETE", "/accounts/acc_1", Resp(200, {}))

    assert client.delete(f"/api/social-accounts/{account.id}", headers=_auth(user)).status_code == 204

    assert len(fake.sent("DELETE", "/accounts/acc_1")) == 1
    assert db_session.query(SocialAccount).count() == 0


def test_disconnect_still_works_when_zernio_is_down(client, db_session, fake):
    user = _user(db_session)
    account = _account(db_session, user)
    fake.on("DELETE", "/accounts/acc_1", Resp(500, {"error": "boom"}))

    assert client.delete(f"/api/social-accounts/{account.id}", headers=_auth(user)).status_code == 204
    assert db_session.query(SocialAccount).count() == 0


def test_disconnecting_a_native_account_never_calls_zernio(client, db_session, fake):
    user = _user(db_session)
    account = _account(db_session, user, provider=SocialProvider.YOUTUBE, broker=None, external="UC1")

    assert client.delete(f"/api/social-accounts/{account.id}", headers=_auth(user)).status_code == 204
    assert fake.calls == []


# --- routing -------------------------------------------------------------------------------------


def test_registry_hands_brokered_accounts_to_zernio():
    assert isinstance(get_social_provider(SocialProvider.TIKTOK, "zernio"), ZernioProvider)
    assert isinstance(get_social_provider(SocialProvider.INSTAGRAM, "zernio"), ZernioProvider)
    assert isinstance(get_social_provider(SocialProvider.YOUTUBE, None), YouTubeProvider)
    assert get_social_provider(SocialProvider.TIKTOK, None).can_publish is False  # native TikTok still can't


def test_auto_clip_posts_to_zernio_accounts_but_not_to_native_tiktok(db_session):
    user = _user(db_session)
    youtube = _account(db_session, user, SocialProvider.YOUTUBE, broker=None, external="UC1")
    brokered = _account(db_session, user, SocialProvider.TIKTOK, external="acc_tt")
    _account(db_session, user, SocialProvider.TIKTOK, broker=None, external="native")

    assert {a.id for a in publishable_accounts(db_session, user.id)} == {youtube.id, brokered.id}


# --- publishing ----------------------------------------------------------------------------------


@pytest.fixture()
def clip_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * 100_000)
    return str(path)


@pytest.fixture()
def upload(mocker):
    """Stands in for the PUT to Zernio's storage: drains the body like a real client would."""
    sent = {}

    def put(url, data, headers, timeout):
        sent.update(url=url, headers=headers, length=len(data), body=b"")
        while chunk := data.read(8192):
            sent["body"] += chunk
        return Resp(200)

    mocker.patch("app.services.social.zernio.requests.put", side_effect=put)
    return sent


def _ready(fake, platform_entry, post_id="post_1", status=201, extra=None):
    fake.on("POST", "/media/presign", Resp(200, {"uploadUrl": "https://s3.example/put", "publicUrl": "https://media.zernio.com/temp/c.mp4"}))
    fake.on("POST", "/posts", Resp(status, {"post": {"_id": post_id, "status": "publishing", "platforms": [platform_entry]}, **(extra or {})}))


def _provider(mocker, platform="tiktok", account="acc_1"):
    mocker.patch.object(ZernioProvider, "_load", return_value=(account, platform))
    return ZernioProvider()


def test_tiktok_post_is_uploaded_created_and_polled_until_published(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "processing"})
    polls = iter([
        Resp(200, {"post": {"_id": "post_1", "platforms": [{"platform": "tiktok", "status": "processing"}]}}),
        Resp(200, {"post": {"_id": "post_1", "platforms": [{"platform": "tiktok", "status": "published", "platformPostUrl": "https://tiktok.com/@me/video/1"}]}}),
    ])
    fake.on("GET", "/posts/post_1", lambda kw: next(polls))
    progress = []

    result = _provider(mocker).publish_video("sid", clip_file, "Judul", "caption #fyp", on_progress=progress.append)

    assert (result.external_post_id, result.url) == ("post_1", "https://tiktok.com/@me/video/1")
    assert upload["url"] == "https://s3.example/put" and upload["headers"] == {"Content-Type": "video/mp4"}
    assert upload["length"] == 100_000 and len(upload["body"]) == 100_000  # sent with a real Content-Length
    (presign,) = fake.sent("POST", "/media/presign")
    assert presign["json"]["size"] == 100_000
    (post,) = fake.sent("POST", "/posts")
    body = post["json"]
    assert body["content"] == "Judul\n\ncaption #fyp"
    assert body["mediaItems"] == [{"type": "video", "url": "https://media.zernio.com/temp/c.mp4"}]
    assert body["platforms"] == [{"platform": "tiktok", "accountId": "acc_1"}]
    assert body["publishNow"] is True
    assert body["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["tiktokSettings"]["content_preview_confirmed"] and body["tiktokSettings"]["express_consent_given"]
    assert progress == sorted(progress) and 0 < progress[0] <= 90 and progress[-1] == 95 and max(progress) < 100
    assert len(fake.sent("GET", "/posts/post_1")) == 2


def test_instagram_post_is_a_reel_without_tiktok_settings(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "instagram", "status": "published", "platformPostUrl": "https://instagram.com/reel/1"})

    result = _provider(mocker, "instagram").publish_video("sid", clip_file, "Judul", "")

    assert result.url == "https://instagram.com/reel/1"
    (post,) = fake.sent("POST", "/posts")
    assert post["json"]["content"] == "Judul"  # no caption: just the title, no trailing blank lines
    assert post["json"]["platforms"][0]["platformSpecificData"] == {"shareToFeed": True}
    assert "tiktokSettings" not in post["json"]


def test_tiktok_published_without_a_url_yet_is_still_published(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "published", "platformPostUrl": None})

    result = _provider(mocker).publish_video("sid", clip_file, "t", "c")

    assert result.url is None and result.external_post_id == "post_1"


def test_long_captions_are_cut_to_the_platform_limit(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "published"})

    _provider(mocker).publish_video("sid", clip_file, "t", "c" * 5000)

    assert len(fake.sent("POST", "/posts")[0]["json"]["content"]) == 2200


def test_a_failed_post_raises_with_the_platforms_reason(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "processing"})
    fake.on("GET", "/posts/post_1", Resp(200, {"post": {"_id": "post_1", "status": "failed", "platforms": [
        {"platform": "tiktok", "status": "failed", "errorMessage": "Video is too long"}]}}))

    with pytest.raises(ZernioError, match="Video is too long"):
        _provider(mocker).publish_video("sid", clip_file, "t", "c")


def test_a_partial_207_response_with_our_platform_failing_raises(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "failed"}, status=207,
           extra={"platformResults": [{"platform": "tiktok", "status": "failed", "error": "Account disconnected"}]})

    with pytest.raises(ZernioError, match="Account disconnected"):
        _provider(mocker).publish_video("sid", clip_file, "t", "c")


def test_zernio_api_errors_surface_their_message(fake, upload, clip_file, mocker):
    fake.on("POST", "/media/presign", Resp(402, {"error": "Free account limit reached"}))

    with pytest.raises(ZernioError, match="Free account limit reached"):
        _provider(mocker).publish_video("sid", clip_file, "t", "c")


def test_a_post_that_never_finishes_times_out(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "processing"})
    fake.on("GET", "/posts/post_1", Resp(200, {"post": {"_id": "post_1", "platforms": [{"platform": "tiktok", "status": "processing"}]}}))
    mocker.patch.object(zernio, "_POLL_TIMEOUT_SECONDS", 0)

    with pytest.raises(ZernioError, match="still publishing"):
        _provider(mocker).publish_video("sid", clip_file, "t", "c")


class _Cancelled(Exception):
    pass


def test_cancelling_during_the_upload_stops_it_before_anything_is_posted(fake, upload, clip_file, mocker):
    fake.on("POST", "/media/presign", Resp(200, {"uploadUrl": "https://s3.example/put", "publicUrl": "u"}))

    def cancel(_percent):
        raise _Cancelled()

    with pytest.raises(_Cancelled):
        _provider(mocker).publish_video("sid", clip_file, "t", "c", on_progress=cancel)
    assert fake.sent("POST", "/posts") == []


def test_cancelling_while_waiting_stops_polling(fake, upload, clip_file, mocker):
    _ready(fake, {"platform": "tiktok", "status": "processing"})
    calls = []

    def on_progress(percent):
        calls.append(percent)
        if percent == 95:  # the first poll tick
            raise _Cancelled()

    with pytest.raises(_Cancelled):
        _provider(mocker).publish_video("sid", clip_file, "t", "c", on_progress=on_progress)
    assert fake.sent("GET", "/posts/post_1") == []


def test_publishing_without_an_api_key_fails_clearly(clip_file, mocker):
    mocker.patch.object(settings, "ZERNIO_API_KEY", None)

    with pytest.raises(ZernioError, match="ZERNIO_API_KEY"):
        _provider(mocker).publish_video("sid", clip_file, "t", "c")


def test_provider_loads_the_account_it_publishes_for(db_session, mocker):
    user = _user(db_session)
    account = _account(db_session, user, SocialProvider.INSTAGRAM, external="acc_ig")
    mocker.patch("app.core.db.SessionLocal", return_value=db_session)

    assert ZernioProvider()._load(account.id) == ("acc_ig", "instagram")
