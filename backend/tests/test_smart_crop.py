from app.services.video.pipeline import get_target_dimensions
from app.services.video.smart_crop import (
    compute_crop_box,
    crop_x_offset,
    fill_and_smooth_centers,
    interpolate_center,
)


def test_get_target_dimensions_known_ratio():
    assert get_target_dimensions("9:16") == (1080, 1920)
    assert get_target_dimensions("1:1") == (1080, 1080)
    assert get_target_dimensions("3:4") == (1080, 1440)
    assert get_target_dimensions("4:3") == (1440, 1080)


def test_get_target_dimensions_unknown_ratio_falls_back_to_9_16():
    assert get_target_dimensions("21:9") == (1080, 1920)


def test_fill_and_smooth_centers_returns_none_with_no_detections():
    assert fill_and_smooth_centers([], duration=30.0) is None


def test_fill_and_smooth_centers_anchors_start_and_end():
    keyframes = fill_and_smooth_centers([(5.0, 0.3), (10.0, 0.7)], duration=15.0, smoothing_window=1)
    assert keyframes is not None
    times = [t for t, _ in keyframes]
    assert times[0] == 0.0
    assert times[-1] == 15.0


def test_fill_and_smooth_centers_smooths_out_a_single_spike():
    # A lone outlier detection surrounded by stable values should be pulled toward its
    # neighbors by the moving average, not passed straight through.
    samples = [(t, 0.5) for t in range(0, 10)]
    samples[5] = (5.0, 0.95)
    keyframes = fill_and_smooth_centers(samples, duration=9.0, smoothing_window=5)
    assert keyframes is not None
    spike_value = dict(keyframes)[5.0]
    assert spike_value < 0.95


def test_interpolate_center_linear_between_keyframes():
    keyframes = [(0.0, 0.2), (10.0, 0.8)]
    assert interpolate_center(keyframes, 5.0) == 0.5


def test_interpolate_center_clamps_outside_range():
    keyframes = [(2.0, 0.3), (8.0, 0.6)]
    assert interpolate_center(keyframes, 0.0) == 0.3
    assert interpolate_center(keyframes, 100.0) == 0.6


def test_compute_crop_box_crops_width_for_wide_source():
    # 1920x1080 source -> 9:16 target: width is the constrained dimension.
    crop_w, crop_h = compute_crop_box(1920, 1080, 1080, 1920)
    assert crop_h == 1080
    assert crop_w == round(1080 * (1080 / 1920))


def test_compute_crop_box_crops_height_for_tall_source():
    crop_w, crop_h = compute_crop_box(1080, 1920, 1920, 1080)
    assert crop_w == 1080
    assert crop_h == round(1080 / (1920 / 1080))


def test_crop_x_offset_centers_on_fraction():
    assert crop_x_offset(0.5, src_w=1000, crop_w=200) == 400


def test_crop_x_offset_clamps_to_left_edge():
    assert crop_x_offset(0.0, src_w=1000, crop_w=200) == 0


def test_crop_x_offset_clamps_to_right_edge():
    assert crop_x_offset(1.0, src_w=1000, crop_w=200) == 800
