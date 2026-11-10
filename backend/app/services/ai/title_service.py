"""The words posted with each clip, written by the LLM in the video's own language: a short,
catchy title, a caption that ends in a question to the viewer, hashtags for the clip's own topic,
and one follow call-to-action for the whole video.

One call covers every clip of a video, so the model can keep the titles distinct from each other.
This never raises: any failure just means "nothing for that clip", and the caller falls back to
the plain truncated-reason title and the reason as caption."""
import logging
import re
from dataclasses import dataclass, field

from app.services.ai.llm import LLMProvider

logger = logging.getLogger(__name__)

TARGET_TITLE_CHARS = 60  # what the prompt asks for
MAX_TITLE_CHARS = 80  # accepted; beyond this the title is rejected rather than chopped mid-thought
TARGET_CAPTION_CHARS = 180
MAX_CAPTION_CHARS = 300
MAX_CTA_CHARS = 80
MAX_TOPIC_HASHTAGS = 5
EXCERPT_CHARS = 700

# Double quotes are never wanted in a title; apostrophes are legitimate inside words ("don't"),
# so those are only trimmed from the ends.
_DOUBLE_QUOTES = re.compile(r'["“”«»`]')
_SNAKE_CASE = re.compile(r"[a-z]+(?:_[a-z0-9]+)+")  # a leaked field name like "emotion_score", not a title
_HASHTAG_IN_TEXT = re.compile(r"(?:^|\s)#\w+")
_HASHTAG_WORD = re.compile(r"[^\W_]\w{1,39}")  # letters/digits in any script, no spaces or punctuation
# Tags that say nothing about the clip — the feed already knows it's a short. The category tag and
# #shorts are added by the caller, so they don't take one of the topic slots either.
_GENERIC_HASHTAGS = {"fyp", "foryou", "foryoupage", "viral", "trending", "shorts", "short", "reels", "tiktok",
                     "youtube", "explore", "news", "podcast", "gaming", "berita", "fypシ"}


@dataclass(frozen=True)
class ClipTitleInput:
    index: int
    excerpt: str  # what is actually said in the clip
    reason: str  # why the AI picked it — context only, never to be copied as the title


@dataclass(frozen=True)
class ClipCopy:
    title: str | None = None
    caption: str | None = None
    hashtags: tuple[str, ...] = ()  # "#topic" each, specific to this clip


@dataclass(frozen=True)
class VideoCopy:
    clips: dict[int, ClipCopy] = field(default_factory=dict)
    cta: str | None = None  # one follow call-to-action line, shared by every clip of the video


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
    title = _HASHTAG_IN_TEXT.sub("", title)  # no hashtags — those belong in the caption
    title = title.strip("'‘’ ").rstrip(".,;:").strip()
    if not title or len(title) > MAX_TITLE_CHARS or _SNAKE_CASE.fullmatch(title):
        return None
    return title[0].upper() + title[1:]


def _clean_line(raw, limit: int) -> str | None:
    """A caption or CTA: one paragraph of plain text, hashtags removed (the caller adds its own).
    Quotes stay — quoting the speaker is the point of a caption."""
    if not isinstance(raw, str):
        return None
    text = " ".join(_HASHTAG_IN_TEXT.sub("", raw).split()).strip()
    if not text or len(text) > limit or _SNAKE_CASE.fullmatch(text):
        return None
    return text[0].upper() + text[1:]


def clean_caption(raw) -> str | None:
    return _clean_line(raw, MAX_CAPTION_CHARS)


def clean_cta(raw) -> str | None:
    return _clean_line(raw, MAX_CTA_CHARS)


def clean_hashtags(raw) -> tuple[str, ...]:
    """Up to MAX_TOPIC_HASHTAGS usable, lowercase, de-duplicated '#tag's; generic ones dropped."""
    if not isinstance(raw, list):
        return ()
    tags: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        word = item.strip().lstrip("#").lower()
        if not _HASHTAG_WORD.fullmatch(word) or word in _GENERIC_HASHTAGS or f"#{word}" in tags:
            continue
        tags.append(f"#{word}")
        if len(tags) == MAX_TOPIC_HASHTAGS:
            break
    return tuple(tags)


