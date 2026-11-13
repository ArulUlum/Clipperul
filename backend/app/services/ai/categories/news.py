import re

from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import VideoCategoryStrategy, cue_score, distinct_cues
from app.services.video.analysis import audio_stats
from app.services.video.analysis_types import VideoAnalysisInput

# Announcements, plans and developments — where things are heading and what is being decided: programs,
# policies, launches, economic and business news, regulation. Indonesian and English. The three lists
# below are disjoint, so one word never feeds two components.
_DIRECTION_KEYWORDS = [
    "ke depan", "masa depan", "tahun depan", "target", "program", "kebijakan", "anggaran", "rencana",
    "swasembada", "kedaulatan", "pembangunan", "reformasi", "kita akan", "pemerintah akan", "kami akan",
    "rakyat", "negara", "bangsa", "generasi", "indonesia emas", "pajak", "ppn",
    "mengumumkan", "diumumkan", "resmi", "peluncuran", "meluncurkan", "mulai berlaku", "aturan baru",
    "regulasi", "investasi", "pertumbuhan ekonomi", "inflasi", "suku bunga",
    "the future", "our country", "our nation", "we will", "policy", "program", "budget", "reform",
    "the people", "next generation", "by 2030", "our plan", "tax",
    "announced", "announce", "launch", "launched", "will be released", "takes effect", "new rule",
    "regulation", "investment", "economic growth", "inflation", "interest rate", "revenue", "acquisition",
]

# Candid, critical or consequential statements — the lines that get quoted and argued about.
_CRITICAL_KEYWORDS = [
    "korupsi", "krisis", "ancaman", "kritik", "tegas", "tidak akan", "tidak boleh", "harus", "gagal",
    "kesalahan", "bohong", "rakus", "utang", "subsidi", "pengangguran", "kemiskinan", "ancam",
    "investigasi", "penyelidikan", "tersangka", "pelanggaran", "kebocoran data", "peretasan", "dilarang",
    "corruption", "crisis", "threat", "criticism", "failed", "mistake", "debt", "unemployment",
    "poverty", "i will not", "we cannot", "unacceptable", "no one is above the law",
    "investigation", "breach", "hack", "fraud", "lawsuit", "banned", "layoffs", "recall",
]

# What a viral talking-point sounds like, whoever says it — a revelation, a confession, a hot take.
_BUZZ_KEYWORDS = [
    "jujur", "sebenarnya", "faktanya", "ternyata", "terungkap", "kontroversial", "viral", "heboh",
    "rahasia", "bongkar", "klarifikasi", "minta maaf", "mengaku", "pengakuan", "skandal", "fakta",
    "honestly", "the truth is", "turns out", "revealed", "exposed", "controversial",
    "scandal", "confession", "apologize", "nobody tells you", "the real reason",
]

# Ceremonial filler that opens nearly every speech and is never a clip.
_PROTOCOL_KEYWORDS = [
    "assalamualaikum", "salam sejahtera", "selamat pagi", "selamat siang", "selamat malam",
    "yang terhormat", "yang saya hormati", "hadirin", "terima kasih", "bapak ibu sekalian",
    "ladies and gentlemen", "distinguished guests", "honorable", "good morning", "thank you all",
]
_PROTOCOL_SENTENCE_MAX_WORDS = 25

