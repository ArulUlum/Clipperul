"""Finds today's trending YouTube videos for the daily auto-clip.

Uses the YouTube Data API v3: "most popular" charts per category/country, plus a
view-count-sorted search for podcasts (YouTube has no podcast chart category)."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

_API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = 20
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_SEARCH_WINDOW = timedelta(hours=72)
# A video a few minutes old with a handful of views would otherwise top a views-per-hour ranking.
_MIN_AGE_HOURS = 6

# Search language hint per region — trending podcasts for a country are mostly in its language.
_REGION_LANGUAGE = {"ID": "id", "US": "en", "GB": "en", "AU": "en", "CA": "en", "IN": "en", "MY": "ms",
                    "PH": "en", "SG": "en", "BR": "pt", "ES": "es", "MX": "es", "FR": "fr", "DE": "de",
                    "JP": "ja", "KR": "ko"}


# Search only *leans* toward a country (regionCode/relevanceLanguage), so a bare "podcast" query
# returns a global mix — measured against the real API, "podcast" for ID gave Brazilian, Indian
# and US results, while "podcast indonesia" gave Indonesian ones. Naming the country in the query
# (in its own language where that's how titles are written) fixes it. English-speaking regions
# are fine without a term.
_REGION_QUERY_TERM = {"ID": "indonesia", "MY": "malaysia", "PH": "pilipinas", "IN": "india", "BR": "brasil",
                      "MX": "mexico", "ES": "españa", "FR": "france", "DE": "deutschland", "JP": "日本", "KR": "한국"}


class TrendingError(Exception):
    """Discovery failed in a way worth showing the user (bad key, quota, unsupported chart...)."""


# News is whatever people are talking about right now, from three angles: YouTube's own "News &
# Politics" chart, speeches by senior officials, and podcasts / interviews / influencer talk. The
# searches are full queries per country in the language its content is in (YouTube search treats "|"
# as OR); the country-name term used for plain podcasts is not appended, since it would bind to just
# the last alternative. "*" covers every other region.
_NEWS_QUERIES: dict[str, tuple[str, ...]] = {
    "ID": (
        "pidato presiden | pidato menteri | arahan presiden | sidang kabinet",
        "podcast indonesia",
        "wawancara eksklusif | klarifikasi | blak-blakan | bongkar",
    ),
    "MY": ("ucapan perdana menteri | ucapan menteri", "podcast malaysia", "temubual eksklusif | dedah | klarifikasi"),
    "BR": ("discurso presidente | discurso ministro", "podcast brasil"),
    "ES": ("discurso presidente | discurso ministro", "podcast españa"),
    "MX": ("discurso presidente | discurso secretario | mañanera presidente", "podcast mexico"),
    "FR": ("discours président | discours premier ministre", "podcast france"),
    "DE": ("rede bundeskanzler | rede minister | regierungserklärung", "podcast deutschland"),
    "*": (
        "president speech | prime minister speech | minister address",
        "podcast",
        "exclusive interview | breaks silence | responds | exposes",
    ),
}


@dataclass(frozen=True)
class TrendingCategory:
    label: str
    youtube_category_id: str | None = None  # "most popular" chart for this YouTube category
    search_query: str | None = None  # no chart exists -> view-sorted search instead
    region_queries: dict[str, tuple[str, ...]] | None = None  # searches per region ("*" = the rest); used as-is
    # YouTube's search duration buckets to run: "medium" is 4-20 min, "long" is over 20. Speeches are
    # often shorter than podcasts, so news looks at both.
    search_durations: tuple[str, ...] = ("long",)
    # Rank by views per hour since upload instead of total views, so what is hot *now* beats a bigger
    # video that has been around for days.
    rank_by_velocity: bool = False


CATEGORIES: dict[str, TrendingCategory] = {
    "podcast": TrendingCategory("Podcast", search_query="podcast"),
    "gaming": TrendingCategory("Gaming", youtube_category_id="20"),
    "entertainment": TrendingCategory("Entertainment", youtube_category_id="24"),
    "news": TrendingCategory(
        "News — what's trending (speeches, podcasts, influencers)",
        youtube_category_id="25",
        region_queries=_NEWS_QUERIES,
        search_durations=("medium", "long"),
        rank_by_velocity=True,
    ),
    "education": TrendingCategory("Education", youtube_category_id="27"),
    "science_tech": TrendingCategory("Science & Technology", youtube_category_id="28"),
    "comedy": TrendingCategory("Comedy", youtube_category_id="23"),
    "sports": TrendingCategory("Sports", youtube_category_id="17"),
    "people_blogs": TrendingCategory("People & Blogs", youtube_category_id="22"),
}


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    channel: str
    duration_seconds: int
    views: int
    is_live: bool
    published_at: datetime | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_iso8601_duration(value: str) -> int:
    """'PT1H2M3S' -> 3723. Unparseable / live ('P0D') -> 0."""
    m = _ISO_DURATION.match(value or "")
    if not m:
        return 0
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def extract_youtube_id(url: str | None) -> str | None:
    """The 11-char video id from a watch / youtu.be / shorts / embed URL, else None."""
    if not url:
        return None
    for pattern in (
        r"(?:youtube\.com/watch\?(?:[^#]*&)?v=)([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _call(path: str, params: dict, api_key: str | None, access_token: str | None) -> dict:
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif api_key:
        params = {**params, "key": api_key}
    else:
        raise TrendingError("No YouTube credential available (set YOUTUBE_API_KEY or connect a YouTube account)")
    try:
        response = requests.get(f"{_API}/{path}", params=params, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise TrendingError(f"Could not reach the YouTube API: {exc.__class__.__name__}") from exc
    if response.status_code != 200:
        try:
            detail = response.json()["error"]["message"]
        except Exception:
            detail = f"HTTP {response.status_code}"
        raise TrendingError(f"YouTube API error: {detail}")
    return response.json()


def _to_candidates(items: list[dict]) -> list[Candidate]:
    out = []
    for item in items:
        video_id = item.get("id")
        if isinstance(video_id, dict):  # search results nest it
            video_id = video_id.get("videoId")
        if not video_id or not _VIDEO_ID.match(video_id):
            continue
        snippet = item.get("snippet", {})
        out.append(
            Candidate(
                video_id=video_id,
                title=snippet.get("title", "").strip() or video_id,
                channel=snippet.get("channelTitle", ""),
                duration_seconds=parse_iso8601_duration(item.get("contentDetails", {}).get("duration", "")),
                views=int(item.get("statistics", {}).get("viewCount", 0) or 0),
                is_live=snippet.get("liveBroadcastContent", "none") != "none",
                published_at=_parse_published(snippet.get("publishedAt")),
            )
        )
    return out


def _parse_published(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _views_per_hour(c: Candidate, now: datetime) -> float:
    age = (now - c.published_at).total_seconds() / 3600 if c.published_at else _SEARCH_WINDOW.total_seconds() / 3600
    return c.views / max(age, _MIN_AGE_HOURS)


def fetch_candidates(
    category_id: str,
    region: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    now: datetime | None = None,
) -> list[Candidate]:
    """Trending videos for a category in a country, hottest first: by the chart's own order for a
    chart-only category, by views for a search-only one, and by views per hour for a category that
    ranks by velocity (the chart and every search merged)."""
    category = CATEGORIES.get(category_id)
    if category is None:
        raise TrendingError(f"Unknown category: {category_id}")
    region = region.upper()
    now = now or datetime.now(timezone.utc)
    parts = "snippet,contentDetails,statistics"

    chart: list[Candidate] = []
    if category.youtube_category_id:
        data = _call(
            "videos",
            {"part": parts, "chart": "mostPopular", "regionCode": region,
             "videoCategoryId": category.youtube_category_id, "maxResults": 50},
            api_key, access_token,
        )
        chart = _to_candidates(data.get("items", []))

    if category.region_queries:
        queries: tuple[str, ...] = category.region_queries.get(region, category.region_queries["*"])
    elif category.search_query:
        queries = (" ".join(filter(None, [category.search_query, _REGION_QUERY_TERM.get(region)])),)
    else:
        return chart

    published_after = (now - _SEARCH_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    search_params = {
        "part": "snippet", "type": "video", "order": "viewCount",
        "publishedAfter": published_after, "regionCode": region, "maxResults": 25,
    }
    if region in _REGION_LANGUAGE:
        search_params["relevanceLanguage"] = _REGION_LANGUAGE[region]
    ids: list[str] = []
    for query in queries:
        for duration in category.search_durations:
            params = {**search_params, "q": query, "videoDuration": duration}
            for item in _call("search", params, api_key, access_token).get("items", []):
                video_id = item["id"].get("videoId") if isinstance(item.get("id"), dict) else None
                if video_id and video_id not in ids:
                    ids.append(video_id)

    found: list[Candidate] = []
    # The videos endpoint takes 50 ids at most. Search results carry no duration/view count, so fetch
    # those in one cheap call per batch.
    for i in range(0, len(ids), 50):
        details = _call("videos", {"part": parts, "id": ",".join(ids[i : i + 50])}, api_key, access_token)
        found.extend(_to_candidates(details.get("items", [])))

    seen: set[str] = set()
    merged = []
    for c in chart + found:
        if c.video_id not in seen:
            seen.add(c.video_id)
            merged.append(c)
    if category.rank_by_velocity:
        return sorted(merged, key=lambda c: _views_per_hour(c, now), reverse=True)
    return sorted(merged, key=lambda c: c.views, reverse=True)


def filter_candidates(
    candidates: list[Candidate], used_ids: set[str], *, min_seconds: int, max_seconds: int
) -> list[Candidate]:
    """Drops already-used videos, live streams, and anything too short to hold several clips or
    too long to transcribe in a reasonable time. Keeps the trending order."""
    seen: set[str] = set()
    usable = []
    for c in candidates:
        if c.video_id in used_ids or c.video_id in seen or c.is_live:
            continue
        if not (min_seconds <= c.duration_seconds <= max_seconds):
            continue
        seen.add(c.video_id)
        usable.append(c)
    return usable