def _build_prompts(clips: list[ClipTitleInput], language: str | None, category: str | None) -> tuple[str, str]:
    system = (
        "You write the title, caption and hashtags for short vertical videos (YouTube Shorts, TikTok, "
        "Reels) cut from longer videos. A great title is short, specific, and makes a stranger curious "
        "enough to watch; a great caption gets them to comment and follow — all while staying true to "
        "what the clip actually contains."
    )
    language_rule = (
        f"write every title, caption, hashtag and the cta in the SAME language as the transcript excerpts "
        f"(language code '{language}') — never translate to English or any other language, even though "
        "these instructions are in English."
        if language
        else "write every title, caption, hashtag and the cta in the SAME language the transcript excerpts "
        "are written in — never translate to English, even though these instructions are in English."
    )
    payload = [{"index": c.index, "excerpt": c.excerpt, "why_it_was_picked": c.reason[:300]} for c in clips]
    user = (
        "For each clip below write a title, a caption and hashtags; then write one cta for all of them.\n\n"
        "Rules for everything:\n"
        f"- Language: {language_rule}\n"
        "- Be truthful: use only what is in the excerpt. Never invent names, numbers, quotes or events, "
        "never promise something the clip doesn't deliver, and name a person only if the excerpt itself "
        "names them.\n"
        "- Do NOT open with generic narration (\"The speaker explains...\", \"In this clip...\", "
        "\"Pembicara menjelaskan...\", \"Narasumber mengatakan...\") and do not just rephrase "
        "'why_it_was_picked'.\n"
        f"- The videos are: {category or 'general'} content.\n\n"
        "title:\n"
        f"- At most {TARGET_TITLE_CHARS} characters; shorter is better (roughly 4-10 words).\n"
        "- Make it a hook: a surprising claim, a pointed question, a striking number or detail, or the "
        "tension in the moment. Lead with the most interesting part.\n"
        "- Plain text only: no quotation marks, no hashtags, no emoji, no trailing period.\n"
        "- Every title must be clearly different from the others.\n\n"
        "caption (shown under the video):\n"
        f"- 1-2 short sentences, at most {TARGET_CAPTION_CHARS} characters.\n"
        "- Open with the point of the clip in plain words — what was said and why it matters — so the "
        "caption adds context the title doesn't.\n"
        "- End with ONE short question to the viewer that invites their opinion in the comments "
        "(agree or not, what they would do, who is right).\n"
        "- No hashtags, no emoji.\n\n"
        "hashtags:\n"
        f"- 3-{MAX_TOPIC_HASHTAGS} hashtags about THIS clip's own topic, the way people search for it: "
        "the people, places, institutions or issues it is about.\n"
        "- Lowercase, one word each (no spaces). No generic tags like fyp, viral, trending, shorts or "
        "the category name — those are added separately.\n\n"
        "cta:\n"
        "- One short line inviting viewers to follow for more clips like these (no hashtags, no emoji, "
        f"at most {MAX_CTA_CHARS} characters).\n\n"
        f"Clips (JSON): {payload}\n\n"
        'Respond with JSON: {"cta": string, "titles": [{"index": number, "title": string, '
        '"caption": string, "hashtags": [string]}]} — exactly one entry per clip, using the index given.'
    )
    return system, user


def generate_clip_copy(
    clips: list[ClipTitleInput], *, language: str | None, category: str | None, llm: LLMProvider
) -> VideoCopy:
    """Everything usable the model wrote. A clip is present only if it got at least one usable
    piece; a title that duplicates an earlier clip's is dropped (the rest of that clip is kept)."""
    if not clips:
        return VideoCopy()
    try:
        system, user = _build_prompts(clips, language, category)
        result = llm.complete_json(system, user)
        entries = result.get("titles") if isinstance(result, dict) else None
        if not isinstance(entries, list):
            raise ValueError("response has no 'titles' list")
    except Exception:
        logger.exception("Clip title generation failed; falling back to plain titles")
        return VideoCopy()

    valid_indexes = {c.index for c in clips}
    copies: dict[int, ClipCopy] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index not in valid_indexes or index in copies:
            continue
        title = clean_title(entry.get("title"))
        if title is not None and title.lower() in seen:  # a duplicate of an earlier clip's title
            title = None
        if title is not None:
            seen.add(title.lower())
        copy = ClipCopy(title=title, caption=clean_caption(entry.get("caption")),
                        hashtags=clean_hashtags(entry.get("hashtags")))
        if copy != ClipCopy():
            copies[index] = copy
    return VideoCopy(clips=copies, cta=clean_cta(result.get("cta")))


def generate_clip_titles(
    clips: list[ClipTitleInput], *, language: str | None, category: str | None, llm: LLMProvider
) -> dict[int, str]:
    """index -> title, containing only the clips that got a valid, distinct title."""
    copy = generate_clip_copy(clips, language=language, category=category, llm=llm)
    return {index: c.title for index, c in copy.clips.items() if c.title}