# A number says something when it comes with what it counts: a currency, a percentage, a magnitude word,
# a unit of time or people — or a year somebody is planning for. A bare "2" or "10" says nothing.
_MEANINGFUL_NUMBER = re.compile(
    r"(?:(?:rp\.?|usd|us\$|\$|€|£)\s*\d[\d.,]*(?:\s*(?:ribu|juta|miliar|triliun|k|m|bn|million|billion|trillion))?)"
    r"|(?:\d[\d.,]*\s*(?:%|persen|percent|ribu|juta|miliar|triliun|thousand|million|billion|trillion|"
    r"tahun|bulan|hari|orang|jiwa|unit|km|ton|years|months|days|people))"
    r"|(?:(?:tahun|by|in|pada|until|sampai|menjelang)\s+(?:19|20)\d{2})",
    re.IGNORECASE,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _meaningful_numbers(text: str) -> int:
    return len({m.group(0).lower().strip() for m in _MEANINGFUL_NUMBER.finditer(text)})


def _protocol_share(text: str) -> float:
    """The share of the window's words that sit in short ceremonial sentences (greetings, thanks,
    addressing the audience). Proportional: one greeting in front of a minute of substance is a small
    share, a window that is mostly protocol is a large one."""
    total = len(text.split())
    if not total:
        return 0.0
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    protocol_words = sum(
        len(s.split())
        for s in sentences
        if len(s.split()) <= _PROTOCOL_SENTENCE_MAX_WORDS and distinct_cues(s, _PROTOCOL_KEYWORDS) > 0
    )
    return protocol_words / total


class NewsCategoryStrategy(VideoCategoryStrategy):
    """Whatever is being talked about right now: speeches and statements by officials, interviews and
    podcasts, announcements in technology, business and economics, current affairs.

    Components (each 0-1, from different word lists or measurements):
    - announcement_score: how many DIFFERENT announcement / plan / development phrases appear.
    - critical_score: how many different critical, candid or consequential phrases appear.
    - buzz_score: how many different revelation / confession / hot-take phrases appear.
    - numbers_score: figures that come with what they count (a percentage, an amount, a year) — a bare
      number counts for nothing.
    - substance_score: 1 minus the share of the window that is ceremonial (greeting) sentences, scaled
      proportionally. A greeting followed by an announcement costs a little; it never suppresses the
      announcement.
    - vocal_emphasis: how far the loudest second rises above the window's average, small weight and
      only when there is audio.

    Cue counts are distinct cues, not frequencies, so repeating a word doesn't inflate a window. These
    rank windows for the LLM; the LLM decides what is worth clipping."""

    category = VideoCategory.NEWS

    def keyword_list(self) -> list[str]:
        return _DIRECTION_KEYWORDS + _CRITICAL_KEYWORDS + _BUZZ_KEYWORDS

    def heuristic_weights(self) -> dict[str, float]:
        return {
            "announcement_score": 0.25,
            "critical_score": 0.20,
            "buzz_score": 0.15,
            "numbers_score": 0.15,
            "substance_score": 0.15,
            "vocal_emphasis": 0.10,
        }

    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        scores: dict[str, float] = {}

        if window_text:
            scores["announcement_score"] = cue_score(window_text, _DIRECTION_KEYWORDS, saturation=3)
            scores["critical_score"] = cue_score(window_text, _CRITICAL_KEYWORDS, saturation=2)
            scores["buzz_score"] = cue_score(window_text, _BUZZ_KEYWORDS, saturation=2)
            scores["numbers_score"] = _clamp01(_meaningful_numbers(window_text) / 3)
            scores["substance_score"] = _clamp01(1.0 - _protocol_share(window_text) * 1.5)

        audio = audio_stats(analysis, start, end)
        if audio and audio["count"]:
            scores["vocal_emphasis"] = _clamp01(audio["peak"] - audio["mean"])

        return scores

    def build_system_prompt(self) -> str:
        return (
            "You are Clipperul's highlight detector for NEWS videos — whatever people are talking "
            "about right now. The speaker may be a senior official (president, prime minister, "
            "minister, governor), a company or institution spokesperson, a journalist or interviewee, a "
            "podcast host or guest, an influencer, an expert, or any other public figure, and the subject "
            "may be politics, government, technology, business, economics, public announcements or "
            "current affairs. Find the moments most worth clipping and most likely to be shared and "
            "argued about: important information, a significant announcement or development, a relevant "
            "fact, a concrete promise or number, what something means for the public, a clarification, "
            "revelation or confession, a critical or candid remark, a meaningful controversial statement, "
            "or a blunt, quotable line. Good news and bad news both count — pick whatever people are "
            "likely to react to, agree or disagree with, and repeat, and favor the point the video is "
            "actually famous for over side topics. "
            "Skip protocol and ceremony (greetings, 'ladies and gentlemen', thanking attendees, "
            "naming dignitaries, prayers), host chatter, sponsor reads, vague boilerplate with no "
            "claim in it, and long lists of figures with no point. A greeting at the start of a window "
            "does not make what follows it any less important. Prefer a self-contained statement "
            "of roughly 20-45 seconds that a viewer understands without the rest of the video and "
            "whose meaning is not distorted by being cut out. Rely on the transcript text as ground "
            "truth — never invent, embellish, exaggerate or editorialize about what was said. Judge on hook (does "
            "the opening line land immediately), flow (a complete thought with a real beginning and "
            "conclusion, not a mid-sentence fragment), value (information the public actually "
            "benefits from knowing, or a statement with real consequences) and trend (a quotable, "
            "debatable line that spreads on its own)."
        )
