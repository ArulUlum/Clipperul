from app.models.enums import AspectRatio
from app.services.video.exceptions import PipelineError
from app.workers.tasks.video_tasks import _default_clip_aspect_ratio


def test_widescreen_16_9_source_defaults_to_3_4(mocker):
    mocker.patch("app.workers.tasks.video_tasks.pipeline.get_video_dimensions", return_value=(1920, 1080))
    assert _default_clip_aspect_ratio("irrelevant.mp4") == AspectRatio.PORTRAIT_3_4


def test_near_16_9_source_within_tolerance_defaults_to_3_4(mocker):
    # 1918x1080 (a common near-16:9 encoder quirk) should still count as "originally 16:9".
    mocker.patch("app.workers.tasks.video_tasks.pipeline.get_video_dimensions", return_value=(1918, 1080))
    assert _default_clip_aspect_ratio("irrelevant.mp4") == AspectRatio.PORTRAIT_3_4


def test_already_vertical_source_keeps_9_16_default(mocker):
    mocker.patch("app.workers.tasks.video_tasks.pipeline.get_video_dimensions", return_value=(1080, 1920))
    assert _default_clip_aspect_ratio("irrelevant.mp4") == AspectRatio.VERTICAL_9_16


def test_square_source_keeps_9_16_default(mocker):
    mocker.patch("app.workers.tasks.video_tasks.pipeline.get_video_dimensions", return_value=(1080, 1080))
    assert _default_clip_aspect_ratio("irrelevant.mp4") == AspectRatio.VERTICAL_9_16


def test_dimension_probe_failure_falls_back_to_9_16(mocker):
    mocker.patch(
        "app.workers.tasks.video_tasks.pipeline.get_video_dimensions",
        side_effect=PipelineError("PROBE_FAILED", "nope"),
    )
    assert _default_clip_aspect_ratio("irrelevant.mp4") == AspectRatio.VERTICAL_9_16
