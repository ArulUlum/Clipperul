"""Category strategies: normalized, non-duplicating heuristic ranking; candidate windows; temporal diversity."""
import math

import pytest

from app.services.ai.categories import strategy as strategy_module
from app.services.ai.categories.gaming import GamingCategoryStrategy
from app.services.ai.categories.news import NewsCategoryStrategy
from app.services.ai.categories.podcast import PodcastCategoryStrategy
from app.services.ai.categories.strategy import (
    _CandidateWindow, cue_score, distinct_cues, select_diverse_candidates,
)
from app.services.ai.llm import LLMProvider
from app.services.ai.transcription import TranscriptSegmentResult
from app.services.video.analysis_types import AudioEnergyPoint, SceneChangePoint, VideoAnalysisInput

GAMING, PODCAST, NEWS = GamingCategoryStrategy(), PodcastCategoryStrategy(), NewsCategoryStrategy()


def _audio(values, start=0):
    return [AudioEnergyPoint(time=start + i, energy=v) for i, v in enumerate(values)]


def _video(text="", *, audio=None, scene=None, duration=60.0, **kw):
    segments = [TranscriptSegmentResult(start=0.0, end=min(duration, 30.0), text=text)] if text else []
    return VideoAnalysisInput(
        video_duration=duration, transcript_segments=segments,
        audio_energy=audio or [], scene_changes=scene or [], **kw,
    )


def _scores(strategy, video, text, start=0.0, end=30.0):
    return strategy.compute_heuristic_scores(video, text, start, end)


def _rank(strategy, video, text, start=0.0, end=30.0):
    return strategy.combine_heuristics(_scores(strategy, video, text, start, end))


# --------------------------------------------------------------------------- combining / normalization

@pytest.mark.parametrize("strategy", [GAMING, PODCAST, NEWS], ids=["gaming", "podcast", "news"])
def test_every_category_ranks_on_the_same_0_to_1_scale_whatever_its_number_of_components(strategy):
    weights = strategy.heuristic_weights()
    assert strategy.combine_heuristics({name: 1.0 for name in weights}) == pytest.approx(1.0)
    assert strategy.combine_heuristics({name: 0.0 for name in weights}) == 0.0
    assert 0.0 <= strategy.combine_heuristics({name: 0.5 for name in weights}) <= 1.0


def test_combining_clamps_ignores_missing_and_non_finite_and_unknown_components():
    assert PODCAST.combine_heuristics({"statement_strength": 7.0, "qa_exchange": -3.0}) == pytest.approx(
        (1.0 * 0.40 + 0.0 * 0.12) / (0.40 + 0.12)
    )
    assert PODCAST.combine_heuristics({}) == 0.0
    assert PODCAST.combine_heuristics({"statement_strength": float("nan"), "qa_exchange": None}) == 0.0
    assert PODCAST.combine_heuristics({"statement_strength": 0.5, "made_up_component": 1.0}) == 0.5


def test_a_missing_component_is_reweighted_not_counted_as_zero():
    full = {"audio_excitement": 0.8, "vocal_reaction": 0.8, "keyword_score": 0.8, "scene_activity": 0.8}
    without_text = {"audio_excitement": 0.8, "scene_activity": 0.8}
    assert GAMING.combine_heuristics(full) == pytest.approx(0.8)
    assert GAMING.combine_heuristics(without_text) == pytest.approx(0.8)  # silence is not a bad score


def test_the_old_sum_is_gone_heuristic_total_is_the_normalized_score():
    window = _CandidateWindow(candidate_id="c0", start=0, end=30, text="t", heuristic_scores={"a": 1.0, "b": 1.0}, heuristic_score=0.42)
    assert window.heuristic_total == 0.42


# --------------------------------------------------------------------------- cue counting

def test_cues_count_distinct_phrases_not_repetitions():
    text = "honestly honestly honestly honestly honestly"
    assert distinct_cues(text, ["honestly", "the truth is"]) == 1
    assert cue_score(text, ["honestly", "the truth is"], saturation=3) == pytest.approx(1 / 3)
    assert cue_score("honestly, the truth is it turns out", ["honestly", "the truth is", "turns out"]) == 1.0
    assert cue_score("", ["a"]) == 0.0 and cue_score("x", []) == 0.0
    assert distinct_cues("Clutch! CLUTCH!", ["clutch"]) == 1  # case-insensitive


# --------------------------------------------------------------------------- gaming

def test_gaming_with_keywords_scores_the_keyword_component():
    video = _video("Oh my god, clutch! No way! GG, that was an insane comeback!", audio=_audio([0.5] * 30))
    scores = _scores(GAMING, video, video.transcript_segments[0].text)
    assert scores["keyword_score"] > 0 and scores["vocal_reaction"] > 0


