"""Short, catchy clip titles written by the LLM — in the video's own language.

One call covers every clip of a video, so the model can keep the titles distinct from each other.
This never raises: any failure just means "no title for that clip", and the caller falls back to
the plain truncated-reason title."""
import logging
import re
from dataclasses import dataclass

from app.services.ai.llm import LLMProvider

logger = logging.getLogger(__name__)

TARGET_TITLE_CHARS = 60  # what the prompt asks for
MAX_TITLE_CHARS = 80  # accepted; beyond this the title is rejected rather than chopped mid-thought
EXCERPT_CHARS = 700

# Double quotes are never wanted in a title; apostrophes are legitimate inside words ("don't"),
# so those are only trimmed from the ends.
_DOUBLE_QUOTES = re.compile(r'["“”«»`]')
_SNAKE_CASE = re.compile(r"[a-z]+(?:_[a-z0-9]+)+")  # a leaked field name like "emotion_score", not a title


@dataclass(frozen=True)
class ClipTitleInput:
    index: int
    excerpt: str  # what is actually said in the clip
    reason: str  # why the AI picked it — context only, never to be copied as the title


def build_excerpt(segments, start: float, end: float, limit: int = EXCERPT_CHARS) -> str:
    """The transcript text spoken between start and end (the opening matters most for a hook)."""
    parts = [s.text for s in segments if s.end >= start and s.start <= end]
    return " ".join(parts).strip()[:limit]


def clean_title(raw) -> str | None:
    """Normalizes a model-written title, or None if it isn't usable as one."""
    if not isinstance(raw, str):
        return None
    title = " ".join(raw.split())  # collapses newlines/tabs/double spaces
    title = _DOUBLE_QUOTES.sub("", title)
    title = re.sub(r"(?:^|\s)#\w+", "", title)  # no hashtags — those belong in the caption
    title = title.strip("'‘’ ").rstrip(".,;:").strip()
    if not title or len(title) > MAX_TITLE_CHARS or _SNAKE_CASE.fullmatch(title):
        return None
    return title[0].upper() + title[1:]


def _build_prompts(clips: list[ClipTitleInput], language: str | None, category: str | None) -> tuple[str, str]:
    system = (
        "You write titles for short vertical videos (YouTube Shorts, TikTok, Reels) cut from longer "
        "videos. A great title is short, specific, and makes a stranger curious enough to watch — "
        "while staying true to what the clip actually contains."
    )
    language_rule = (
        f"write every title in the SAME language as the transcript excerpts (language code '{language}') — "
        "never translate to English or any other language, even though these instructions are in English."
        if language
        else "write every title in the SAME language the transcript excerpts are written in — never "
        "translate to English, even though these instructions are in English."
    )
    payload = [{"index": c.index, "excerpt": c.excerpt, "why_it_was_picked": c.reason[:300]} for c in clips]
    user = (
        "Write ONE title for each clip below.\n\n"
        "Rules:\n"
        f"- Language: {language_rule}\n"
        f"- Length: at most {TARGET_TITLE_CHARS} characters; shorter is better (roughly 4-10 words).\n"
        "- Make it a hook: a surprising claim, a pointed question, a striking number or detail, or the "
        "tension in the moment. Lead with the most interesting part.\n"
        "- Be truthful: use only what is in the excerpt. Never invent names, numbers, quotes or events, "
        "and never promise something the clip doesn't deliver.\n"
        "- Do NOT open with generic narration (\"The speaker explains...\", \"In this clip...\", "
        "\"Narasumber menjelaskan...\") and do not just rephrase 'why_it_was_picked'.\n"
        "- Plain text only: no quotation marks, no hashtags, no emoji, no trailing period.\n"
        "- Every title must be clearly different from the others.\n"
        f"- The videos are: {category or 'general'} content.\n\n"
        f"Clips (JSON): {payload}\n\n"
        'Respond with JSON: {"titles": [{"index": number, "title": string}]} — exactly one entry per '
        "clip, using the index given."
    )
    return system, user


def generate_clip_titles(
    clips: list[ClipTitleInput], *, language: str | None, category: str | None, llm: LLMProvider
) -> dict[int, str]:
    """index -> title, containing only the clips that got a valid, distinct title."""
    if not clips:
        return {}
    try:
        system, user = _build_prompts(clips, language, category)
        result = llm.complete_json(system, user)
        entries = result.get("titles") if isinstance(result, dict) else None
        if not isinstance(entries, list):
            raise ValueError("response has no 'titles' list")
    except Exception:
        logger.exception("Clip title generation failed; falling back to plain titles")
        return {}

    valid_indexes = {c.index for c in clips}
    titles: dict[int, str] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index not in valid_indexes or index in titles:
            continue
        title = clean_title(entry.get("title"))
        if title is None or title.lower() in seen:  # unusable, or a duplicate of an earlier clip's title
            continue
        seen.add(title.lower())
        titles[index] = title
    return titles
