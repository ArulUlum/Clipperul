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


# ---------------------------------------------------------------- gaming layout

from app.services.video import gaming_layout  # noqa: E402
from app.services.video.gaming_layout import Facecam  # noqa: E402


def _facecam_detections(frames=10, *, extra=()):
    """A steady facecam in the bottom-left corner on every sampled frame, plus any extra detections."""
    dets = [(i, 0.15 + (i % 2) * 0.004, 0.78, 0.12) for i in range(frames)]
    return dets + list(extra)


def test_the_steady_face_wins_over_one_that_comes_and_goes():
    # a game character's face shows up big in the middle on three frames
    game_faces = [(i, 0.5, 0.4, 0.3) for i in (2, 5, 8)]
    cam = gaming_layout.facecam_from_detections(_facecam_detections(extra=game_faces), sampled_frames=10)
    assert cam is not None
    assert cam.x == pytest.approx(15.2, abs=0.3)
    height = 0.12 * 2.2  # head and shoulders around the face
    assert cam.size == pytest.approx(height * 100, abs=0.01)
    assert cam.y == pytest.approx((0.78 + height * 0.2) * 100, abs=0.01)  # the face sits in the upper third


def test_no_facecam_when_faces_are_only_occasional():
    occasional = [(i, 0.5, 0.4, 0.2) for i in (1, 7)]
    assert gaming_layout.facecam_from_detections(occasional, sampled_frames=20) is None
    assert gaming_layout.facecam_from_detections([], sampled_frames=20) is None


def test_crops_take_the_shape_of_their_part_of_the_screen_and_stay_inside_the_frame():
    face, game = gaming_layout.crop_boxes(1920, 1080, Facecam(x=5.0, y=95.0, size=30.0), 50.0)
    assert face == (454, 324, 0, 756)  # 1080:768 shape, pushed back inside at the bottom-left corner
    assert game == (1012, 1080, 454, 0)  # 1080:1152 shape, full height, centered
    _, game_right = gaming_layout.crop_boxes(1920, 1080, Facecam(50, 50, 30), 100.0)
    assert game_right == (1012, 1080, 908, 0)  # slid to the right edge, not past it
    # a portrait source too narrow for the game crop's height: it shrinks to the frame's width
    _, narrow = gaming_layout.crop_boxes(720, 1280, Facecam(50, 20, 20), 50.0)
    assert narrow[0] == 720 and narrow[1] == 768


def test_the_two_parts_fill_the_whole_canvas_40_60():
    assert gaming_layout.FACE_H + gaming_layout.GAME_H == 1920
    assert gaming_layout.FACE_H / 1920 == pytest.approx(0.4)


def test_gaming_compose_stacks_face_over_game_full_width(mocker):
    mocker.patch("app.services.video.pipeline.get_video_dimensions", return_value=(1920, 1080))
    run = mocker.patch("app.services.video.pipeline._run")
    gaming_layout.compose("/t/trim.mp4", "/t/out.mp4", Facecam(15, 78, 31.2), 50.0)
    cmd = run.call_args.args[0]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=472:336:52:674,scale=1080:768" in graph
    assert "crop=1012:1080:454:0,scale=1080:1152" in graph
    assert "[face][game]vstack[out]" in graph and "boxblur" not in graph  # nothing left over to fill


def test_render_uses_a_given_facecam_detects_otherwise_and_reports_no_face(mocker):
    compose = mocker.patch.object(gaming_layout, "compose")
    detect = mocker.patch.object(gaming_layout, "detect_facecam", return_value=Facecam(10, 80, 30))
    given = Facecam(20, 70, 25)
    assert gaming_layout.render("/t/in.mp4", "/t/out.mp4", given, 30.0) == given
    detect.assert_not_called()
    compose.assert_called_with("/t/in.mp4", "/t/out.mp4", given, 30.0)

    assert gaming_layout.render("/t/in.mp4", "/t/out.mp4", None, None) == Facecam(10, 80, 30)
    compose.assert_called_with("/t/in.mp4", "/t/out.mp4", Facecam(10, 80, 30), 50.0)

    detect.return_value = None
    compose.reset_mock()
    assert gaming_layout.render("/t/in.mp4", "/t/out.mp4", None, None) is None
    compose.assert_not_called()


def test_gaming_without_a_face_falls_back_to_news_and_subtitles_sit_on_the_seam():
    assert layout.effective("gaming", True) == "gaming"
    assert layout.effective("gaming", False) == "news"
    assert layout.effective("news", False) == "news" and layout.effective(None, False) == "full"
    assert layout.subtitle_position("gaming", "1:1", None, None) == (50.0, 40.0)  # on the seam


def test_editor_sets_or_clears_the_facecam_and_rejects_half_of_one(db_session, mocker):
    mocker.patch("app.api.clips.retrim_clip_task.delay")
    _, _, clip = _setup_clip(db_session)
    headers = {"Authorization": f"Bearer {create_access_token(clip.video.user_id)}"}
    client = _client(db_session)
    base = {"start_time": 10.0, "end_time": 25.0, "aspect_ratio": "1:1", "layout": "gaming"}
    try:
        body = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json={
            **base, "facecam_x": 12.0, "facecam_y": 80.0, "facecam_size": 30.0,
        }).json()
        assert (body["layout"], body["facecam_x"], body["facecam_y"], body["facecam_size"]) == ("gaming", 12.0, 80.0, 30.0)

        body = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json=base).json()
        assert body["facecam_x"] is None  # detect again on the render

        half = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json={**base, "facecam_x": 12.0})
        assert half.status_code == 400
        tiny = client.post(f"/api/clips/{clip.id}/apply-edits", headers=headers, json={
            **base, "facecam_x": 12.0, "facecam_y": 80.0, "facecam_size": 2.0,
        })
        assert tiny.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_a_gaming_clip_that_fell_back_to_news_rerenders_on_a_new_title(db_session, mocker):
    delay = mocker.patch("app.api.clips.retrim_clip_task.delay")
    _, _, clip = _setup_clip(db_session)
    clip.layout, clip.aspect_ratio = "gaming", AspectRatio.SQUARE_1_1
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(clip.video.user_id)}"}
    client = _client(db_session)
    try:
        client.patch(f"/api/clips/{clip.id}", headers=headers, json={"title": "Headline baru"})
        delay.assert_called_once()  # no facecam -> news layout -> the title is burned in

        clip.status, clip.facecam_x, clip.facecam_y, clip.facecam_size = ClipStatus.READY, 10.0, 80.0, 30.0
        db_session.commit()
        client.patch(f"/api/clips/{clip.id}", headers=headers, json={"title": "Lagi"})
        assert delay.call_count == 1  # the gaming layout has no headline
    finally:
        app.dependency_overrides.clear()
