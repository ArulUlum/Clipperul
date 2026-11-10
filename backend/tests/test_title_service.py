import pytest

from app.services.ai.llm import LLMProvider
from app.services.ai.title_service import (
    MAX_TITLE_CHARS, ClipCopy, ClipTitleInput, build_excerpt, clean_caption, clean_hashtags, clean_title,
    generate_clip_copy, generate_clip_titles,
)
from app.workers.tasks.video_tasks import _compose_caption
from app.services.ai.transcription import TranscriptSegmentResult


class FakeLLM(LLMProvider):
    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error = result, error
        self.system = self.user = None

    def complete_json(self, system_prompt, user_prompt):
        self.system, self.user = system_prompt, user_prompt
        if self.error:
            raise self.error
        return self.result


def _clips(n=3):
    return [ClipTitleInput(index=i, excerpt=f"excerpt {i}", reason=f"reason {i}") for i in range(n)]


def _titles(*pairs):
    return {"titles": [{"index": i, "title": t} for i, t in pairs]}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Kenapa angkot biru ini bikin merinding?", "Kenapa angkot biru ini bikin merinding?"),
        ('  "Rahasia di balik 70% makanan padat."  ', "Rahasia di balik 70% makanan padat"),  # quotes + trailing period
        ("“Smart quotes” too", "Smart quotes too"),
        ("Dia bilang \"gila\" banget", "Dia bilang gila banget"),
        ("Don't miss it's the end", "Don't miss it's the end"),  # apostrophes inside words survive
        ("'Dikutip'", "Dikutip"),
        ("Satu", "Satu"),  # a one-word title is fine
        ("kalimat huruf kecil", "Kalimat huruf kecil"),
        ("Dua   baris\nsatu judul", "Dua baris satu judul"),
        ("Judul bagus #podcast #fyp", "Judul bagus"),  # hashtags belong in the caption
        ("Wow!", "Wow!"),  # ! and ? are kept
    ],
)
def test_clean_title_normalizes(raw, expected):
    assert clean_title(raw) == expected


@pytest.mark.parametrize("raw", [None, 5, "", "   ", '""', "x" * (MAX_TITLE_CHARS + 1), "emotion_score", "hook_score_2", ["a"], {"t": 1}])
def test_clean_title_rejects_unusable_values(raw):
    assert clean_title(raw) is None


def test_a_title_right_at_the_limit_is_accepted():
    assert clean_title("a" * MAX_TITLE_CHARS) == "A" + "a" * (MAX_TITLE_CHARS - 1)


def test_generates_a_title_per_clip_by_index():
    llm = FakeLLM(_titles((0, "Judul nol"), (1, "Judul satu"), (2, "Judul dua")))
    assert generate_clip_titles(_clips(), language="id", category="podcast", llm=llm) == {
        0: "Judul nol", 1: "Judul satu", 2: "Judul dua",
    }


def test_prompt_demands_the_videos_language_and_carries_the_excerpts():
    llm = FakeLLM(_titles((0, "A"), (1, "B"), (2, "C")))
    generate_clip_titles(_clips(), language="id", category="gaming", llm=llm)
    assert "'id'" in llm.user and "never translate" in llm.user.lower()
    assert "excerpt 1" in llm.user and "reason 2" in llm.user
    assert "gaming" in llm.user
    assert "truthful" in llm.user.lower()  # no invented facts
    assert str(60) in llm.user  # the length target


def test_prompt_still_demands_matching_language_when_it_was_not_detected():
    llm = FakeLLM(_titles((0, "A")))
    generate_clip_titles(_clips(1), language=None, category=None, llm=llm)
    assert "same language" in llm.user.lower() and "never" in llm.user.lower()


def test_only_valid_titles_come_back_and_the_rest_are_left_for_the_fallback():
    llm = FakeLLM(_titles((0, "Bagus"), (1, "x" * 200), (2, "")))
    assert generate_clip_titles(_clips(), language="id", category="podcast", llm=llm) == {0: "Bagus"}


def test_duplicate_titles_keep_only_the_first():
    llm = FakeLLM(_titles((0, "Sama persis"), (1, "sama PERSIS"), (2, "Beda")))
    assert generate_clip_titles(_clips(), language="id", category="podcast", llm=llm) == {0: "Sama persis", 2: "Beda"}


def test_unknown_repeated_or_malformed_entries_are_ignored():
    llm = FakeLLM({"titles": [
        {"index": 99, "title": "Tidak ada clip ini"},
        {"index": 0, "title": "Pertama"},
        {"index": 0, "title": "Kedua untuk indeks sama"},
        {"index": "1", "title": "Indeks string"},
        {"index": True, "title": "Bool bukan indeks"},
        "bukan dict",
        {"title": "tanpa indeks"},
    ]})
    assert generate_clip_titles(_clips(), language="id", category="podcast", llm=llm) == {0: "Pertama"}


