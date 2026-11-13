"""The daily auto-clip: trending discovery, the once-a-day schedule, never reusing a link,
1:1 / no-auto-track output, and uploading one clip at a time."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.auto_clip import AutoClipRun, AutoClipSettings, AutoClipSource
from app.models.clip import Clip
from app.models.enums import (
    AspectRatio, ClipStatus, ScheduledPostStatus, SocialProvider, VideoCategory, VideoStatus,
)
from app.models.scheduled_post import ScheduledPost
from app.models.social_account import SocialAccount
from app.models.user import User
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob
from app.services.auto_clip import service, trending
from app.services.auto_clip.trending import Candidate, TrendingError

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)  # 10:00 in Asia/Jakarta (UTC+7)


# ----------------------------------------------------------------------------- trending

@pytest.mark.parametrize(
    "value,seconds",
    [("PT1H2M3S", 3723), ("PT45M", 2700), ("PT30S", 30), ("P1DT1H", 90000), ("P0D", 0), ("garbage", 0), ("", 0)],
)
def test_parse_iso8601_duration(value, seconds):
    assert trending.parse_iso8601_duration(value) == seconds


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=5", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://example.com/video.mp4", None),
        (None, None),
    ],
)
def test_extract_youtube_id(url, expected):
    assert trending.extract_youtube_id(url) == expected


def _cand(video_id="a" * 11, seconds=1800, live=False, views=100, title="T"):
    return Candidate(video_id=video_id, title=title, channel="C", duration_seconds=seconds, views=views, is_live=live)


def test_filter_drops_used_live_too_short_too_long_and_duplicates_but_keeps_order():
    cands = [
        _cand("a" * 11), _cand("b" * 11), _cand("c" * 11, live=True), _cand("d" * 11, seconds=60),
        _cand("e" * 11, seconds=4 * 3600), _cand("f" * 11), _cand("f" * 11),
    ]
    kept = trending.filter_candidates(cands, {"b" * 11}, min_seconds=480, max_seconds=4500)
    assert [c.video_id for c in kept] == ["a" * 11, "f" * 11]


def _http(mocker, *payloads, status=200):
    responses = []
    for payload in payloads:
        r = mocker.MagicMock()
        r.status_code = status
        r.json.return_value = payload
        responses.append(r)
    return mocker.patch("app.services.auto_clip.trending.requests.get", side_effect=responses)


def _video_item(video_id, duration="PT30M", views="1000", live="none", published=None):
    snippet = {"title": f"Title {video_id}", "channelTitle": "Chan", "liveBroadcastContent": live}
    if published:
        snippet["publishedAt"] = published
    return {
        "id": video_id,
        "snippet": snippet,
        "contentDetails": {"duration": duration},
        "statistics": {"viewCount": views},
    }


def test_chart_categories_use_the_most_popular_chart(mocker):
    get = _http(mocker, {"items": [_video_item("a" * 11), _video_item("b" * 11, live="live")]})

    cands = trending.fetch_candidates("gaming", "id", api_key="K")

    params = get.call_args.kwargs["params"]
    assert (params["chart"], params["regionCode"], params["videoCategoryId"], params["key"]) == ("mostPopular", "ID", "20", "K")
    assert [(c.video_id, c.is_live, c.duration_seconds) for c in cands] == [("a" * 11, False, 1800), ("b" * 11, True, 1800)]


def test_podcast_searches_by_views_then_fetches_durations_sorted_by_views(mocker):
    search = {"items": [{"id": {"videoId": "a" * 11}}, {"id": {"videoId": "b" * 11}}]}
    details = {"items": [_video_item("a" * 11, views="10"), _video_item("b" * 11, views="999")]}
    get = _http(mocker, search, details)

    cands = trending.fetch_candidates("podcast", "ID", access_token="TOKEN", now=NOW)

    first = get.call_args_list[0]
    assert first.args[0].endswith("/search")
    assert first.kwargs["params"]["order"] == "viewCount"
    assert first.kwargs["params"]["q"] == "podcast indonesia"  # the country is named, or results go global
    assert first.kwargs["params"]["publishedAfter"] == "2026-10-26T03:00:00Z"  # last 72h
    assert first.kwargs["headers"]["Authorization"] == "Bearer TOKEN"
    assert "key" not in first.kwargs["params"]
    assert [c.video_id for c in cands] == ["b" * 11, "a" * 11]  # most viewed first


def test_podcast_query_has_no_country_term_for_english_speaking_regions(mocker):
    get = _http(mocker, {"items": []})
    trending.fetch_candidates("podcast", "US", api_key="K")
    assert get.call_args.kwargs["params"]["q"] == "podcast"


def test_news_merges_the_chart_with_speech_podcast_and_influencer_searches_ranked_by_velocity(mocker):
    def ago(hours):
        return (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    empty = {"items": []}
    chart = {"items": [_video_item("a" * 11, views="1000", published=ago(48))]}  # 21 views/h
    speech_medium = {"items": [{"id": {"videoId": "b" * 11}}, {"id": {"videoId": "c" * 11}}]}
    podcast_long = {"items": [{"id": {"videoId": "c" * 11}}, {"id": {"videoId": "a" * 11}}]}
    details = {"items": [
        _video_item("b" * 11, views="6000", published=ago(12)),    # 500 views/h
        _video_item("c" * 11, views="100000", published=ago(70)),  # ~1429 views/h, though not the newest
        _video_item("a" * 11, views="1000", published=ago(48)),
    ]}
    # chart, then ID's three queries (speeches / podcasts / interviews) x (medium, long), then details
    get = _http(mocker, chart, speech_medium, empty, empty, podcast_long, empty, empty, details)

    cands = trending.fetch_candidates("news", "ID", api_key="K", now=NOW)

    calls = get.call_args_list
    assert calls[0].kwargs["params"]["videoCategoryId"] == "25" and calls[0].kwargs["params"]["chart"] == "mostPopular"
    searches = [c.kwargs["params"] for c in calls[1:7]]
    assert [p["videoDuration"] for p in searches] == ["medium", "long"] * 3
    assert [p["q"] for p in searches[::2]] == [
        "pidato presiden | pidato menteri | arahan presiden | sidang kabinet",  # as-is, no country suffix
        "podcast indonesia",
        "wawancara eksklusif | klarifikasi | blak-blakan | bongkar",
    ]
    assert all(p["order"] == "viewCount" and p["relevanceLanguage"] == "id" for p in searches)
    # c and a each came back from more than one place but are asked about once
    assert calls[7].kwargs["params"]["id"] == f"{'b' * 11},{'c' * 11},{'a' * 11}"
    assert [c.video_id for c in cands] == ["c" * 11, "b" * 11, "a" * 11]  # hottest per hour first


def test_news_ranking_does_not_let_a_brand_new_video_win_on_a_few_views(mocker):
    chart = {"items": [
        _video_item("a" * 11, views="30", published=(NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        _video_item("b" * 11, views="600", published=(NOW - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    ]}
    _http(mocker, chart, *[{"items": []}] * 6)
    cands = trending.fetch_candidates("news", "ID", api_key="K", now=NOW)
    # 30 views in 10 minutes would be 180/h unclamped; with the 6 h floor it is 5/h, below b's 25/h
    assert [c.video_id for c in cands] == ["b" * 11, "a" * 11]


def test_news_falls_back_to_english_queries_for_other_regions(mocker):
    get = _http(mocker, {"items": []}, *[{"items": []}] * 6)
    trending.fetch_candidates("news", "US", api_key="K")
    queries = [c.kwargs["params"]["q"] for c in get.call_args_list[1:7:2]]
    assert queries[0].startswith("president speech | prime minister speech")
    assert queries[1] == "podcast"
    assert "exclusive interview" in queries[2]


def test_api_errors_become_readable_trending_errors(mocker):
    r = mocker.MagicMock(status_code=403)
    r.json.return_value = {"error": {"message": "quota exceeded"}}
    mocker.patch("app.services.auto_clip.trending.requests.get", return_value=r)
    with pytest.raises(TrendingError, match="quota exceeded"):
        trending.fetch_candidates("gaming", "ID", api_key="K")


def test_no_credential_is_a_clear_error():
    with pytest.raises(TrendingError, match="No YouTube credential"):
        trending.fetch_candidates("gaming", "ID")


# ----------------------------------------------------------------------------- fixtures

@pytest.fixture()
def user(db_session):
    u = User(email="auto@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(u)
    db_session.commit()
    return u


def _account(db_session, user, provider=SocialProvider.YOUTUBE, ext="UC1", broker=None):
    a = SocialAccount(
        user_id=user.id, provider=provider, account_name=f"{provider.value}-{ext}", external_account_id=ext,
        encrypted_access_token="enc", connected_at=datetime.now(timezone.utc), broker=broker,
    )
    db_session.add(a)
    db_session.commit()
    return a


def _settings(db_session, user, **kw):
    fields = dict(enabled=True, category="podcast", region="ID", run_time="08:00", timezone="Asia/Jakarta", max_clips=5)
    fields.update(kw)
    st = AutoClipSettings(user_id=user.id, **fields)
    db_session.add(st)
    db_session.commit()
    return st


@pytest.fixture()
def no_dns(mocker):
    mocker.patch("app.services.video.video_service.validate_video_url")


def _start(db_session, mocker, candidates, *, run=None, user=None):
    """Runs the 'start' step for a run (creating one if needed) with trending mocked."""
    mocker.patch.object(service, "get_credentials", return_value=("KEY", None))
    mocker.patch.object(service, "fetch_candidates", return_value=candidates)
    owner = user or db_session.get(User, run.user_id)
    if service.get_settings(db_session, owner.id) is None:
        _settings(db_session, owner)
    if run is None:
        run = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 29))
    run.status = "STARTING"
    db_session.commit()
    return run, service.start_run(db_session, run.id)


# ----------------------------------------------------------------------------- schedule

@pytest.mark.parametrize(
    "now,last_run,enabled,tz,expected",
    [
        (datetime(2026, 10, 29, 0, 30, tzinfo=timezone.utc), None, True, "Asia/Jakarta", False),  # 07:30 local
        (datetime(2026, 10, 29, 1, 30, tzinfo=timezone.utc), None, True, "Asia/Jakarta", True),   # 08:30 local
        (datetime(2026, 10, 29, 1, 30, tzinfo=timezone.utc), date(2026, 10, 29), True, "Asia/Jakarta", False),  # ran today
        (datetime(2026, 10, 29, 1, 30, tzinfo=timezone.utc), date(2026, 10, 28), True, "Asia/Jakarta", True),   # ran yesterday
        (datetime(2026, 10, 29, 1, 30, tzinfo=timezone.utc), None, False, "Asia/Jakarta", False),  # switched off
        (datetime(2026, 10, 29, 1, 30, tzinfo=timezone.utc), None, True, "Not/AZone", False),  # bad tz -> UTC, 01:30 < 08:00
        # 20:00 UTC on the 28th is already 03:00 on the 29th in Jakarta — "today" is the user's day
        (datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc), date(2026, 10, 28), True, "Asia/Jakarta", False),
    ],
)
def test_is_due(now, last_run, enabled, tz, expected):
    st = SimpleNamespace(enabled=enabled, run_time="08:00", timezone=tz, last_run_date=last_run)
    assert service.is_due(st, now) is expected


def test_a_due_user_gets_exactly_one_run_per_day(db_session, user):
    st = _settings(db_session, user)

    first = service.tick(db_session, NOW)
    second = service.tick(db_session, NOW + timedelta(minutes=1))

    runs = db_session.query(AutoClipRun).all()
    assert len(runs) == 1
    assert runs[0].trigger == "scheduled" and runs[0].run_date == date(2026, 10, 29)
    assert first.runs_to_start == [str(runs[0].id)] and second.runs_to_start == []
    db_session.refresh(st)
    assert st.last_run_date == date(2026, 10, 29)

    # A new day doesn't start while yesterday's run is still going...
    service.tick(db_session, NOW + timedelta(hours=26))
    assert db_session.query(AutoClipRun).count() == 1

    # ...but once that run is finished, the next day's run time starts a fresh one.
    runs[0].status = "COMPLETED"
    db_session.commit()
    service.tick(db_session, NOW + timedelta(hours=26))
    assert db_session.query(AutoClipRun).count() == 2


def test_a_disabled_user_gets_no_run(db_session, user):
    _settings(db_session, user, enabled=False)
    service.tick(db_session, NOW)
    assert db_session.query(AutoClipRun).count() == 0


# ----------------------------------------------------------------------------- picking a video

def test_picks_the_top_unused_trending_video_as_1_1_with_tracking_off(db_session, user, mocker, no_dns):
    _settings(db_session, user)
    cands = [_cand("a" * 11, title="Top podcast"), _cand("b" * 11, title="Second")]

    run, job_id = _start(db_session, mocker, cands, user=user)

    assert job_id
    db_session.refresh(run)
    assert (run.status, run.source_video_id, run.attempts) == ("PROCESSING", "a" * 11, 1)
    video = db_session.get(Video, run.video_id)
    assert video.source_url == "https://www.youtube.com/watch?v=" + "a" * 11
    assert video.clip_aspect_ratio == AspectRatio.SQUARE_1_1
    assert video.auto_track is False
    assert video.category == VideoCategory.PODCAST  # the chosen category skips AI detection
    assert [c["id"] for c in run.remaining_candidates] == ["b" * 11]
    assert db_session.query(AutoClipSource).filter_by(user_id=user.id).one().youtube_video_id == "a" * 11


def test_a_link_used_on_an_earlier_day_is_never_picked_again(db_session, user, mocker, no_dns):
    _settings(db_session, user)
    db_session.add(AutoClipSource(user_id=user.id, youtube_video_id="a" * 11))  # used yesterday
    db_session.commit()

    run, _ = _start(db_session, mocker, [_cand("a" * 11), _cand("b" * 11)], user=user)

    db_session.refresh(run)
    assert run.source_video_id == "b" * 11


def test_a_video_the_user_uploaded_themselves_counts_as_used_too(db_session, user, mocker, no_dns):
    _settings(db_session, user)
    db_session.add(Video(
        user_id=user.id, title="mine", original_filename="x", storage_key="k", file_size_bytes=1,
        source_url="https://youtu.be/" + "a" * 11 + "?si=zzz",
    ))
    db_session.commit()

    run, _ = _start(db_session, mocker, [_cand("a" * 11), _cand("b" * 11)], user=user)

    db_session.refresh(run)
    assert run.source_video_id == "b" * 11


def test_used_links_are_per_user(db_session, user, mocker, no_dns):
    other = User(email="other@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(other)
    db_session.commit()
    db_session.add(AutoClipSource(user_id=other.id, youtube_video_id="a" * 11))
    db_session.commit()

    run, _ = _start(db_session, mocker, [_cand("a" * 11)], user=user)

    db_session.refresh(run)
    assert run.source_video_id == "a" * 11


def test_nothing_new_trending_fails_the_run_with_a_reason(db_session, user, mocker):
    run, job_id = _start(db_session, mocker, [_cand("a" * 11, live=True), _cand("b" * 11, seconds=30)], user=user)
    db_session.refresh(run)
    assert job_id is None
    assert run.status == "FAILED" and "No new trending" in run.error


def test_trending_errors_fail_the_run_with_the_reason(db_session, user, mocker):
    mocker.patch.object(service, "get_credentials", return_value=("KEY", None))
    mocker.patch.object(service, "fetch_candidates", side_effect=TrendingError("YouTube API error: quota exceeded"))
    run = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 29))
    run.status = "STARTING"
    db_session.commit()

    assert service.start_run(db_session, run.id) is None
    db_session.refresh(run)
    assert run.status == "FAILED" and "quota exceeded" in run.error


def test_expired_youtube_authorization_fails_the_run_with_a_reconnect_hint(db_session, user, mocker):
    _account(db_session, user)
    mocker.patch.object(service.YouTubeProvider, "refresh_token_if_needed", side_effect=service.YouTubeAuthExpired("x"))
    run = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 29))
    run.status = "STARTING"
    db_session.commit()

    assert service.start_run(db_session, run.id) is None
    db_session.refresh(run)
    assert run.status == "FAILED" and "reconnect your YouTube account" in run.error


def test_youtube_refresh_maps_invalid_grant_to_auth_expired(db_session, user, mocker):
    from app.core.security import encrypt_token
    from app.services.social.youtube import YouTubeAuthExpired, YouTubeProvider

    account = _account(db_session, user)
    account.encrypted_refresh_token = encrypt_token("old")
    account.token_expires_at = None  # unknown expiry -> always refresh (SQLite hands back naive datetimes)
    db_session.commit()
    mocker.patch("app.core.db.SessionLocal", return_value=db_session)
    mocker.patch(
        "app.services.social.youtube.requests.post",
        return_value=mocker.Mock(status_code=400, json=lambda: {"error": "invalid_grant"}),
    )

    with pytest.raises(YouTubeAuthExpired):
        YouTubeProvider().refresh_token_if_needed(account.id)


def test_if_a_video_cant_be_processed_the_next_trending_one_is_tried(db_session, user, mocker, no_dns):
    run, _ = _start(db_session, mocker, [_cand("a" * 11), _cand("b" * 11), _cand("c" * 11)], user=user)
    db_session.refresh(run)
    failed_video = db_session.get(Video, run.video_id)
    failed_video.status = VideoStatus.FAILED
    db_session.add(VideoProcessingJob(video_id=failed_video.id, status="FAILED", error_message="Video unavailable"))
    db_session.commit()

    result = service.tick(db_session, NOW)

    db_session.refresh(run)
    assert (run.status, run.source_video_id, run.attempts) == ("PROCESSING", "b" * 11, 2)
    assert len(result.jobs_to_process) == 1
    assert db_session.query(AutoClipSource).filter_by(user_id=user.id).count() == 2  # the broken link stays used


def test_it_gives_up_after_three_candidates(db_session, user, mocker, no_dns):
    run, _ = _start(db_session, mocker, [_cand(c * 11) for c in "abcdef"], user=user)
    for _ in range(3):
        db_session.refresh(run)
        if run.status != "PROCESSING":
            break
        video = db_session.get(Video, run.video_id)
        video.status = VideoStatus.FAILED
        db_session.commit()
        service.tick(db_session, NOW)

    db_session.refresh(run)
    assert run.status == "FAILED" and run.attempts == 3


# ----------------------------------------------------------------------------- publishing

def _processed_run(db_session, user, *, clip_scores=(90, 80, 70), max_clips=5):
    """A run whose video has finished processing with one clip per score."""
    _settings(db_session, user, max_clips=max_clips)
    video = Video(
        user_id=user.id, title="V", original_filename="x", storage_key="k", file_size_bytes=1,
        status=VideoStatus.COMPLETED, clip_aspect_ratio=AspectRatio.SQUARE_1_1, auto_track=False,
    )
    db_session.add(video)
    db_session.commit()
    for i, score in enumerate(clip_scores):
        db_session.add(Clip(
            video_id=video.id, title=f"Clip {score}", caption="cap", start_time=0, end_time=20, score=score,
            category=VideoCategory.PODCAST, status=ClipStatus.READY, storage_key=f"clips/{i}.mp4",
            duration_seconds=20.0, aspect_ratio=AspectRatio.SQUARE_1_1,
        ))
    run = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 29))
    run.status, run.video_id = "PROCESSING", video.id
    db_session.commit()
    return run


def test_finished_video_queues_the_best_clips_for_accounts_that_can_publish(db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    _account(db_session, user, SocialProvider.TIKTOK, "tt1")  # can't publish yet -> skipped
    run = _processed_run(db_session, user, max_clips=2)

    service.tick(db_session, NOW)

    db_session.refresh(run)
    assert run.status == "PUBLISHING"
    assert run.clips_total == 2  # capped by max_clips (best first); TikTok ignored
    # The best clip's upload starts right away; the other waits its turn in the queue.
    assert [(p.title, p.platform) for p in db_session.query(ScheduledPost).all()] == [("Clip 90", SocialProvider.YOUTUBE)]
    queued = [db_session.get(Clip, __import__("uuid").UUID(q["clip_id"])).title for q in run.publish_queue]
    assert queued == ["Clip 80"]


def test_no_connected_account_means_the_clips_are_made_but_nothing_is_posted(db_session, user):
    run = _processed_run(db_session, user)

    service.tick(db_session, NOW)

    db_session.refresh(run)
    assert run.status == "COMPLETED" and "nothing was posted" in run.note
    assert db_session.query(ScheduledPost).count() == 0


def test_only_tiktok_connected_counts_as_no_account(db_session, user):
    _account(db_session, user, SocialProvider.TIKTOK, "tt1")
    run = _processed_run(db_session, user)
    service.tick(db_session, NOW)
    db_session.refresh(run)
    assert run.status == "COMPLETED"


def test_clips_are_uploaded_strictly_one_at_a_time(db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    run = _processed_run(db_session, user, clip_scores=(90, 80, 70))

    first = service.tick(db_session, NOW)  # finishes processing and releases the first upload
    assert len(first.posts_to_publish) == 1
    post = db_session.query(ScheduledPost).one()
    assert post.auto_run_id == run.id and post.title == "Clip 90"
    assert post.status == ScheduledPostStatus.PROCESSING and post.run_token == first.posts_to_publish[0][1]

    # While the first is still uploading (or waiting out a retry), nothing else starts.
    for status in (ScheduledPostStatus.PROCESSING, ScheduledPostStatus.UPLOADING):
        post.status = status
        db_session.commit()
        assert service.tick(db_session, NOW).posts_to_publish == []
    assert db_session.query(ScheduledPost).count() == 1

    # Once it finishes, the next one is released — after the spacing gap (see the gap tests below).
    post.status = ScheduledPostStatus.PUBLISHED
    db_session.commit()
    assert service.tick(db_session, NOW).posts_to_publish == []
    second = service.tick(db_session, NOW + timedelta(minutes=91))
    assert len(second.posts_to_publish) == 1
    assert db_session.query(ScheduledPost).count() == 2


@pytest.mark.parametrize("final", [ScheduledPostStatus.FAILED, ScheduledPostStatus.CANCELLED])
def test_a_failed_or_cancelled_upload_does_not_block_the_rest(db_session, user, final):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    _processed_run(db_session, user, clip_scores=(90, 80))
    service.tick(db_session, NOW)
    service.tick(db_session, NOW)
    first = db_session.query(ScheduledPost).one()
    first.status = final
    db_session.commit()

    service.tick(db_session, NOW)  # starts the gap
    assert len(service.tick(db_session, NOW + timedelta(minutes=91)).posts_to_publish) == 1


def test_the_run_completes_after_the_last_upload(db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    run = _processed_run(db_session, user, clip_scores=(90,))
    service.tick(db_session, NOW)
    service.tick(db_session, NOW)
    db_session.query(ScheduledPost).one().status = ScheduledPostStatus.PUBLISHED
    db_session.commit()

    assert service.tick(db_session, NOW).posts_to_publish == []

    db_session.refresh(run)
    assert run.status == "COMPLETED"


def test_a_clip_deleted_before_its_turn_is_skipped(db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    run = _processed_run(db_session, user, clip_scores=(90, 80, 70))
    service.tick(db_session, NOW)  # Clip 90 is uploading; Clip 80 and Clip 70 wait
    db_session.refresh(run)
    doomed = db_session.get(Clip, __import__("uuid").UUID(run.publish_queue[0]["clip_id"]))
    assert doomed.title == "Clip 80"
    db_session.delete(doomed)
    db_session.query(ScheduledPost).one().status = ScheduledPostStatus.PUBLISHED
    db_session.commit()

    service.tick(db_session, NOW)  # starts the gap
    result = service.tick(db_session, NOW + timedelta(minutes=91))

    assert len(result.posts_to_publish) == 1
    assert sorted(p.title for p in db_session.query(ScheduledPost).all()) == ["Clip 70", "Clip 90"]


# ----------------------------------------------------------------------------- off switch / housekeeping

def test_turning_it_off_stops_a_scheduled_run_but_not_a_manual_one(db_session, user):
    st = _settings(db_session, user)
    scheduled = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 29))
    scheduled.status = "STARTING"
    other = User(email="m@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(other)
    db_session.commit()
    manual = service.create_run(db_session, other.id, "manual", "podcast", date(2026, 10, 29))
    manual.status = "STARTING"
    st.enabled = False
    db_session.commit()

    service.tick(db_session, NOW)

    db_session.refresh(scheduled)
    db_session.refresh(manual)
    assert scheduled.status == "CANCELLED" and "turned off" in scheduled.note
    assert manual.status == "STARTING"


# ----------------------------------------------------------------------------- stop

@pytest.mark.parametrize("stage", ["SELECTING", "STARTING", "PROCESSING", "PUBLISHING"])
def test_stop_ends_a_run_at_any_stage(db_session, user, stage):
    _settings(db_session, user)
    run = service.create_run(db_session, user.id, "manual", "podcast", date(2026, 10, 29))
    run.status = stage
    run.publish_queue = [{"clip_id": "c", "account_id": "a"}]
    run.next_publish_by_platform = {"youtube": (NOW + timedelta(hours=1)).isoformat()}
    run.remaining_candidates = [{"id": "x" * 11, "title": "T"}]
    db_session.commit()

    stopped = service.cancel_run(db_session, user.id)

    assert stopped.id == run.id and stopped.status == "CANCELLED"
    assert stopped.publish_queue == [] and stopped.next_publish_by_platform == {} and stopped.remaining_candidates == []
    assert stopped.note.startswith("Stopped by you.")
    assert not service.has_in_progress_run(db_session, user.id)


def test_stop_with_nothing_running_does_nothing(db_session, user):
    done = service.create_run(db_session, user.id, "manual", "podcast", date(2026, 10, 29))
    done.status = "COMPLETED"
    db_session.commit()
    assert service.cancel_run(db_session, user.id) is None
    db_session.refresh(done)
    assert done.status == "COMPLETED"  # a finished run is never turned into "stopped"


def test_stopping_while_the_video_is_being_clipped_explains_that_its_clips_still_appear(db_session, user):
    run = _processed_run(db_session, user)
    assert run.status == "PROCESSING"
    stopped = service.cancel_run(db_session, user.id)
    assert "still appear in your library" in stopped.note and "nothing will be posted" in stopped.note

    # when the video later finishes, the stopped run neither queues nor posts anything
    service.tick(db_session, NOW)
    db_session.refresh(run)
    assert run.status == "CANCELLED" and run.publish_queue == []
    assert db_session.query(ScheduledPost).count() == 0


def test_stop_cancels_uploads_that_have_not_finished_but_leaves_published_ones(db_session, user):
    _three_platforms(db_session, user)
    service.tick(db_session, NOW)  # one upload released per platform
    posts = {p.platform.value: p for p in db_session.query(ScheduledPost)}
    posts["youtube"].status = ScheduledPostStatus.PUBLISHED
    posts["tiktok"].status, posts["tiktok"].run_token, posts["tiktok"].progress = ScheduledPostStatus.UPLOADING, "tok", 40
    posts["instagram"].run_token = "tok2"
    run = db_session.query(AutoClipRun).one()
    # a post the user scheduled by hand (no run) must not be touched
    manual = ScheduledPost(
        clip_id=posts["youtube"].clip_id, social_account_id=posts["youtube"].social_account_id,
        platform=SocialProvider.YOUTUBE, title="mine", caption="", scheduled_at=NOW + timedelta(days=1),
        status=ScheduledPostStatus.SCHEDULED, run_token="keep",
    )
    db_session.add(manual)
    db_session.commit()

    service.cancel_run(db_session, user.id)

    for post in db_session.query(ScheduledPost).filter(ScheduledPost.auto_run_id == run.id):
        db_session.refresh(post)
    assert posts["youtube"].status == ScheduledPostStatus.PUBLISHED
    for name in ("tiktok", "instagram"):
        assert posts[name].status == ScheduledPostStatus.CANCELLED
        assert posts[name].run_token is None and posts[name].progress == 0  # clearing the token is what stops the worker
    db_session.refresh(manual)
    assert manual.status == ScheduledPostStatus.SCHEDULED and manual.run_token == "keep"

    # and nothing further is released afterwards
    assert service.tick(db_session, NOW + timedelta(hours=3)).posts_to_publish == []
    assert db_session.query(ScheduledPost).count() == 4


def test_a_stop_pressed_while_a_video_is_being_chosen_is_not_undone(db_session, user, mocker, no_dns):
    _settings(db_session, user)
    run = service.create_run(db_session, user.id, "manual", "podcast", date(2026, 10, 29))
    run.status = "STARTING"
    db_session.commit()
    mocker.patch.object(service, "get_credentials", return_value=("KEY", None))

    def slow_trending_call(*args, **kwargs):  # Stop is pressed while this is in flight
        service.cancel_run(db_session, user.id)
        return [_cand("a" * 11)]

    mocker.patch.object(service, "fetch_candidates", side_effect=slow_trending_call)

    assert service.start_run(db_session, run.id) is None
    db_session.refresh(run)
    assert run.status == "CANCELLED"
    assert db_session.query(Video).count() == 0 and db_session.query(AutoClipSource).count() == 0


def test_a_stopped_run_still_counts_as_todays_run_so_the_schedule_does_not_start_another(db_session, user):
    _settings(db_session, user)
    service.tick(db_session, NOW)  # 10:00 in Jakarta: today's scheduled run starts
    assert db_session.query(AutoClipRun).count() == 1
    service.cancel_run(db_session, user.id)

    service.tick(db_session, NOW + timedelta(minutes=5))

    assert db_session.query(AutoClipRun).count() == 1
    assert not service.has_in_progress_run(db_session, user.id)  # and "Run now" is free again (see the API test)


def test_a_run_stuck_while_choosing_a_video_times_out(db_session, user):
    _settings(db_session, user)
    run = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 29))
    run.status = "STARTING"
    run.updated_at = NOW - timedelta(minutes=30)
    db_session.commit()

    service.tick(db_session, NOW)

    db_session.refresh(run)
    assert run.status == "FAILED" and "Timed out" in run.error


# ----------------------------------------------------------------------------- API

@pytest.fixture()
def api(db_session, user):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    yield TestClient(app), {"Authorization": f"Bearer {create_access_token(user.id)}"}
    app.dependency_overrides.clear()


def _with_key(mocker):
    mocker.patch.object(service.app_settings, "YOUTUBE_API_KEY", "K")


def test_api_defaults_when_never_configured(api, mocker):
    mocker.patch.object(service.app_settings, "YOUTUBE_API_KEY", None)
    client, headers = api
    body = client.get("/api/auto-clip", headers=headers).json()
    assert body["enabled"] is False and body["category"] == "podcast" and body["can_discover"] is False
    assert {"id": "podcast", "label": "Podcast"} in body["categories"]


_BODY = {"enabled": False, "category": "podcast", "region": "id", "run_time": "08:00", "timezone": "UTC", "max_clips": 3}


def test_api_lists_the_platforms_and_which_are_connected(api, db_session, user):
    client, headers = api
    body = client.get("/api/auto-clip", headers=headers).json()
    assert body["platform_options"] == [
        {"id": "youtube", "label": "YouTube", "connected": False},
        {"id": "tiktok", "label": "TikTok", "connected": False},
        {"id": "instagram", "label": "Instagram", "connected": False},
    ]
    assert body["platforms"] == []

    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    _account(db_session, user, SocialProvider.TIKTOK, "tt1", broker="zernio")
    body = client.get("/api/auto-clip", headers=headers).json()
    assert [(o["id"], o["connected"]) for o in body["platform_options"]] == [
        ("youtube", True), ("tiktok", True), ("instagram", False),
    ]
    assert body["platforms"] == ["youtube", "tiktok"]  # never chosen: everything that is connected
    assert body["publish_accounts"] == 2


def test_api_saves_the_chosen_platforms_and_they_shape_who_gets_posted_to(api, db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    _account(db_session, user, SocialProvider.TIKTOK, "tt1", broker="zernio")
    _account(db_session, user, SocialProvider.INSTAGRAM, "ig1", broker="zernio")
    client, headers = api

    body = client.put("/api/auto-clip", headers=headers, json={**_BODY, "platforms": ["instagram", "youtube", "youtube"]}).json()

    assert body["platforms"] == ["youtube", "instagram"]  # de-duplicated, in the settings' order
    assert body["publish_accounts"] == 2
    assert client.get("/api/auto-clip", headers=headers).json()["platforms"] == ["youtube", "instagram"]
    assert {a.provider.value for a in service.publishable_accounts(db_session, user.id, ["youtube", "instagram"])} == {
        "youtube", "instagram",
    }


def test_api_rejects_a_platform_that_is_not_connected_or_not_known(api, db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    client, headers = api

    not_connected = client.put("/api/auto-clip", headers=headers, json={**_BODY, "platforms": ["youtube", "tiktok"]})
    assert not_connected.status_code == 400 and "Connect TikTok" in not_connected.json()["detail"]
    assert client.put("/api/auto-clip", headers=headers, json={**_BODY, "platforms": ["facebook"]}).status_code == 422
    # nothing was saved by the rejected requests
    assert client.get("/api/auto-clip", headers=headers).json()["platforms"] == ["youtube"]


def test_api_leaves_the_choice_alone_when_platforms_is_omitted_and_allows_choosing_none(api, db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    _account(db_session, user, SocialProvider.TIKTOK, "tt1", broker="zernio")
    client, headers = api
    client.put("/api/auto-clip", headers=headers, json={**_BODY, "platforms": ["tiktok"]})

    assert client.put("/api/auto-clip", headers=headers, json=_BODY).json()["platforms"] == ["tiktok"]

    body = client.put("/api/auto-clip", headers=headers, json={**_BODY, "platforms": []}).json()
    assert body["platforms"] == [] and body["publish_accounts"] == 0


def test_a_chosen_platform_that_gets_disconnected_drops_out_of_the_selection(api, db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    tiktok = _account(db_session, user, SocialProvider.TIKTOK, "tt1", broker="zernio")
    client, headers = api
    client.put("/api/auto-clip", headers=headers, json={**_BODY, "platforms": ["youtube", "tiktok"]})

    db_session.delete(tiktok)
    db_session.commit()

    body = client.get("/api/auto-clip", headers=headers).json()
    assert body["platforms"] == ["youtube"] and body["publish_accounts"] == 1


def test_a_run_only_posts_to_the_chosen_platforms(db_session, user):
    _three_platforms(db_session, user)
    service.get_settings(db_session, user.id).platforms = ["youtube", "instagram"]
    db_session.commit()

    result = service.tick(db_session, NOW)

    assert len(result.posts_to_publish) == 2
    assert _posts_by_platform(db_session) == {"youtube": 1, "instagram": 1}


def test_choosing_no_platform_makes_the_clips_and_posts_nothing_and_says_why(db_session, user):
    run = _three_platforms(db_session, user)
    service.get_settings(db_session, user.id).platforms = []
    db_session.commit()

    result = service.tick(db_session, NOW)

    db_session.refresh(run)
    assert result.posts_to_publish == [] and db_session.query(ScheduledPost).count() == 0
    assert run.status == "COMPLETED" and "No platform is selected" in run.note


def test_a_user_who_never_chose_posts_everywhere_they_are_connected(db_session, user):
    _three_platforms(db_session, user)
    assert service.get_settings(db_session, user.id).platforms is None
    assert len(service.tick(db_session, NOW).posts_to_publish) == 3


def test_api_cannot_enable_without_a_way_to_read_trending(api, mocker):
    mocker.patch.object(service.app_settings, "YOUTUBE_API_KEY", None)
    client, headers = api
    payload = {"enabled": True, "category": "podcast", "region": "id", "run_time": "08:00", "timezone": "UTC", "max_clips": 3}
    assert client.put("/api/auto-clip", headers=headers, json=payload).status_code == 400
    assert client.put("/api/auto-clip", headers=headers, json={**payload, "enabled": False}).status_code == 200


@pytest.mark.parametrize(
    "change",
    [{"category": "nope"}, {"run_time": "25:00"}, {"run_time": "8am"}, {"region": "IDN"}, {"region": "1d"}, {"max_clips": 0}, {"max_clips": 9}],
)
def test_api_rejects_bad_values(api, mocker, change):
    _with_key(mocker)
    client, headers = api
    payload = {"enabled": False, "category": "podcast", "region": "ID", "run_time": "08:00", "timezone": "UTC", "max_clips": 3}
    assert client.put("/api/auto-clip", headers=headers, json={**payload, **change}).status_code == 422


def test_api_rejects_an_unknown_timezone(api, mocker):
    _with_key(mocker)
    client, headers = api
    payload = {"enabled": False, "category": "podcast", "region": "ID", "run_time": "08:00", "timezone": "Mars/Base", "max_clips": 3}
    assert client.put("/api/auto-clip", headers=headers, json=payload).status_code == 400


def test_enabling_after_todays_run_time_waits_for_tomorrow(api, db_session, user, mocker):
    _with_key(mocker)
    client, headers = api
    # 00:00 has always already passed, so "enable now" must not trigger an immediate run today.
    payload = {"enabled": True, "category": "gaming", "region": "us", "run_time": "00:00", "timezone": "UTC", "max_clips": 4}

    body = client.put("/api/auto-clip", headers=headers, json=payload).json()

    assert body["enabled"] is True and body["region"] == "US" and body["category"] == "gaming" and body["max_clips"] == 4
    assert body["last_run_date"] == datetime.now(timezone.utc).date().isoformat()
    assert service.is_due(service.get_settings(db_session, user.id), datetime.now(timezone.utc)) is False


def test_run_now_needs_a_credential(api, mocker):
    mocker.patch.object(service.app_settings, "YOUTUBE_API_KEY", None)
    client, headers = api
    assert client.post("/api/auto-clip/run", headers=headers).status_code == 400


def test_run_now_starts_a_run_counts_as_todays_run_and_blocks_a_second(api, db_session, user, mocker):
    _with_key(mocker)
    tick = mocker.patch("app.api.auto_clip.auto_clip_tick.delay")
    _settings(db_session, user)
    client, headers = api

    first = client.post("/api/auto-clip/run", headers=headers)
    assert first.status_code == 202 and first.json()["trigger"] == "manual" and first.json()["status"] == "SELECTING"
    tick.assert_called_once()
    assert service.get_settings(db_session, user.id).last_run_date is not None  # the scheduled run won't double up

    assert client.post("/api/auto-clip/run", headers=headers).status_code == 409


def test_run_history_reports_publish_counts(api, db_session, user):
    _account(db_session, user)
    run = _processed_run(db_session, user, clip_scores=(90,))
    run.status, run.clips_total, run.source_title = "PUBLISHING", 3, "Big Podcast"
    clip = db_session.query(Clip).first()
    acct = db_session.query(SocialAccount).first()
    for st in (ScheduledPostStatus.PUBLISHED, ScheduledPostStatus.PUBLISHED, ScheduledPostStatus.FAILED):
        db_session.add(ScheduledPost(
            clip_id=clip.id, social_account_id=acct.id, platform=SocialProvider.YOUTUBE, scheduled_at=NOW,
            title="t", status=st, auto_run_id=run.id,
        ))
    db_session.commit()
    client, headers = api

    [item] = client.get("/api/auto-clip/runs", headers=headers).json()

    assert (item["source_title"], item["clips_total"], item["published"], item["failed"]) == ("Big Podcast", 3, 2, 1)


def test_run_history_is_private_to_each_user(api, db_session, user):
    other = User(email="spy@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(other)
    db_session.commit()
    service.create_run(db_session, other.id, "manual", "podcast", date(2026, 10, 29))
    db_session.commit()
    client, headers = api
    assert client.get("/api/auto-clip/runs", headers=headers).json() == []


def test_api_stop_ends_the_running_run_and_run_now_works_again(api, db_session, user, mocker):
    _with_key(mocker)
    mocker.patch("app.api.auto_clip.auto_clip_tick.delay")
    client, headers = api
    assert client.post("/api/auto-clip/run", headers=headers).status_code == 202
    assert client.get("/api/auto-clip", headers=headers).json()["in_progress"] is True

    response = client.post("/api/auto-clip/stop", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED" and body["note"].startswith("Stopped by you.")
    assert client.get("/api/auto-clip", headers=headers).json()["in_progress"] is False
    assert client.get("/api/auto-clip/runs", headers=headers).json()[0]["status"] == "CANCELLED"
    assert client.post("/api/auto-clip/run", headers=headers).status_code == 202  # not blocked by the stopped one


def test_api_stop_with_nothing_running_is_a_conflict(api):
    client, headers = api
    response = client.post("/api/auto-clip/stop", headers=headers)
    assert response.status_code == 409 and "No auto clip run" in response.json()["detail"]


def test_api_stop_only_touches_your_own_run(api, db_session, user):
    other = User(email="other@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(other)
    db_session.commit()
    theirs = service.create_run(db_session, other.id, "manual", "podcast", date(2026, 10, 29))
    db_session.commit()
    client, headers = api

    assert client.post("/api/auto-clip/stop", headers=headers).status_code == 409
    db_session.refresh(theirs)
    assert theirs.status == "SELECTING"


def test_a_run_stuck_for_over_two_days_is_stopped_so_it_cannot_block_later_days(db_session, user):
    _settings(db_session, user)
    stuck = service.create_run(db_session, user.id, "scheduled", "podcast", date(2026, 10, 27))
    stuck.status = "PUBLISHING"
    stuck.publish_queue = [{"clip_id": "x", "account_id": "y"}]
    stuck.created_at = NOW - timedelta(hours=60)
    db_session.commit()

    result = service.tick(db_session, NOW)

    db_session.refresh(stuck)
    assert stuck.status == "FAILED" and "too long" in stuck.error and stuck.publish_queue == []
    # ...which frees today's scheduled run to start.
    assert len(result.runs_to_start) == 1


# ----------------------------------------------------------------------------- spacing between uploads

def _one_published_then_waiting(db_session, user, mocker, *, clip_scores=(90, 80)):
    """A run whose first upload has just finished (PUBLISHED), with the rest still queued."""
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    run = _processed_run(db_session, user, clip_scores=clip_scores)
    service.tick(db_session, NOW)  # processing done; the first upload is released straight away
    db_session.query(ScheduledPost).one().status = ScheduledPostStatus.PUBLISHED
    db_session.commit()
    return run


def test_the_first_upload_of_a_run_starts_immediately(db_session, user):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    run = _processed_run(db_session, user)
    result = service.tick(db_session, NOW)
    assert len(result.posts_to_publish) == 1
    db_session.refresh(run)
    assert run.next_publish_at is None


def test_the_next_upload_waits_a_random_60_to_90_minutes(db_session, user, mocker):
    mocker.patch.object(service.random, "uniform", side_effect=lambda lo, hi: (lo + hi) / 2)
    run = _one_published_then_waiting(db_session, user, mocker)

    assert service.tick(db_session, NOW).posts_to_publish == []

    db_session.refresh(run)
    assert service._aware(run.next_publish_at) == NOW + timedelta(minutes=75)
    assert run.status == "PUBLISHING"
    # Not a minute early...
    assert service.tick(db_session, NOW + timedelta(minutes=74)).posts_to_publish == []
    assert db_session.query(ScheduledPost).count() == 1
    # ...and released as soon as the gap is over, clearing the timer for the following one.
    assert len(service.tick(db_session, NOW + timedelta(minutes=76)).posts_to_publish) == 1
    db_session.refresh(run)
    assert run.next_publish_at is None


def test_the_gap_is_random_but_always_within_60_to_90_minutes(db_session, user, mocker):
    run = _one_published_then_waiting(db_session, user, mocker)
    seen = set()
    for _ in range(25):
        run.next_publish_by_platform = {}
        db_session.commit()
        service.tick(db_session, NOW)
        db_session.refresh(run)
        minutes = (service._aware(run.next_publish_at) - NOW).total_seconds() / 60
        assert 60 <= minutes <= 90
        seen.add(round(minutes))
    assert len(seen) > 5  # genuinely varies, not a fixed delay


def test_every_upload_gets_its_own_gap_not_just_the_first(db_session, user, mocker):
    mocker.patch.object(service.random, "uniform", return_value=60)
    run = _one_published_then_waiting(db_session, user, mocker, clip_scores=(90, 80, 70))
    service.tick(db_session, NOW)
    assert len(service.tick(db_session, NOW + timedelta(minutes=61)).posts_to_publish) == 1  # second upload
    db_session.query(ScheduledPost).filter(ScheduledPost.status == ScheduledPostStatus.PROCESSING).one().status = ScheduledPostStatus.PUBLISHED
    db_session.commit()

    later = NOW + timedelta(minutes=62)
    assert service.tick(db_session, later).posts_to_publish == []  # a fresh wait starts for the third
    assert len(service.tick(db_session, later + timedelta(minutes=61)).posts_to_publish) == 1
    assert db_session.query(ScheduledPost).count() == 3


def test_there_is_no_wait_after_the_last_upload(db_session, user, mocker):
    run = _one_published_then_waiting(db_session, user, mocker, clip_scores=(90,))
    service.tick(db_session, NOW)
    db_session.refresh(run)
    assert run.status == "COMPLETED" and run.next_publish_at is None


def test_the_gap_survives_a_restart(db_session, user, mocker):
    # The deadline lives in the database, not in memory, so a restart/redeploy doesn't reset or skip it.
    mocker.patch.object(service.random, "uniform", return_value=80)
    run = _one_published_then_waiting(db_session, user, mocker)
    service.tick(db_session, NOW)
    db_session.expire_all()
    assert service.tick(db_session, NOW + timedelta(minutes=79)).posts_to_publish == []
    assert len(service.tick(db_session, NOW + timedelta(minutes=81)).posts_to_publish) == 1


# ----------------------------------------------------------------------------- one gap per platform

def _three_platforms(db_session, user, clip_scores=(90, 80)):
    _account(db_session, user, SocialProvider.YOUTUBE, "yt1")
    _account(db_session, user, SocialProvider.TIKTOK, "tt1", broker="zernio")
    _account(db_session, user, SocialProvider.INSTAGRAM, "ig1", broker="zernio")
    return _processed_run(db_session, user, clip_scores=clip_scores)


def _posts_by_platform(db_session):
    counts: dict[str, int] = {}
    for post in db_session.query(ScheduledPost):
        counts[post.platform.value] = counts.get(post.platform.value, 0) + 1
    return counts


def _finish(db_session, *platforms, status=ScheduledPostStatus.PUBLISHED):
    for post in db_session.query(ScheduledPost).filter(ScheduledPost.status != status):
        if post.platform.value in platforms:
            post.status = status
    db_session.commit()


def test_every_platform_gets_the_best_clip_at_once(db_session, user):
    _three_platforms(db_session, user)

    result = service.tick(db_session, NOW)

    assert len(result.posts_to_publish) == 3
    assert _posts_by_platform(db_session) == {"youtube": 1, "tiktok": 1, "instagram": 1}
    assert {p.title for p in db_session.query(ScheduledPost)} == {"Clip 90"}


def test_each_platform_waits_its_own_gap_and_the_others_dont_wait_for_it(db_session, user, mocker):
    mocker.patch.object(service.random, "uniform", return_value=60)
    run = _three_platforms(db_session, user)
    service.tick(db_session, NOW)
    _finish(db_session, "youtube", "instagram")  # TikTok is still uploading its first post

    assert service.tick(db_session, NOW).posts_to_publish == []  # the gaps start now
    db_session.refresh(run)
    assert set(run.next_publish_by_platform) == {"youtube", "instagram"}  # none for the busy platform

    result = service.tick(db_session, NOW + timedelta(minutes=61))

    assert len(result.posts_to_publish) == 2  # YouTube and Instagram move on...
    assert _posts_by_platform(db_session) == {"youtube": 2, "tiktok": 1, "instagram": 2}  # ...TikTok doesn't


def test_next_upload_time_shown_is_the_soonest_platform_deadline(db_session, user, mocker):
    mocker.patch.object(service.random, "uniform", side_effect=[80, 65, 90])
    run = _three_platforms(db_session, user)
    service.tick(db_session, NOW)
    _finish(db_session, "youtube", "tiktok", "instagram")

    service.tick(db_session, NOW)

    db_session.refresh(run)
    assert sorted(run.next_publish_by_platform.values()) == sorted(
        (NOW + timedelta(minutes=m)).isoformat() for m in (80, 65, 90)
    )
    assert service._aware(run.next_publish_at) == NOW + timedelta(minutes=65)


def test_the_run_is_not_complete_until_every_platform_has_finished(db_session, user):
    run = _three_platforms(db_session, user, clip_scores=(90,))
    service.tick(db_session, NOW)
    _finish(db_session, "youtube", "instagram")

    service.tick(db_session, NOW)
    db_session.refresh(run)
    assert run.status == "PUBLISHING"  # TikTok's post is still in flight

    _finish(db_session, "tiktok")
    service.tick(db_session, NOW)
    db_session.refresh(run)
    assert run.status == "COMPLETED" and run.next_publish_by_platform == {}


def test_a_disconnected_platform_is_skipped_without_stopping_the_others(db_session, user):
    run = _three_platforms(db_session, user)
    tiktok = db_session.query(SocialAccount).filter_by(external_account_id="tt1").one()
    tiktok_id = str(tiktok.id)
    db_session.delete(tiktok)
    db_session.commit()

    service.tick(db_session, NOW)

    assert _posts_by_platform(db_session) == {"youtube": 1, "instagram": 1}
    db_session.refresh(run)
    assert all(item["account_id"] != tiktok_id for item in run.publish_queue)  # its leftovers were dropped


def test_a_platforms_failed_upload_still_gets_a_pause_before_its_next(db_session, user, mocker):
    mocker.patch.object(service.random, "uniform", return_value=70)
    run = _three_platforms(db_session, user)
    service.tick(db_session, NOW)
    _finish(db_session, "tiktok", status=ScheduledPostStatus.FAILED)

    service.tick(db_session, NOW)
    db_session.refresh(run)
    assert "tiktok" in run.next_publish_by_platform
    assert service.tick(db_session, NOW + timedelta(minutes=69)).posts_to_publish == []


def test_run_history_exposes_when_the_next_upload_is_due(api, db_session, user, mocker):
    mocker.patch.object(service.random, "uniform", return_value=70)
    _one_published_then_waiting(db_session, user, mocker)
    service.tick(db_session, NOW)
    client, headers = api
    [item] = client.get("/api/auto-clip/runs", headers=headers).json()
    assert item["status"] == "PUBLISHING" and item["next_publish_at"].startswith("2026-10-29T04:10")
