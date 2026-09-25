from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.clip import Clip
from app.models.enums import ClipStatus, ScheduledPostStatus, SocialProvider, VideoCategory
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.models.video import Video


def _setup(
    db_session, *, clip_status: ClipStatus = ClipStatus.READY
) -> tuple[User, Clip, SocialAccount, SocialAccount]:
    user = User(email="scheduler@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    video = Video(
        user_id=user.id,
        title="Source video",
        original_filename="source.mp4",
        storage_key="videos/source.mp4",
        file_size_bytes=1000,
        duration_seconds=120.0,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)

    clip = Clip(
        video_id=video.id,
        title="Highlight",
        start_time=10.0,
        end_time=25.0,
        score=80.0,
        category=VideoCategory.GAMING,
        status=clip_status,
        storage_key="clips/clip.mp4" if clip_status == ClipStatus.READY else None,
        duration_seconds=15.0,
    )
    db_session.add(clip)
    db_session.commit()
    db_session.refresh(clip)

    account = SocialAccount(
        user_id=user.id,
        provider=SocialProvider.YOUTUBE,
        account_name="My Channel",
        external_account_id="UC1",
        encrypted_access_token="enc",
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    account2 = SocialAccount(
        user_id=user.id,
        provider=SocialProvider.TIKTOK,
        account_name="My TikTok",
        external_account_id="tt1",
        encrypted_access_token="enc",
        connected_at=datetime.now(timezone.utc),
    )
    db_session.add(account2)
    db_session.commit()
    db_session.refresh(account2)

    return user, clip, account, account2


def _future(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_create_scheduled_post_success(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id)],
                "scheduled_at": _future(),
                "title": "My clip",
                "caption": "Check this out",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert len(body) == 1
        assert body[0]["status"] == "SCHEDULED"
        assert body[0]["platform"] == "youtube"
        assert body[0]["clip_title"] == "Highlight"
        assert body[0]["account_name"] == "My Channel"
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_for_two_accounts_at_once(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, account2 = _setup(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id), str(account2.id)],
                "scheduled_at": _future(),
                "title": "My clip",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert len(body) == 2
        platforms = {row["platform"] for row in body}
        assert platforms == {"youtube", "tiktok"}
        # Independent rows, not a shared one — each gets its own id/retry history.
        assert body[0]["id"] != body[1]["id"]

        posts = db_session.query(ScheduledPost).filter_by(clip_id=clip.id).all()
        assert len(posts) == 2
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_publish_now_dispatches_immediately(db_session, mocker):
    delay_mock = mocker.patch("app.api.scheduled_posts.publish_scheduled_post_task.delay")
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, account2 = _setup(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id), str(account2.id)],
                "publish_now": True,
                "title": "My clip",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert len(body) == 2
        assert all(row["status"] == "PROCESSING" for row in body)
        assert delay_mock.call_count == 2
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_without_scheduled_at_or_publish_now_rejected(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={"clip_id": str(clip.id), "social_account_ids": [str(account.id)], "title": "My clip"},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_rejects_clip_not_ready(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session, clip_status=ClipStatus.RENDERING)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id)],
                "scheduled_at": _future(),
                "title": "My clip",
            },
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_rejects_past_time(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id)],
                "scheduled_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "title": "My clip",
            },
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_rejects_unowned_clip(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        _, clip, account, _ = _setup(db_session)
        other = User(email="other-scheduler@example.com", hashed_password=hash_password("supersecret123"))
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        token = create_access_token(other.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id)],
                "scheduled_at": _future(),
                "title": "My clip",
            },
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_create_scheduled_post_rejects_unowned_social_account(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        other = User(email="other-account-owner@example.com", hashed_password=hash_password("supersecret123"))
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)
        other_account = SocialAccount(
            user_id=other.id,
            provider=SocialProvider.YOUTUBE,
            account_name="Not yours",
            external_account_id="UC-other",
            encrypted_access_token="enc",
            connected_at=datetime.now(timezone.utc),
        )
        db_session.add(other_account)
        db_session.commit()
        db_session.refresh(other_account)

        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.post(
            "/api/scheduled-posts",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "clip_id": str(clip.id),
                "social_account_ids": [str(account.id), str(other_account.id)],
                "scheduled_at": _future(),
                "title": "My clip",
            },
        )
        assert response.status_code == 404
        # Nothing should be created when one of the accounts in the batch is invalid.
        assert db_session.query(ScheduledPost).filter_by(clip_id=clip.id).count() == 0
    finally:
        app.dependency_overrides.clear()


def test_list_scheduled_posts_only_returns_current_users_posts(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        other = User(email="other-viewer@example.com", hashed_password=hash_password("supersecret123"))
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        db_session.add(
            ScheduledPost(
                clip_id=clip.id,
                social_account_id=account.id,
                platform=SocialProvider.YOUTUBE,
                scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
                title="Scheduled",
                status=ScheduledPostStatus.SCHEDULED,
            )
        )
        db_session.commit()

        token = create_access_token(other.id)
        client = TestClient(app)
        response = client.get("/api/scheduled-posts", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides.clear()


def test_update_scheduled_post_while_still_scheduled(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        post = ScheduledPost(
            clip_id=clip.id,
            social_account_id=account.id,
            platform=SocialProvider.YOUTUBE,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            title="Old title",
            status=ScheduledPostStatus.SCHEDULED,
        )
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)

        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.patch(
            f"/api/scheduled-posts/{post.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "New title"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "New title"
    finally:
        app.dependency_overrides.clear()


def test_update_scheduled_post_after_claimed_rejected(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        post = ScheduledPost(
            clip_id=clip.id,
            social_account_id=account.id,
            platform=SocialProvider.YOUTUBE,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            title="Old title",
            status=ScheduledPostStatus.PROCESSING,
        )
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)

        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.patch(
            f"/api/scheduled-posts/{post.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "New title"},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_cancel_scheduled_post_while_still_scheduled(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        post = ScheduledPost(
            clip_id=clip.id,
            social_account_id=account.id,
            platform=SocialProvider.YOUTUBE,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            title="Title",
            status=ScheduledPostStatus.SCHEDULED,
        )
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)

        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.delete(f"/api/scheduled-posts/{post.id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 204

        db_session.refresh(post)
        assert post.status == ScheduledPostStatus.CANCELLED
    finally:
        app.dependency_overrides.clear()


def test_cancel_scheduled_post_after_claimed_rejected(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        user, clip, account, _ = _setup(db_session)
        post = ScheduledPost(
            clip_id=clip.id,
            social_account_id=account.id,
            platform=SocialProvider.YOUTUBE,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            title="Title",
            status=ScheduledPostStatus.PUBLISHED,
        )
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)

        token = create_access_token(user.id)
        client = TestClient(app)
        response = client.delete(f"/api/scheduled-posts/{post.id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()