def test_gaming_does_not_need_any_keyword_in_the_transcript():
    loud = _video("And then he just moves over there", audio=_audio([0.1] * 25 + [1.0, 0.9, 1.0, 0.8, 0.9]))
    quiet = _video("And then he just moves over there", audio=_audio([0.1] * 30))
    text = "And then he just moves over there"

    assert _scores(GAMING, loud, text)["keyword_score"] == 0.0
    assert _rank(GAMING, loud, text) > _rank(GAMING, quiet, text)  # the loud burst still ranks it higher


def test_a_loud_spike_beats_a_uniformly_loud_stretch_of_the_same_average():
    spike = _video("x", audio=_audio([0.1] * 27 + [1.0, 1.0, 1.0]))
    flat = _video("x", audio=_audio([0.19] * 30))
    assert _scores(GAMING, spike, "x")["audio_excitement"] > _scores(GAMING, flat, "x")["audio_excitement"]


def test_quiet_gameplay_with_no_commentary_is_still_a_candidate_and_ranked_on_audio_and_picture():
    scene = [SceneChangePoint(time=t, magnitude=0.9) for t in range(10, 20)]
    video = _video("", audio=_audio([0.2] * 10 + [0.9] * 10 + [0.2] * 5), scene=scene, duration=25.0)

    windows = GAMING._generate_candidate_windows(video)

    assert len(windows) == 1 and windows[0].text == ""
    assert set(windows[0].heuristic_scores) == {"audio_excitement", "scene_activity"}  # nothing to transcribe: left out
    assert windows[0].heuristic_score > 0
    assert "audio mean=" in windows[0].signals


def test_a_window_with_no_transcript_and_no_signal_is_not_a_candidate():
    assert GAMING._generate_candidate_windows(_video("", duration=30.0)) == []
    assert GAMING._generate_candidate_windows(_video("", audio=_audio([0.0] * 30), duration=30.0)) == []  # silence


def test_scene_changes_are_a_weak_signal_and_unknown_scenes_are_left_out():
    scene = [SceneChangePoint(time=t, magnitude=1.0) for t in range(30)]
    complete = _video("x", audio=_audio([0.5] * 30), scene=scene)
    cut_short = _video("x", audio=_audio([0.5] * 30), scene=scene[:10], scene_analysis_complete=False, scene_analyzed_until=9.0)

    assert "scene_activity" in _scores(GAMING, complete, "x")
    assert "scene_activity" not in _scores(GAMING, cut_short, "x")  # analysis stopped before the window ended
    # and the picture alone can't carry a window: weight 0.12 of 0.82
    assert GAMING.heuristic_weights()["scene_activity"] < GAMING.heuristic_weights()["audio_excitement"] / 3


def test_gaming_signals_are_not_derived_from_each_other():
    """Each component reads a different input, so changing one input moves only its own component."""
    base = _video("hello there", audio=_audio([0.3] * 30), scene=[SceneChangePoint(time=t, magnitude=0.2) for t in range(30)])
    more_scene = _video("hello there", audio=_audio([0.3] * 30), scene=[SceneChangePoint(time=t, magnitude=0.9) for t in range(30)])

    a, b = _scores(GAMING, base, "hello there"), _scores(GAMING, more_scene, "hello there")

    assert b["scene_activity"] > a["scene_activity"]
    assert {k: v for k, v in a.items() if k != "scene_activity"} == {k: v for k, v in b.items() if k != "scene_activity"}


def test_a_gaming_keyword_repeated_many_times_does_not_inflate_the_score():
    once = _scores(GAMING, _video("clutch"), "clutch")["keyword_score"]
    many = _scores(GAMING, _video("clutch clutch clutch clutch clutch clutch"), "clutch clutch clutch clutch clutch clutch")["keyword_score"]
    assert once == many


def test_gaming_prompt_covers_the_moments_and_forbids_inventing_events():
    prompt = GAMING.build_system_prompt().lower()
    for moment in ("clutch", "boss", "comeback", "rare", "achievement", "skillful", "funny failures", "unexpected", "reactions"):
        assert moment in prompt
    assert "do not require commentary" in prompt  # quiet play can win
    assert "never state or imply" in prompt and "do not make up" in prompt
    assert "not proof" in prompt  # a scene change or a loud burst is not evidence of a play


# --------------------------------------------------------------------------- podcast

INSIGHT = "The reason is that most people never measure the thing, and it turns out the difference is huge."
FILLER = "So yeah, we went there and it was fine, and then we left and went home after that."


