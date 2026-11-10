"""The "news" clip layout: the cropped clip sits in the middle of a full 9:16 canvas over a blurred,
darkened copy of itself, with the clip's title as a headline in the band above it and the subtitles
in the band below.

A 1:1 (or 4:3 / 3:4) clip otherwise plays as a small box with black bars in a vertical feed; this
fills the screen and puts the hook in writing during the first seconds. A 9:16 clip already fills
the screen, so the layout leaves it alone.

The composed video is what the rest of the pipeline treats as the clip's plain render: subtitles are
burned on top of it in canvas coordinates, exactly as for any other clip."""
from pathlib import Path

from app.services.video import pipeline

FULL = "full"
NEWS = "news"
LAYOUTS = (FULL, NEWS)

CANVAS_W, CANVAS_H = 1080, 1920
_HEADLINE_MARGIN_X = 70
_HEADLINE_MAX_FONT = 72
_HEADLINE_MIN_FONT = 40
# The feeds draw their own UI over the very top (tabs) and the bottom (caption, buttons) of the
# screen, so the headline hugs the top edge of the clip and the subtitles its bottom edge.
_HEADLINE_GAP = 40  # between the headline's last line and the clip
_SUBTITLE_GAP = 110  # from the clip's bottom edge to the subtitle's center
_HEADLINE_COLOR = "&H00FFFFFF"  # white text
_HEADLINE_BOX = "&H001E26D7"  # red box behind it (#D7261E, ASS is BGR)


def applies(layout: str | None, aspect_ratio: str) -> bool:
    return layout == NEWS and aspect_ratio != "9:16"


def content_box(aspect_ratio: str) -> tuple[int, int, int]:
    """(width, height, top) of the clip inside the canvas: full width, vertically centered."""
    w, h = pipeline.get_target_dimensions(aspect_ratio)
    height = round(CANVAS_W * h / w / 2) * 2  # even, for x264
    return CANVAS_W, height, (CANVAS_H - height) // 2


def subtitle_center_pct(aspect_ratio: str) -> float:
    """Just below the clip (or the middle of the band, if it's narrower than that), as a percentage
    of the canvas height — where subtitles go unless the user has placed them somewhere else."""
    _, height, top = content_box(aspect_ratio)
    bottom = top + height
    center = min(bottom + _SUBTITLE_GAP, (bottom + CANVAS_H) / 2)
    return round(center / CANVAS_H * 100, 2)


def _headline_font_size(band_height: int) -> int:
    # Room for three lines (with the box padding) in the band above the clip.
    return max(_HEADLINE_MIN_FONT, min(_HEADLINE_MAX_FONT, int(band_height / 5)))


def _escape(text: str) -> str:
    """ASS treats {...} as override tags and backslash sequences as commands; neither belongs in a
    title, so they become harmless look-alikes."""
    return " ".join(text.split()).replace("\\", "/").replace("{", "(").replace("}", ")")


def build_headline_ass(title: str, aspect_ratio: str) -> str:
    _, _, top = content_box(aspect_ratio)
    size = _headline_font_size(top)
    # BorderStyle 3 draws an opaque box (in OutlineColour) behind each line; Outline is its padding.
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {CANVAS_W}
PlayResY: {CANVAS_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Headline,DejaVu Sans Bold,{size},{_HEADLINE_COLOR},{_HEADLINE_COLOR},{_HEADLINE_BOX},{_HEADLINE_BOX},1,0,0,0,100,100,0,0,3,14,0,5,{_HEADLINE_MARGIN_X},{_HEADLINE_MARGIN_X},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.00,Headline,,0,0,0,,{{\\an2\\pos({CANVAS_W // 2},{max(top - _HEADLINE_GAP, size)})}}{_escape(title)}
"""


def compose_news(content_path: str, output_path: str, title: str, aspect_ratio: str) -> None:
    """Renders the news layout from an already-cropped clip at the given aspect ratio."""
    _, height, top = content_box(aspect_ratio)
    ass_path = Path(output_path).with_suffix(".headline.ass")
    ass_path.write_text(build_headline_ass(title, aspect_ratio), encoding="utf-8")
    # The background is blurred at a quarter of the size (cheap) and darkened so the headline and
    # subtitles stay readable over it.
    graph = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={CANVAS_W // 4}:{CANVAS_H // 4}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W // 4}:{CANVAS_H // 4},boxblur=12:2,scale={CANVAS_W}:{CANVAS_H},"
        f"eq=brightness=-0.22:saturation=0.8[bgb];"
        f"[fg]scale={CANVAS_W}:{height}[fgs];"
        f"[bgb][fgs]overlay=0:{top},setsar=1,ass={ass_path.name}[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", content_path,
        "-filter_complex", graph, "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    pipeline._run(cmd, "LAYOUT_FAILED", "Unable to compose the news layout", cwd=str(ass_path.parent))


def apply(layout: str | None, cropped_path: str, output_path: str, title: str, aspect_ratio: str) -> str:
    """Returns the path of the clip's plain render: the composed video, or the cropped one unchanged
    when the layout doesn't apply."""
    if not applies(layout, aspect_ratio):
        return cropped_path
    compose_news(cropped_path, output_path, title, aspect_ratio)
    return output_path


def subtitle_position(
    layout: str | None, aspect_ratio: str, position_x: float | None, position_y: float | None
) -> tuple[float | None, float | None]:
    """The user's own subtitle position if they set one; under the news layout, the band below the
    clip otherwise (the presets' own placement is measured for a full-frame clip)."""
    if position_x is not None and position_y is not None:
        return position_x, position_y
    if applies(layout, aspect_ratio):
        return 50.0, subtitle_center_pct(aspect_ratio)
    return None, None
