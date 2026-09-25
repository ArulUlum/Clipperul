import re

from app.services.ai.transcription import TranscriptionResult, TranscriptSegmentResult

# Known Whisper mis-transcriptions of English gaming loanwords when the audio is detected as
# Indonesian. Whisper isn't mishearing these — once it locks onto Indonesian, it writes
# code-switched English terms the way they SOUND in Indonesian spelling, which a bigger model
# doesn't fix either (verified: "small" produces the exact same spellings as "base"). A plain
# glossary correction does, cheaply. Add more entries here as new consistent mis-transcriptions
# are found — each key is matched whole-word, case-insensitively.
GLOSSARY_CORRECTIONS: dict[str, str] = {
    "piti": "pity",
    "gaca": "gacha",
    "gacar": "gacha",
}

_PATTERN = re.compile(r"\b(" + "|".join(re.escape(k) for k in GLOSSARY_CORRECTIONS) + r")\b", re.IGNORECASE)


def _match_case(matched: str, replacement: str) -> str:
    if matched.isupper():
        return replacement.upper()
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_glossary_corrections(text: str) -> str:
    if not text:
        return text
    return _PATTERN.sub(lambda m: _match_case(m.group(0), GLOSSARY_CORRECTIONS[m.group(0).lower()]), text)


def correct_transcription(result: TranscriptionResult) -> TranscriptionResult:
    """Applies glossary corrections to every segment and rebuilds full_text from the corrected
    segments — a new TranscriptionResult, the input is left untouched."""
    corrected_segments = [
        TranscriptSegmentResult(start=seg.start, end=seg.end, text=apply_glossary_corrections(seg.text))
        for seg in result.segments
    ]
    return TranscriptionResult(
        language=result.language,
        full_text=" ".join(seg.text for seg in corrected_segments),
        segments=corrected_segments,
    )
