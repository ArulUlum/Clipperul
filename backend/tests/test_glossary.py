from app.services.ai.glossary import apply_glossary_corrections, correct_transcription
from app.services.ai.transcription import TranscriptionResult, TranscriptSegmentResult


def test_apply_glossary_corrections_fixes_known_terms():
    assert apply_glossary_corrections("sampe piti 40, baru 10") == "sampe pity 40, baru 10"
    assert apply_glossary_corrections("yang mau gaca disini") == "yang mau gacha disini"


def test_apply_glossary_corrections_preserves_capitalization():
    assert apply_glossary_corrections("Piti sudah 70") == "Pity sudah 70"
    assert apply_glossary_corrections("GACA lagi bang") == "GACHA lagi bang"


def test_apply_glossary_corrections_only_matches_whole_words():
    # "piti" embedded inside a longer word must not be touched.
    text = "pititik dua"
    assert apply_glossary_corrections(text) == text


def test_apply_glossary_corrections_handles_empty_and_unrelated_text():
    assert apply_glossary_corrections("") == ""
    assert apply_glossary_corrections("Ini kalimat biasa saja.") == "Ini kalimat biasa saja."


def test_correct_transcription_rebuilds_full_text_from_corrected_segments():
    original = TranscriptionResult(
        language="id",
        full_text="mau gaca disini, sampe piti 40",
        segments=[
            TranscriptSegmentResult(start=0.0, end=2.0, text="mau gaca disini"),
            TranscriptSegmentResult(start=2.0, end=4.0, text="sampe piti 40"),
        ],
    )

    corrected = correct_transcription(original)

    assert [s.text for s in corrected.segments] == ["mau gacha disini", "sampe pity 40"]
    assert corrected.full_text == "mau gacha disini sampe pity 40"
    assert corrected.language == "id"
    # Original is left untouched.
    assert original.segments[0].text == "mau gaca disini"
