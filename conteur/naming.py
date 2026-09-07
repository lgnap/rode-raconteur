"""Deciding a file name. Imports neither Qt, nor PyAudio, nor Whisper."""

import re
import unicodedata
from collections.abc import Callable


def slugify(text: str, max_len: int = 60) -> str:
    """Return a lowercase ASCII slug, cut on a word boundary."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if len(slug) <= max_len:
        return slug
    if slug[max_len] == "-":
        # The cut already falls on a word boundary: drop nothing.
        return slug[:max_len].strip("-")
    cut = slug[:max_len]
    if "-" in cut:
        cut = cut[: cut.rindex("-")]
    return cut.strip("-")


# User-visible words: they end up in the file names on disk, and the
# application is used in French. Translating them would rename every future
# take away from the ones already on disk.
SILENCE = "silence"
NO_SPEECH = "sans-parole"
UNNAMED = "sans-nom"
# A take that was cut at its markers. It is named without being listened to:
# its parts carry the content, and transcribing both is the same audio twice.
SPLIT = "decoupee"

# Where a take's name came from. These tokens are internal and stable: they
# travel between `job.py`, `titler.py`, the queue and the UI, and the tests
# rely on them. Nothing displays them as-is — the UI goes through
# `origin_label`, the single translation, defined here so it exists only once.
ORIGIN_TRANSCRIPT = "transcript"
ORIGIN_TITLE = "title"
ORIGIN_KEYWORDS = "keywords"
ORIGIN_TIMECODE = "timecode"
ORIGIN_SILENCE = SILENCE
ORIGIN_NO_SPEECH = NO_SPEECH
ORIGIN_FAILED = "echec"
ORIGIN_MANUAL = "manuel"

ORIGIN_LABELS = {
    ORIGIN_TRANSCRIPT: "transcription",
    ORIGIN_NO_SPEECH: "aucune parole",
    ORIGIN_TITLE: "titre généré",
    ORIGIN_KEYWORDS: "mots-clés",
    ORIGIN_TIMECODE: "canal timecode",
    ORIGIN_SILENCE: "silence",
    ORIGIN_FAILED: "échec",
    ORIGIN_MANUAL: "manuel",
}


def origin_label(origin: str) -> str:
    """French label for an origin, for display only."""
    return ORIGIN_LABELS.get(origin, origin)


def choose_name(
    text: str,
    speech_s: float,
    make_title: Callable[[str], str],
    threshold_s: float = 12.0,
) -> str:
    """Pick the strategy and return the final slug.

    `make_title` is only called past the threshold. Injecting it keeps this
    module testable without a network or a model.
    """
    text = text.strip()
    if not text:
        return SILENCE
    source = text if speech_s <= threshold_s else make_title(text)
    return slugify(source) or UNNAMED
