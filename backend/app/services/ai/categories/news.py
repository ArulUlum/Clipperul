import re

from app.models.enums import VideoCategory
from app.services.ai.categories.strategy import VideoCategoryStrategy, _keyword_score
from app.services.video.analysis import average_in_range
from app.services.video.analysis_types import VideoAnalysisInput

# Where the country is heading: programs, policies, plans, promises. Indonesian and English —
# these are the languages the speeches are mostly in.
_DIRECTION_KEYWORDS = [
    "ke depan", "masa depan", "tahun depan", "target", "program", "kebijakan", "anggaran", "rencana",
    "swasembada", "kedaulatan", "pembangunan", "reformasi", "kita akan", "pemerintah akan", "kami akan",
    "rakyat", "negara", "bangsa", "generasi", "indonesia emas", "pajak", "ppn",
    "the future", "our country", "our nation", "we will", "policy", "program", "budget", "reform",
    "the people", "next generation", "by 2030", "our plan", "tax"
]

# Candid, critical or consequential statements — the lines that get quoted and argued about.
_CRITICAL_KEYWORDS = [
    "korupsi", "krisis", "ancaman", "kritik", "tegas", "tidak akan", "tidak boleh", "harus", "gagal",
    "kesalahan", "bohong", "rakus", "utang", "subsidi", "pengangguran", "kemiskinan", "ancam",
    "corruption", "crisis", "threat", "criticism", "failed", "mistake", "debt", "unemployment",
    "poverty", "i will not", "we cannot", "unacceptable", "no one is above the law",
]

# What a viral talking-point sounds like, whoever says it — a revelation, a confession, a hot take.
_BUZZ_KEYWORDS = [
    "jujur", "sebenarnya", "faktanya", "ternyata", "terungkap", "kontroversial", "viral", "heboh",
    "rahasia", "bongkar", "klarifikasi", "minta maaf", "mengaku", "pengakuan", "skandal", "fakta",
    "honestly", "the truth is", "turns out", "revealed", "exposed", "controversial", "viral",
    "scandal", "confession", "apologize", "nobody tells you", "the real reason",
]

# Ceremonial filler that opens nearly every speech and is never a clip.
_PROTOCOL_KEYWORDS = [
    "assalamualaikum", "salam sejahtera", "selamat pagi", "selamat siang", "selamat malam",
    "yang terhormat", "yang saya hormati", "hadirin", "terima kasih", "bapak ibu sekalian",
    "ladies and gentlemen", "distinguished guests", "honorable", "good morning", "thank you all",
]

_NUMBER_PATTERN = re.compile(
    r"\d[\d.,]*\s*(?:%|persen|percent|triliun|trilliun|miliar|juta|ribu|trillion|billion|million)?", re.IGNORECASE
)


class NewsCategoryStrategy(VideoCategoryStrategy):
    """Whatever is being talked about right now: speeches by senior officials, podcasts and
    interviews, influencers and public figures. The heuristics only pre-rank the windows — they
    reward talk about direction and programs, critical or revealing statements and concrete numbers,
    and punish ceremonial openings — while the LLM judges what is actually worth clipping."""

    category = VideoCategory.NEWS

    def keyword_list(self) -> list[str]:
        return _DIRECTION_KEYWORDS + _CRITICAL_KEYWORDS + _BUZZ_KEYWORDS

    def compute_heuristic_scores(
        self, analysis: VideoAnalysisInput, window_text: str, start: float, end: float
    ) -> dict[str, float]:
        speech_energy = average_in_range(analysis.audio_energy, start, end)
        word_count = max(1, len(window_text.split()))
        number_hits = len(_NUMBER_PATTERN.findall(window_text))
        protocol = _keyword_score(window_text, _PROTOCOL_KEYWORDS)

        return {
            "direction_score": _keyword_score(window_text, _DIRECTION_KEYWORDS),
            "critical_score": _keyword_score(window_text, _CRITICAL_KEYWORDS),
            "buzz_score": _keyword_score(window_text, _BUZZ_KEYWORDS),
            # A figure or two backs a claim; a window that is all numbers is a table, so it caps out.
            "numbers_score": min(1.0, number_hits / max(1.0, word_count / 30)),
            "speech_energy": speech_energy,
            "substance_score": max(0.0, 1.0 - protocol * 2),
        }

    def build_system_prompt(self) -> str:
        return (
            "You are Clipperul's highlight detector for NEWS videos — whatever people are talking "
            "about right now. The speaker may be a senior official (president, prime minister, "
            "minister, governor), a podcast host or guest, an influencer, an expert, or any other "
            "public figure. Find the moments most worth clipping and most likely to be shared and "
            "argued about: where the country or the topic is heading, a program or policy and what "
            "it means for people, a critical, candid or surprising remark (about corruption, the "
            "economy, a failure, a threat, a decision), a revelation, confession, clarification or "
            "hot take, a concrete promise or number, or a blunt, quotable line. Good news and bad "
            "news both count — pick whatever people are likely to react to, agree or disagree with, "
            "and repeat, and favor the point the video is actually famous for over side topics. "
            "Skip protocol and ceremony (greetings, 'ladies and gentlemen', thanking attendees, "
            "naming dignitaries, prayers), host chatter, sponsor reads, vague boilerplate with no "
            "claim in it, and long lists of figures with no point. Prefer a self-contained statement "
            "of roughly 20-45 seconds that a viewer understands without the rest of the video and "
            "whose meaning is not distorted by being cut out. Rely on the transcript text as ground "
            "truth — never invent, embellish, or editorialize about what was said. Judge on hook (does "
            "the opening line land immediately), flow (a complete thought with a real beginning and "
            "conclusion, not a mid-sentence fragment), value (information the public actually "
            "benefits from knowing, or a statement with real consequences) and trend (a quotable, "
            "debatable line that spreads on its own)."
        )