def test_a_podcast_insight_without_any_question_mark_is_recognised():
    video = _video(INSIGHT)
    scores = _scores(PODCAST, video, INSIGHT)
    assert "?" not in INSIGHT
    assert scores["statement_strength"] > 0 and scores["qa_exchange"] == 0.0
    assert _rank(PODCAST, video, INSIGHT) > _rank(PODCAST, _video(FILLER), FILLER)


def test_question_marks_alone_do_not_make_a_window_relevant():
    plain = _scores(PODCAST, _video(FILLER), FILLER)
    questions = "So yeah? We went there? And it was fine? Then we left? And went home? After that?"
    asking = _scores(PODCAST, _video(questions), questions)
    assert plain["statement_strength"] == asking["statement_strength"] == 0.0
    assert asking["qa_exchange"] == 0.0  # no answer follows the questions


def test_a_question_with_a_real_answer_is_an_exchange():
    segments = [
        TranscriptSegmentResult(start=0.0, end=5.0, text="Why did the project fail?"),
        TranscriptSegmentResult(
            start=5.0, end=25.0,
            text="Because nobody owned the budget and every team assumed another team was watching the numbers.",
        ),
    ]
    video = VideoAnalysisInput(video_duration=30.0, transcript_segments=segments)
    text = " ".join(s.text for s in segments)
    assert _scores(PODCAST, video, text)["qa_exchange"] == 1.0

    unanswered = VideoAnalysisInput(video_duration=30.0, transcript_segments=segments[:1] + [TranscriptSegmentResult(start=5.0, end=7.0, text="Yeah.")])
    assert _scores(PODCAST, unanswered, "Why did the project fail? Yeah.")["qa_exchange"] == 0.0


def test_podcast_insight_and_statement_are_one_signal_not_two():
    scores = _scores(PODCAST, _video(INSIGHT), INSIGHT)
    assert "insight_score" not in scores and "emotion_score" not in scores and "topic_relevance" not in scores
    assert list(scores).count("statement_strength") == 1


def test_a_longer_transcript_does_not_score_higher_than_a_full_one():
    full = "word " * 75  # ordinary speech pace over 30 s
    triple = "word " * 225
    a = _scores(PODCAST, _video("x"), full)["content_density"]
    b = _scores(PODCAST, _video("x"), triple)["content_density"]
    assert a == b == 1.0
    assert _scores(PODCAST, _video("x"), "word " * 10)["content_density"] < 0.2  # mostly pauses


def test_a_quiet_thoughtful_statement_outranks_a_loud_empty_one():
    quiet = _video(INSIGHT, audio=_audio([0.2] * 30))
    loud = _video(FILLER, audio=_audio([0.2] * 20 + [1.0] * 10))
    assert _rank(PODCAST, quiet, INSIGHT) > _rank(PODCAST, loud, FILLER)


def test_podcast_windows_cut_mid_sentence_rank_below_self_contained_ones():
    segments = [TranscriptSegmentResult(start=-5.0, end=10.0, text=INSIGHT), TranscriptSegmentResult(start=10.0, end=40.0, text=INSIGHT)]
    cut = VideoAnalysisInput(video_duration=60.0, transcript_segments=segments)
    whole = _video(INSIGHT)
    assert _scores(PODCAST, cut, INSIGHT * 2)["segment_integrity"] < _scores(PODCAST, whole, INSIGHT)["segment_integrity"]


def test_podcast_prompt_values_quiet_content_and_lists_the_kinds_of_moment():
    prompt = PODCAST.build_system_prompt().lower()
    for kind in ("insights", "opinions", "stories", "funny", "surprising facts", "memorable", "question-and-answer", "self-contained"):
        assert kind in prompt
    assert "quiet, thoughtful statement" in prompt and "loud conversation" in prompt


# --------------------------------------------------------------------------- news

ANNOUNCEMENT = "Pemerintah mengumumkan kebijakan baru: anggaran pangan naik 20 persen mulai tahun 2027."
GREETING = "Assalamualaikum warahmatullahi wabarakatuh. Selamat pagi hadirin sekalian."


def test_an_announcement_after_a_greeting_is_not_suppressed():
    announcement = " ".join([ANNOUNCEMENT] * 4)  # a realistic 30 s window: about 55 words of substance
    with_greeting = f"{GREETING} {announcement}"
    plain = _rank(NEWS, _video(announcement), announcement)
    greeted = _rank(NEWS, _video(with_greeting), with_greeting)

    assert _scores(NEWS, _video(with_greeting), with_greeting)["announcement_score"] == \
        _scores(NEWS, _video(announcement), announcement)["announcement_score"]
    assert plain - greeted < 0.05  # a small, proportional cost
    mostly_greeting = _rank(NEWS, _video(GREETING), GREETING)
    assert greeted > mostly_greeting + 0.15  # a window that IS the greeting ranks far lower


