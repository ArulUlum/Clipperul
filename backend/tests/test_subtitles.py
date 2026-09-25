from app.services.video.subtitles import (
    SubtitleCue,
    _hex_to_ass_color,
    _resolve_font,
    build_ass_subtitle,
    cues_from_segments,
)


def test_hex_to_ass_color_converts_rgb_to_bgr():
    # ASS colors are &H00BBGGRR — red should end up in the last two hex digits.
    assert _hex_to_ass_color("#FF0000") == "&H000000FF"
    assert _hex_to_ass_color("#00FF00") == "&H0000FF00"
    assert _hex_to_ass_color("#0000FF") == "&H00FF0000"


def test_resolve_font_maps_bold_hints_to_bold_fallback():
    assert _resolve_font("Bangers") == "DejaVu Sans Bold"
    assert _resolve_font("Montserrat-Bold") == "DejaVu Sans Bold"
    assert _resolve_font("Inter") == "DejaVu Sans"


def test_build_ass_subtitle_includes_dialogue_lines_and_style():
    cues = [
        SubtitleCue(start=0.0, end=2.5, text="That was an insane clutch!"),
        SubtitleCue(start=2.5, end=5.0, text="No way, incredible comeback."),
    ]
    preset_config = {
        "font": "Bangers",
        "size": 60,
        "position": "middle-bottom",
        "color": "#FFFFFF",
        "highlight_color": "#39FF14",
    }
    ass = build_ass_subtitle(cues, preset_config, keywords=["clutch", "comeback"])

    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "DejaVu Sans Bold,60" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,Default" in ass
    assert "Dialogue: 0,0:00:02.50,0:00:05.00,Default" in ass
    # keyword highlight override tags wrap each matched word with a color change
    assert "{\\c&H0014FF39}clutch{\\c&H00FFFFFF}" in ass
    assert "{\\c&H0014FF39}comeback{\\c&H00FFFFFF}" in ass


def test_build_ass_subtitle_handles_no_keywords():
    cues = [SubtitleCue(start=0.0, end=1.0, text="Plain text with no matches")]
    ass = build_ass_subtitle(cues, {"font": "Inter", "size": 40, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#FFD400"}, keywords=[])
    assert "Plain text with no matches" in ass


def _base_preset() -> dict:
    return {"font": "Inter", "size": 44, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#FFD400"}


def test_build_ass_subtitle_font_size_override_replaces_preset_size():
    cues = [SubtitleCue(start=0.0, end=1.0, text="Hello")]
    ass = build_ass_subtitle(cues, _base_preset(), keywords=[], font_size_override=90)
    assert "DejaVu Sans,90" in ass
    assert "DejaVu Sans,44" not in ass


def test_build_ass_subtitle_without_position_uses_preset_alignment_no_pos_tag():
    cues = [SubtitleCue(start=0.0, end=1.0, text="Hello")]
    ass = build_ass_subtitle(cues, _base_preset(), keywords=[])
    assert "\\pos(" not in ass


def test_build_ass_subtitle_custom_position_emits_pos_tag_at_pixel_coords():
    cues = [SubtitleCue(start=0.0, end=1.0, text="Hello")]
    ass = build_ass_subtitle(
        cues, _base_preset(), keywords=[], video_width=1080, video_height=1920,
        position_x_pct=25.0, position_y_pct=75.0,
    )
    # 25% of 1080 = 270, 75% of 1920 = 1440
    assert "{\\pos(270,1440)\\an5}Hello" in ass


def test_build_ass_subtitle_custom_position_requires_both_x_and_y():
    # Only one of the two given -> treated as "no custom position" (falls back to preset).
    cues = [SubtitleCue(start=0.0, end=1.0, text="Hello")]
    ass = build_ass_subtitle(cues, _base_preset(), keywords=[], position_x_pct=50.0)
    assert "\\pos(" not in ass


def test_build_ass_subtitle_custom_position_clamps_out_of_range_percentages():
    cues = [SubtitleCue(start=0.0, end=1.0, text="Hello")]
    ass = build_ass_subtitle(
        cues, _base_preset(), keywords=[], video_width=1000, video_height=2000,
        position_x_pct=-20.0, position_y_pct=150.0,
    )
    assert "{\\pos(0,2000)\\an5}Hello" in ass


def test_cues_from_segments_keeps_only_fully_contained_segments():
    # Clip is [10, 20] (already includes CLIP_PADDING_SECONDS beyond the snapped sentence
    # boundary at, say, 12). The segment ending at 9 is entirely before the clip (dropped), the
    # segment [8, 12] straddles the clip's padded start (dropped — the clip's audio at
    # t=10..12 only has the tail of that sentence, not the whole thing), [12, 18] is fully
    # inside (kept), and [18, 22] straddles the padded end (dropped).
    segments = [
        (5.0, 9.0, "Unrelated earlier sentence."),
        (8.0, 12.0, "Sentence that bleeds into the padding."),
        (12.0, 18.0, "The actual highlighted sentence."),
        (18.0, 22.0, "Sentence that bleeds past the end padding."),
    ]
    cues = cues_from_segments(segments, clip_start=10.0, clip_end=20.0)

    assert len(cues) == 1
    assert cues[0].text == "The actual highlighted sentence."
    assert cues[0].start == 2.0  # rebased to clip-relative time (12 - 10)
    assert cues[0].end == 8.0


def test_cues_from_segments_rebases_to_clip_relative_time():
    segments = [(30.0, 32.0, "Hello there.")]
    cues = cues_from_segments(segments, clip_start=30.0, clip_end=40.0)
    assert cues == [SubtitleCue(start=0.0, end=2.0, text="Hello there.")]


def test_cues_from_segments_returns_empty_when_nothing_fully_contained():
    segments = [(0.0, 5.0, "Before the clip."), (18.0, 25.0, "After the clip.")]
    assert cues_from_segments(segments, clip_start=10.0, clip_end=15.0) == []
