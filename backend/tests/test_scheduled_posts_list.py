from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.api import deps
from app.core.security import create_access_token
from app.main import app
from app.models.enums import PublishJobStatus, ScheduledPostStatus, SocialProvider
from app.models.publish_job import PublishJob
from app.models.scheduled_post import ScheduledPost
from tests.test_scheduled_posts import _setup

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _post(db, clip, account, status=ScheduledPostStatus.SCHEDULED, minutes=0):
    post = ScheduledPost(
        clip_id=clip.id, social_account_id=account.id, platform=SocialProvider.YOUTUBE, title="t", status=status,
        scheduled_at=NOW + timedelta(minutes=minutes),
    )
    db.add(post)
    db.commit()
    return post


def _list(db_session, user):
    """The response, and how many SQL statements it took."""
    statements = []
    engine = db_session.get_bind()
    listener = lambda conn, cursor, statement, *args: statements.append(statement)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        response = TestClient(app).get(
            "/api/scheduled-posts", headers={"Authorization": f"Bearer {create_access_token(user.id)}"}
        )
    finally:
        app.dependency_overrides.clear()
        event.remove(engine, "before_cursor_execute", listener)
    assert response.status_code == 200
    return response.json(), len(statements)


def test_the_number_of_queries_does_not_grow_with_the_number_of_posts(db_session):
    user, clip, account, _ = _setup(db_session)
    _post(db_session, clip, account, ScheduledPostStatus.FAILED)
    _, with_one = _list(db_session, user)

    for i in range(1, 25):
        _post(db_session, clip, account, ScheduledPostStatus.FAILED if i % 2 else ScheduledPostStatus.SCHEDULED, minutes=i)
    posts, with_many = _list(db_session, user)

    assert len(posts) == 25
    assert with_many == with_one  # it used to be a few more queries for every post


def test_the_shown_error_is_the_latest_job_and_only_for_failed_or_processing_posts(db_session):
    user, clip, account, _ = _setup(db_session)
    failed = _post(db_session, clip, account, ScheduledPostStatus.FAILED, minutes=0)
    published = _post(db_session, clip, account, ScheduledPostStatus.PUBLISHED, minutes=1)
    for post, message, age in [(failed, "old failure", 10), (failed, "newest failure", 1), (published, "stale error", 1)]:
        db_session.add(PublishJob(
            scheduled_post_id=post.id, status=PublishJobStatus.FAILED, error_message=message,
            created_at=NOW - timedelta(minutes=age),
        ))
    db_session.commit()

    posts, _ = _list(db_session, user)

    by_id = {p["id"]: p for p in posts}
    assert by_id[str(failed.id)]["latest_error"] == "newest failure"
    assert by_id[str(published.id)]["latest_error"] is None
    assert [p["id"] for p in posts] == [str(failed.id), str(published.id)]  # still ordered by scheduled time


def test_a_user_with_no_posts_gets_an_empty_list(db_session):
    user, *_ = _setup(db_session)
    assert _list(db_session, user)[0] == []
