"""Making a title out of a transcript."""

import re
import unicodedata
from collections import Counter

from conteur.naming import ORIGIN_KEYWORDS, ORIGIN_TITLE

# French stopwords: the transcripts, and the titles, are in French.
STOPWORDS = {
    "alors", "apres", "aussi", "autre", "avait", "avec", "avoir", "bien",
    "cela", "cette", "comme", "dans", "deux", "dire", "donc", "elle", "elles",
    "encore", "etait", "etaient", "etre", "faire", "fait", "ils", "leur",
    "leurs", "mais", "meme", "moins", "nous", "parce", "pour", "quand", "que",
    "quel", "quelle", "sans", "ses", "sont", "sous", "sur", "tous", "tout",
    "toute", "toutes", "tres", "une", "vers", "voir", "vous", "etais", "chez",
    "plus", "peu", "beaucoup", "ensuite", "puis", "rien", "jamais", "toujours",
}
MIN_LEN = 4


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def keywords(text: str, n: int = 3) -> list[str]:
    """The n most frequent words, minus stopwords, numbers and short words."""
    words = re.findall(r"[a-z]+", _fold(text))
    kept = [w for w in words if len(w) >= MIN_LEN and w not in STOPWORDS]
    return [w for w, _ in Counter(kept).most_common(n)]


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"
# Measured: 30 s on the first call, the time Ollama needs to load qwen3:8b
# into VRAM, then 0.7 s warm. A 20 s timeout therefore sent the first title of
# every session to the keyword fallback, every time.
TIMEOUT_S = 60.0
MAX_TITLE_LEN = 120

PROMPT = (
    "Voici la transcription d'un enregistrement en français.\n"
    "Donne un titre court, en français, de trois à huit mots.\n"
    "Réponds uniquement par le titre, sur une seule ligne, "
    "sans guillemets ni ponctuation finale.\n\n"
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(raw: str) -> str:
    """Strip the reasoning blocks qwen3 may emit despite think=False."""
    return _THINK.sub("", raw).strip()


def title_from_ollama(text: str, post=None, timeout_s: float = TIMEOUT_S) -> str | None:
    """A short title via Ollama, or None if the answer is unusable."""
    if post is None:
        import requests

        post = requests.post
    try:
        response = post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": PROMPT + text,
                "stream": False,
                "think": False,
                # Give the VRAM back immediately: measured, Whisper takes
                # 2033 MiB and qwen3:8b 5470 out of 8192. Both fit at rest,
                # but the remaining 690 MiB are not enough for the working set
                # of a multi-minute transcription — hence a run of
                # "CUDA out of memory".
                "keep_alive": 0,
            },
            timeout=timeout_s,
        )
        response.raise_for_status()
        candidate = strip_think(response.json().get("response", ""))
    except Exception:
        return None
    if (
        not candidate
        or "<think" in candidate
        or "\n" in candidate
        or len(candidate) > MAX_TITLE_LEN
    ):
        return None
    return candidate


def make_title(text: str, post=None) -> str:
    """An Ollama title when possible, keywords otherwise. Never raises."""
    return make_title_tracked(text, post=post)[0]


def make_title_tracked(text: str, post=None) -> tuple[str, str]:
    """Like make_title, but says where the result came from.

    The UI needs to tell a model-written title from a fallback on keywords:
    that is useful information when reading back a list of takes.
    """
    title = title_from_ollama(text, post=post)
    if title:
        return title, ORIGIN_TITLE
    return " ".join(keywords(text)), ORIGIN_KEYWORDS