@pytest.mark.parametrize("bad", [{}, {"titles": "nope"}, {"titles": None}, [], "string"])
def test_a_malformed_response_yields_no_titles(bad):
    assert generate_clip_titles(_clips(), language="id", category="podcast", llm=FakeLLM(bad)) == {}


def test_an_llm_failure_never_raises():
    llm = FakeLLM(error=RuntimeError("router down"))
    assert generate_clip_titles(_clips(), language="id", category="podcast", llm=llm) == {}


def test_no_clips_means_no_call():
    llm = FakeLLM(error=AssertionError("must not be called"))
    assert generate_clip_titles([], language="id", category="podcast", llm=llm) == {}


def test_copy_carries_caption_topic_hashtags_and_one_cta():
    llm = FakeLLM({
        "cta": "Follow untuk potongan berikutnya #fyp",
        "titles": [
            {"index": 0, "title": "Alat sadap buat siapa?", "caption": "Alat sadap ratusan miliar dibeli. Setuju?",
             "hashtags": ["#Penyadapan", "pegasus", "#fyp", "#viral", "dua kata", "#penyadapan", 5]},
            {"index": 1, "title": "Alat sadap buat siapa?", "caption": "Caption kedua, benar?", "hashtags": []},
        ],
    })
    copy = generate_clip_copy(_clips(), language="id", category="news", llm=llm)

    assert copy.cta == "Follow untuk potongan berikutnya"  # hashtags never ride along in the CTA
    first = copy.clips[0]
    assert first.title == "Alat sadap buat siapa?"
    assert first.caption == "Alat sadap ratusan miliar dibeli. Setuju?"
    assert first.hashtags == ("#penyadapan", "#pegasus")  # lowercased, generic/invalid/duplicate dropped
    # the duplicate title is dropped, but that clip still keeps its caption
    assert copy.clips[1].title is None and copy.clips[1].caption == "Caption kedua, benar?"
    assert 2 not in copy.clips


def test_old_style_title_only_responses_still_work():
    copy = generate_clip_copy(_clips(1), language="id", category="news", llm=FakeLLM(_titles((0, "Judul"))))
    assert copy.clips[0] == ClipCopy(title="Judul") and copy.cta is None


@pytest.mark.parametrize("raw", [None, 3, "", "   ", "x" * 301, "hook_score"])
def test_unusable_captions_are_dropped(raw):
    assert clean_caption(raw) is None


def test_caption_keeps_quotes_but_not_hashtags():
    assert clean_caption('dia bilang "tergantung kopi" #prabowo\nsetuju?') == 'Dia bilang "tergantung kopi" setuju?'


def test_hashtags_are_capped_at_five():
    assert len(clean_hashtags([f"tag{i}" for i in range(9)])) == 5


def test_prompt_asks_for_a_question_and_bans_speaker_narration():
    llm = FakeLLM(_titles((0, "A")))
    generate_clip_copy(_clips(1), language="id", category="news", llm=llm)
    user = llm.user.lower()
    assert "question to the viewer" in user
    assert "pembicara menjelaskan" in user  # named as the narration to avoid
    assert "name a person only if the excerpt itself names them" in user
    assert '"cta"' in llm.user and '"hashtags"' in llm.user


def test_excerpt_takes_only_the_clips_own_transcript_and_is_capped():
    segs = [
        TranscriptSegmentResult(0, 10, "sebelum clip"),
        TranscriptSegmentResult(10, 20, "isi clip pertama"),
        TranscriptSegmentResult(20, 30, "isi clip kedua"),
        TranscriptSegmentResult(40, 50, "setelah clip"),
    ]
    assert build_excerpt(segs, 11, 29) == "isi clip pertama isi clip kedua"
    assert len(build_excerpt(segs, 0, 50, limit=15)) == 15


def test_composed_caption_is_hook_then_cta_then_topic_and_category_tags():
    clip = ClipCopy(title="T", caption="Alat sadap dibeli rakyat. Setuju?", hashtags=("#penyadapan", "#shorts"))
    assert _compose_caption(clip, "Follow untuk potongan berikutnya", "reason", "news") == (
        "Alat sadap dibeli rakyat. Setuju?\n\nFollow untuk potongan berikutnya\n\n#penyadapan #shorts #news"
    )


def test_composed_caption_falls_back_to_the_reason_and_stock_tags():
    assert _compose_caption(None, None, "Pembicara menjelaskan X.", "news") == "Pembicara menjelaskan X.\n\n#news #shorts #trending"
    assert _compose_caption(ClipCopy(title="T"), "Follow ya", "R", "podcast") == "R\n\nFollow ya\n\n#podcast #shorts #podcastclips"
