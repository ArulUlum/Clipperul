from app.services.ai.highlight_service import _snap_to_segment_boundaries
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.pipeline import _center_crop_scale_filter, _positioned_crop_scale_filter


def test_center_crop_filter_targets_exact_output_resolution():
    filt = _center_crop_scale_filter(1080, 1920)
    assert "scale=1080:1920" in filt
    assert "crop=" in filt
    # No pad/letterbox filter should ever be present — this must always be a hard crop.
    assert "pad=" not in filt


def test_center_crop_filter_uses_centered_offsets():
    filt = _center_crop_scale_filter(1080, 1920)
    assert "(iw-ow)/2" in filt
    assert "(ih-oh)/2" in filt


def test_positioned_crop_filter_targets_exact_output_resolution():
    filt = _positioned_crop_scale_filter(1080, 1920, position_x_pct=30.0, position_y_pct=70.0)
    assert "scale=1080:1920" in filt
    assert "crop=" in filt
    assert "pad=" not in filt


def test_positioned_crop_filter_embeds_the_chosen_fraction():
    filt = _positioned_crop_scale_filter(1080, 1920, position_x_pct=25.0, position_y_pct=75.0)
    assert "iw*0.25" in filt
    assert "ih*0.75" in filt


def test_positioned_crop_filter_clamps_out_of_range_percentages():
    filt = _positioned_crop_scale_filter(1080, 1920, position_x_pct=-10.0, position_y_pct=120.0)
    assert "iw*0.0" in filt
    assert "ih*1.0" in filt


def _segments():
    return [
        TranscriptSegmentResult(start=0.0, end=4.0, text="First sentence here."),
        TranscriptSegmentResult(start=4.2, end=9.5, text="Second sentence, the good part."),
        TranscriptSegmentResult(start=9.8, end=14.0, text="Third sentence wraps it up."),
    ]


def test_snap_extends_start_and_end_to_segment_boundaries():
    # A candidate that starts/ends mid-sentence relative to the second segment.
    start, end = _snap_to_segment_boundaries(5.0, 8.0, _segments())
    assert start == 4.2  # snapped back to the start of the segment containing 5.0
    assert end == 9.5  # snapped forward to the end of the segment containing 8.0


def test_snap_is_noop_with_no_segments():
    assert _snap_to_segment_boundaries(5.0, 8.0, []) == (5.0, 8.0)


def test_snap_falls_back_when_it_would_blow_up_the_range():
    huge_segment = [TranscriptSegmentResult(start=0.0, end=500.0, text="one giant segment")]
    # Original range is only 3s; snapping to the giant segment would balloon it far past the
    # +20s safety margin, so the original range should be kept instead.
    start, end = _snap_to_segment_boundaries(100.0, 103.0, huge_segment)
    assert (start, end) == (100.0, 103.0)
