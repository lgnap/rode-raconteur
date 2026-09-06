"""Décision du nom de fichier. N'importe ni Qt, ni PyAudio, ni Whisper."""

import re
import unicodedata
from collections.abc import Callable


def slugify(text: str, max_len: int = 60) -> str:
    """Rend un slug ASCII minuscule, coupé sur une frontière de mot."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if len(slug) <= max_len:
        return slug
    if slug[max_len] == "-":
        # La coupe tombe déjà sur une frontière de mot : ne rien retirer.
        return slug[:max_len].strip("-")
    cut = slug[:max_len]
    if "-" in cut:
        cut = cut[: cut.rindex("-")]
    return cut.strip("-")


SILENCE = "silence"
NO_SPEECH = "sans-parole"
UNNAMED = "sans-nom"

# Origine du nom d'une prise. Les jetons sont internes et stables : ils
# circulent entre `job.py`, `titler.py`, la file et l'interface, et les tests
# s'appuient dessus. Rien ne les affiche tels quels — l'interface passe par
# `origin_label`, seule traduction, définie ici pour n'exister qu'une fois.
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
    """Libellé français d'une origine, pour l'affichage seul."""
    return ORIGIN_LABELS.get(origin, origin)


def choose_name(
    text: str,
    speech_s: float,
    make_title: Callable[[str], str],
    threshold_s: float = 12.0,
) -> str:
    """Décide de la stratégie et rend le slug final.

    `make_title` n'est appelé qu'au-delà du seuil. L'injecter garde ce module
    testable sans réseau ni modèle.
    """
    text = text.strip()
    if not text:
        return SILENCE
    source = text if speech_s <= threshold_s else make_title(text)
    return slugify(source) or UNNAMED