def test_the_protocol_penalty_is_proportional_to_how_much_of_the_window_is_ceremony():
    one = _scores(NEWS, _video("x"), f"{GREETING} " + " ".join([ANNOUNCEMENT] * 5))["substance_score"]
    most = _scores(NEWS, _video("x"), f"{GREETING} {GREETING} {GREETING} {ANNOUNCEMENT}")["substance_score"]
    assert 0.8 < one <= 1.0 and most < 0.5
    assert _scores(NEWS, _video("x"), ANNOUNCEMENT)["substance_score"] == 1.0


def test_numbers_count_when_they_come_with_what_they_count():
    meaningful = _scores(NEWS, _video("x"), "Inflasi naik 3,2 persen dan investasi mencapai Rp 300 triliun pada tahun 2026.")
    bare = _scores(NEWS, _video("x"), "Ada nomor 7 di sebelah pintu dan ruang 12 di lantai 3.")
    assert meaningful["numbers_score"] > 0.5
    assert bare["numbers_score"] == 0.0


def test_news_with_no_keyword_matches_still_gets_a_substance_based_baseline():
    text = "The committee met in the afternoon and discussed several routine matters at length."
    scores = _scores(NEWS, _video(text), text)
    assert scores["announcement_score"] == scores["critical_score"] == scores["buzz_score"] == 0.0
    assert scores["substance_score"] == 1.0
    assert 0.0 < NEWS.combine_heuristics(scores) < _rank(NEWS, _video(ANNOUNCEMENT), ANNOUNCEMENT)


def test_news_is_not_only_about_government_business_and_tech_news_register():
    text = "The company announced a product launch today, with revenue growth of 15 percent and a new regulation on data."
    scores = _scores(NEWS, _video(text), text)
    assert scores["announcement_score"] > 0.5 and scores["numbers_score"] > 0


def test_news_keeps_indonesian_and_english_cues():
    assert _scores(NEWS, _video("x"), "Ini kebijakan baru soal subsidi dan korupsi, sebenarnya terungkap kemarin.")["critical_score"] > 0
    assert _scores(NEWS, _video("x"), "Our plan is a new tax reform, and the investigation revealed fraud.")["critical_score"] > 0
    assert _scores(NEWS, _video("x"), "Jujur, ternyata faktanya begini.")["buzz_score"] > 0


def test_news_repeating_a_keyword_does_not_inflate_it():
    assert _scores(NEWS, _video("x"), "korupsi")["critical_score"] == _scores(NEWS, _video("x"), "korupsi korupsi korupsi korupsi")["critical_score"]


def test_news_prompt_covers_general_news_and_never_inventing():
    prompt = NEWS.build_system_prompt().lower()
    for topic in ("technology", "business", "economics", "public announcements", "current affairs", "interview"):
        assert topic in prompt
    for kind in ("important information", "significant announcement", "relevant fact", "concrete promise", "clarification", "revelation"):
        assert kind in prompt
    assert "never invent" in prompt and "exaggerate" in prompt
    assert "does not make what follows it any less important" in prompt


# --------------------------------------------------------------------------- candidate windows

class _NeverCalledLLM(LLMProvider):
    def complete_json(self, system_prompt, user_prompt):
        raise AssertionError("the LLM must not be called when there is nothing to score")


@pytest.mark.parametrize("strategy", [PODCAST, NEWS, GAMING], ids=["podcast", "news", "gaming"])
def test_an_empty_transcript_with_no_signals_yields_no_candidates_and_no_llm_call(strategy):
    assert strategy.find_highlights(_video("", duration=120.0), _NeverCalledLLM()) == []


def test_a_video_shorter_than_one_window_still_gets_one_window():
    windows = PODCAST._generate_candidate_windows(_video(INSIGHT, duration=12.0))
    assert [(w.start, w.end) for w in windows] == [(0.0, 12.0)]


def test_a_zero_length_video_has_no_windows_and_unknown_duration_is_inferred_from_the_content():
    unknown = VideoAnalysisInput(
        video_duration=0.0, transcript_segments=[TranscriptSegmentResult(start=0.0, end=30.0, text=INSIGHT)]
    )
    assert PODCAST._generate_candidate_windows(unknown)[0].end == 30.0  # duration taken from the transcript
    assert PODCAST._generate_candidate_windows(VideoAnalysisInput(video_duration=0.0)) == []


