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


@dataclass(frozen=True)
class TrendingCategory:
    label: str
    youtube_category_id: str | None = None  # "most popular" chart for this YouTube category
    search_query: str | None = None  # no chart exists -> view-sorted search instead


CATEGORIES: dict[str, TrendingCategory] = {
    "podcast": TrendingCategory("Podcast", search_query="podcast"),
    "gaming": TrendingCategory("Gaming", youtube_category_id="20"),
    "entertainment": TrendingCategory("Entertainment", youtube_category_id="24"),
    "news": TrendingCategory("News & Politics", youtube_category_id="25"),
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
            )
        )
    return out


def fetch_candidates(
    category_id: str,
    region: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    now: datetime | None = None,
) -> list[Candidate]:
    """Trending videos for a category in a country, most popular first."""
    category = CATEGORIES.get(category_id)
    if category is None:
        raise TrendingError(f"Unknown category: {category_id}")
    region = region.upper()
    parts = "snippet,contentDetails,statistics"

    if category.youtube_category_id:
        data = _call(
            "videos",
            {"part": parts, "chart": "mostPopular", "regionCode": region,
             "videoCategoryId": category.youtube_category_id, "maxResults": 50},
            api_key, access_token,
        )
        return _to_candidates(data.get("items", []))

    published_after = ((now or datetime.now(timezone.utc)) - _SEARCH_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = " ".join(filter(None, [category.search_query, _REGION_QUERY_TERM.get(region)]))
    search_params = {
        "part": "snippet", "type": "video", "q": query, "order": "viewCount",
        "publishedAfter": published_after, "regionCode": region, "videoDuration": "long", "maxResults": 25,
    }
    if region in _REGION_LANGUAGE:
        search_params["relevanceLanguage"] = _REGION_LANGUAGE[region]
    found = _call("search", search_params, api_key, access_token).get("items", [])
    ids = [i["id"]["videoId"] for i in found if isinstance(i.get("id"), dict) and i["id"].get("videoId")]
    if not ids:
        return []
    # Search results carry no duration/view count — fetch those in one cheap call, keep the order.
    details = _call("videos", {"part": parts, "id": ",".join(ids)}, api_key, access_token).get("items", [])
    by_id = {c.video_id: c for c in _to_candidates(details)}
    ranked = [by_id[i] for i in ids if i in by_id]
    return sorted(ranked, key=lambda c: c.views, reverse=True)


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
