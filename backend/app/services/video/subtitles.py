import re
from dataclasses import dataclass

# The 5 seeded presets name fonts (Bangers, Montserrat-Bold, Poppins-SemiBold...) that aren't
# bundled in the container — only DejaVu ships by default. Map to the closest available family
# rather than silently falling back to whatever libass picks (which varies by preset intent).
_FONT_FALLBACK = {
    "default": "DejaVu Sans",
    "bold": "DejaVu Sans Bold",
}
_BOLD_FONT_HINTS = ("bold", "bangers", "semibold", "black", "extrabold")


@dataclass
class SubtitleCue:
    start: float  # seconds, relative to the clip's own start (already shifted)
    end: float
    text: str


def cues_from_segments(
    segments: list[tuple[float, float, str]], clip_start: float, clip_end: float
) -> list[SubtitleCue]:
    """Builds subtitle cues from (start, end, text) transcript segments — in the video's
    original timeline, not clip-relative — keeping only segments FULLY inside
    [clip_start, clip_end] and shifting their times to be clip-relative.

    Only-fully-inside is deliberate: a clip's range includes CLIP_PADDING_SECONDS on each side
    beyond the snapped sentence boundary, so a segment that merely overlaps the clip edge would
    show its whole sentence's text while the clip's audio there only contains a sliver of it
    (the padding) — making the subtitle look out of sync with what's actually heard."""
    return [
        SubtitleCue(start=max(0.0, start - clip_start), end=max(0.1, end - clip_start), text=text)
        for start, end, text in segments
        if start >= clip_start and end <= clip_end
    ]


def _resolve_font(preset_font: str) -> str:
    lowered = preset_font.lower()
    return _FONT_FALLBACK["bold"] if any(h in lowered for h in _BOLD_FONT_HINTS) else _FONT_FALLBACK["default"]


def _hex_to_ass_color(hex_color: str) -> str:
    """'#RRGGBB' -> ASS's '&H00BBGGRR' (alpha-BGR, 00 = fully opaque)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H00{b}{g}{r}".upper()


# Bottom-center (ASS numpad Alignment=2) for every position label — subtitles always sit at
# the bottom, horizontally centered; only how far up from the bottom edge varies by preset.
# Margins are FRACTIONS of video height (not raw pixels) so they hold up across aspect ratios.
# Tuned down slightly from 0.25 per user feedback — 0.25 (a literal quarter up from the
# bottom) read as too high once seen on a real clip; 0.16 sits closer to the bottom while
# still clear of the previous ~0.03 "too low" position.
_ALIGNMENT_BOTTOM_CENTER = 2
_MARGIN_V_FRACTION_BY_POSITION = {
    "bottom": 0.16,
    "middle-bottom": 0.20,
    "center": 0.16,
    "top": 0.85,
}


def build_ass_subtitle(
    cues: list[SubtitleCue],
    preset_config: dict,
    keywords: list[str],
    video_width: int = 1080,
    video_height: int = 1920,
    font_size_override: int | None = None,
    position_x_pct: float | None = None,
    position_y_pct: float | None = None,
) -> str:
    """Renders transcript cues as an .ass subtitle track styled per the given preset config,
    with category keywords (gaming/podcast keyword lists — the same ones used for highlight
    scoring) picked out in the preset's highlight_color for simple word-level emphasis.

    video_width/video_height MUST match the actual output video's pixel dimensions — ASS
    positions/margins are relative to PlayResX/PlayResY, so a mismatch (e.g. assuming a
    1080x1920 portrait canvas for a video that's still 16:9) puts the subtitle in the wrong
    place relative to the real frame.

    font_size_override replaces the preset's own size when given. position_x_pct/position_y_pct
    (0-100, percentage of video width/height) place the subtitle's CENTER at that exact point via
    an ASS \\pos override, overriding the preset's fixed bottom-center placement — pass both
    together for a user-chosen position, or leave both None to keep the preset's own position.
    """
    font = _resolve_font(preset_config.get("font", "Inter"))
    size = int(font_size_override) if font_size_override is not None else int(preset_config.get("size", 44))
    color = _hex_to_ass_color(preset_config.get("color", "#FFFFFF"))
    highlight_color = _hex_to_ass_color(preset_config.get("highlight_color", "#FFD400"))
    position = preset_config.get("position", "bottom")
    margin_v = int(video_height * _MARGIN_V_FRACTION_BY_POSITION.get(position, 0.25))

    custom_position = position_x_pct is not None and position_y_pct is not None
    pos_tag = ""
    if custom_position:
        pos_x = int(video_width * max(0.0, min(100.0, position_x_pct)) / 100)
        pos_y = int(video_height * max(0.0, min(100.0, position_y_pct)) / 100)
        # \an5 anchors \pos's (x, y) to the CENTER of the text block, matching the intuitive
        # "slider at 50/50 = dead center" behavior a free x/y position control implies.
        pos_tag = f"{{\\pos({pos_x},{pos_y})\\an5}}"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{color},{color},&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,{_ALIGNMENT_BOTTOM_CENTER},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def _fmt_time(t: float) -> str:
        t = max(0.0, t)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def _emphasize(text: str) -> str:
        if not keywords:
            return text

        def repl(match: re.Match) -> str:
            return f"{{\\c{highlight_color}}}{match.group(0)}{{\\c{color}}}"

        pattern = r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

    lines = [header]
    for cue in cues:
        text = pos_tag + _emphasize(cue.text.strip().replace("\n", "\\N"))
        lines.append(f"Dialogue: 0,{_fmt_time(cue.start)},{_fmt_time(cue.end)},Default,,0,0,0,,{text}\n")

    return "".join(lines)