def test_a_tiny_trailing_window_is_skipped_because_the_previous_one_covers_it():
    segments = [TranscriptSegmentResult(start=float(t), end=float(t) + 8, text=INSIGHT) for t in range(0, 40, 10)]
    video = VideoAnalysisInput(video_duration=40.0, transcript_segments=segments)
    assert [(w.start, w.end) for w in PODCAST._generate_candidate_windows(video)] == [(0.0, 30.0), (15.0, 40.0)]


def test_candidate_ids_are_stable_and_unique():
    segments = [TranscriptSegmentResult(start=float(t), end=float(t) + 10, text=INSIGHT) for t in range(0, 120, 12)]
    video = VideoAnalysisInput(video_duration=120.0, transcript_segments=segments)
    first = [w.candidate_id for w in PODCAST._generate_candidate_windows(video)]
    assert first == [w.candidate_id for w in PODCAST._generate_candidate_windows(video)]
    assert len(first) == len(set(first)) and first[:3] == ["c0", "c15", "c30"]


# --------------------------------------------------------------------------- temporal diversity

def _window(start, score):
    return _CandidateWindow(candidate_id=f"c{int(start)}", start=start, end=start + 30.0, text="t", heuristic_scores={}, heuristic_score=score)


def _long_pool(hot_until=300.0, duration=3600.0):
    """A one-hour video where every window in the first five minutes ranks above every window after it."""
    return [_window(float(t), 0.9 - t / 100000 if t < hot_until else 0.3 + (t % 90) / 1000) for t in range(0, int(duration), 15)]


def test_candidates_are_spread_across_the_video_not_piled_into_the_best_section():
    chosen = select_diverse_candidates(_long_pool(), limit=20, duration=3600.0)

    assert len(chosen) == 20
    sections = {int(w.start // 450) for w in chosen}  # 8 sections of 450 s
    assert len(sections) >= 6  # a naive top-20 would sit entirely in section 0
    assert sum(1 for w in chosen if w.start < 450) <= 6  # and no section hogs the slots


def test_the_best_windows_are_still_chosen_and_none_overlap_in_the_quality_pass():
    chosen = select_diverse_candidates(_long_pool(), limit=20, duration=3600.0)
    top = max(_long_pool(), key=lambda w: w.heuristic_score)
    assert top.candidate_id in {w.candidate_id for w in chosen}
    starts = sorted(w.start for w in chosen)
    assert all(b - a >= 30.0 for a, b in zip(starts, starts[1:]))  # windows are 30 s: none overlap here


def test_some_slots_go_to_windows_the_heuristics_ranked_low():
    pool = _long_pool()
    chosen = select_diverse_candidates(pool, limit=20, duration=3600.0)
    cutoff = sorted((w.heuristic_score for w in pool), reverse=True)[19]  # the 20th-best score
    assert any(w.heuristic_score < cutoff for w in chosen)  # exploration took something a plain top-20 would not


def test_the_selection_is_returned_in_time_order_and_small_pools_pass_through():
    chosen = select_diverse_candidates(_long_pool(), limit=20, duration=3600.0)
    assert [w.start for w in chosen] == sorted(w.start for w in chosen)

    few = [_window(30.0, 0.1), _window(0.0, 0.9), _window(15.0, 0.5)]
    assert [w.start for w in select_diverse_candidates(few, limit=20)] == [0.0, 15.0, 30.0]


def test_a_short_video_still_fills_its_slots_even_though_few_windows_are_apart_enough():
    pool = [_window(float(t), 0.5) for t in range(0, 300, 15)]  # 20 overlapping windows in 5 minutes... pick 12
    chosen = select_diverse_candidates(pool, limit=12, duration=330.0)
    assert len(chosen) == 12 and len({w.candidate_id for w in chosen}) == 12


def test_find_highlights_sends_at_most_the_candidate_limit_to_the_llm(mocker):
    calls = []

    class _Recorder(LLMProvider):
        def complete_json(self, system_prompt, user_prompt):
            calls.append(user_prompt)
            return {"highlights": []}

    segments = [TranscriptSegmentResult(start=float(t), end=float(t) + 12, text=INSIGHT) for t in range(0, 1800, 15)]
    video = VideoAnalysisInput(video_duration=1800.0, transcript_segments=segments)
    PODCAST.find_highlights(video, _Recorder())

    assert len(calls) == 1
    assert calls[0].count('"candidate_id"') == strategy_module._MAX_CANDIDATES_TO_LLM or calls[0].count("'candidate_id'") == 20
    assert math.isfinite(PODCAST._generate_candidate_windows(video)[0].heuristic_score)
