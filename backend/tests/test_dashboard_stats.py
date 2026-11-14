from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.clip import Clip
from app.models.enums import ClipStatus, JobStatus, VideoCategory
from app.models.user import User
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob


def _user(db, email):
    user = User(email=email, hashed_password=hash_password("supersecret123"))
    db.add(user)
    db.commit()
    return user


def _video(db, user, *, clips=0, jobs=()):
    video = Video(user_id=user.id, title="v", original_filename="v.mp4", storage_key="k", file_size_bytes=1)
    db.add(video)
    db.commit()
    for _ in range(clips):
        db.add(Clip(video_id=video.id, title="c", start_time=0, end_time=20, score=1, category=VideoCategory.GAMING,
                    status=ClipStatus.READY, duration_seconds=20.0))
    for status in jobs:
        db.add(VideoProcessingJob(video_id=video.id, status=status))
    db.commit()


def test_dashboard_counts_are_right_and_private_to_each_user(db_session):
    mine, other = _user(db_session, "me@example.com"), _user(db_session, "other@example.com")
    _video(db_session, mine, clips=2, jobs=[JobStatus.PENDING, JobStatus.RUNNING, JobStatus.COMPLETED])
    _video(db_session, mine, clips=1, jobs=[JobStatus.FAILED])
    _video(db_session, other, clips=5, jobs=[JobStatus.RUNNING])
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        body = TestClient(app).get(
            "/api/dashboard/stats", headers={"Authorization": f"Bearer {create_access_token(mine.id)}"}
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert body["total_videos"] == 2 and body["total_clips"] == 3
    assert body["processing_jobs"] == 2  # only PENDING and RUNNING, and only this user's
