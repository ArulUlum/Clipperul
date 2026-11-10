import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.security import create_access_token
from app.main import app
from app.models.enums import AspectRatio, ClipStatus
from app.services.video import layout
from tests.test_clip_apply_edits import _setup_clip


# ---------------------------------------------------------------- geometry and the headline


@pytest.mark.parametrize("ratio,expected", [("1:1", (1080, 1080, 420)), ("4:3", (1080, 810, 555)), ("3:4", (1080, 1440, 240))])
def test_the_clip_fills_the_width_and_sits_in_the_middle(ratio, expected):
    assert layout.content_box(ratio) == expected


def test_only_the_news_layout_on_a_non_vertical_crop_applies():
    assert layout.applies("news", "1:1") and layout.applies("news", "4:3")
    assert not layout.applies("news", "9:16")  # already fills the screen
    assert not layout.applies("full", "1:1") and not layout.applies(None, "1:1")


def test_subtitles_default_to_just_below_the_clip_but_a_chosen_spot_wins():
    x, y = layout.subtitle_position("news", "1:1", None, None)
    assert x == 50.0 and y == pytest.approx((1500 + 110) / 1920 * 100, abs=0.01)
    assert layout.subtitle_position("news", "1:1", 30.0, 70.0) == (30.0, 70.0)
    assert layout.subtitle_position("full", "1:1", None, None) == (None, None)  # the preset's own spot
    # 3:4 leaves only a 240 px band; 110 px below the clip still sits inside it
    _, y = layout.subtitle_position("news", "3:4", None, None)
    assert y == pytest.approx((1680 + 110) / 1920 * 100, abs=0.01)


def test_headline_is_the_title_above_the_clip_and_tags_in_it_are_defused():
    ass = layout.build_headline_ass("Prabowo {\\b1} di podium\\N lagi", "1:1")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    line = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
    assert "\\an2\\pos(540,380)" in line  # bottom-anchored, 40 px above the clip's top edge (420)
    assert line.endswith("Prabowo (/b1) di podium/N lagi")  # no override tags or line commands get through


def test_apply_passes_through_when_the_layout_does_not_apply(mocker):
    compose = mocker.patch.object(layout, "compose_news")
    assert layout.apply("full", "/t/crop.mp4", "/t/out.mp4", "T", "1:1") == "/t/crop.mp4"
    assert layout.apply("news", "/t/crop.mp4", "/t/out.mp4", "T", "9:16") == "/t/crop.mp4"
    compose.assert_not_called()
    assert layout.apply("news", "/t/crop.mp4", "/t/out.mp4", "T", "1:1") == "/t/out.mp4"
    compose.assert_called_once_with("/t/crop.mp4", "/t/out.mp4", "T", "1:1")


def test_compose_runs_one_ffmpeg_pass_with_the_headline(mocker, tmp_path):
    run = mocker.patch("app.services.video.pipeline._run")
    out = tmp_path / "out.mp4"
    layout.compose_news("/t/crop.mp4", str(out), "Judul", "1:1")

    cmd = run.call_args.args[0]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=0:420" in graph and "scale=1080:1080" in graph and "ass=out.headline.ass" in graph
    assert run.call_args.kwargs["cwd"] == str(tmp_path)
    assert "Judul" in (tmp_path / "out.headline.ass").read_text(encoding="utf-8")


# ---------------------------------------------------------------- API


def _client(db_session):
    app.dependency_overrides[deps.get_db] = lambda: db_session
    return TestClient(app)


def test_editor_can_switch_the_layout_and_omitting_it_keeps_it(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    _, _, clip = _setup_clip(db_session)
    headers = {"Authorization": f"Bearer {create_access_token(clip.video.user_id)}"}
    client = _client(db_session)
    try:
        body = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json={
            "start_time": 10.0, "end_time": 25.0, "aspect_ratio": "1:1", "layout": "news",
        }).json()
        assert body["layout"] == "news"
        body = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json={
            "start_time": 10.0, "end_time": 25.0, "aspect_ratio": "1:1",
        }).json()
        assert body["layout"] == "news"
        bad = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json={
            "start_time": 10.0, "end_time": 25.0, "aspect_ratio": "1:1", "layout": "poster",
        })
        assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_a_new_title_rerenders_a_news_layout_clip_because_the_headline_is_burned_in(db_session, mocker):
    delay = mocker.patch("app.api.clips.retrim_clip_task.delay")
    _, _, clip = _setup_clip(db_session)
    clip.layout, clip.aspect_ratio = "news", AspectRatio.SQUARE_1_1
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(clip.video.user_id)}"}
    client = _client(db_session)
    try:
        body = client.patch(f"/api/clips/{clip.id}", headers=headers, json={"title": "Judul baru"}).json()
        assert body["title"] == "Judul baru" and body["status"] == "RENDERING"
        delay.assert_called_once_with(str(clip.id))

        # same title again, or only the caption: nothing to re-render
        clip.status = ClipStatus.READY
        db_session.commit()
        client.patch(f"/api/clips/{clip.id}", headers=headers, json={"title": "Judul baru", "caption": "x"})
        assert delay.call_count == 1
    finally:
        app.dependency_overrides.clear()


def test_a_new_title_on_a_full_frame_clip_is_just_saved(db_session, mocker):
    delay = mocker.patch("app.api.clips.retrim_clip_task.delay")
    _, _, clip = _setup_clip(db_session)
    headers = {"Authorization": f"Bearer {create_access_token(clip.video.user_id)}"}
    client = _client(db_session)
    try:
        body = client.patch(f"/api/clips/{clip.id}", headers=headers, json={"title": "Judul baru"}).json()
        assert body["status"] == "READY" and body["layout"] == "full"
        delay.assert_not_called()
    finally:
        app.dependency_overrides.clear()
