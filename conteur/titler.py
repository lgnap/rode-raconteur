"""Fabrication d'un titre à partir d'une transcription."""

import re
import unicodedata
from collections import Counter

from conteur.naming import ORIGIN_KEYWORDS, ORIGIN_TITLE

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
    """Les n mots les plus fréquents, hors mots-vides, nombres et mots courts."""
    words = re.findall(r"[a-z]+", _fold(text))
    kept = [w for w in words if len(w) >= MIN_LEN and w not in STOPWORDS]
    return [w for w, _ in Counter(kept).most_common(n)]


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"
# Mesuré : 30 s au premier appel, le temps qu'Ollama charge qwen3:8b en VRAM,
# puis 0,7 s à chaud. Un délai de 20 s faisait donc systématiquement basculer le
# premier titre de chaque session sur le repli mots-clés.
TIMEOUT_S = 60.0
WARM_UP_TIMEOUT_S = 120.0
MAX_TITLE_LEN = 120

PROMPT = (
    "Voici la transcription d'un enregistrement en français.\n"
    "Donne un titre court, en français, de trois à huit mots.\n"
    "Réponds uniquement par le titre, sur une seule ligne, "
    "sans guillemets ni ponctuation finale.\n\n"
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(raw: str) -> str:
    """Retire les blocs de raisonnement que qwen3 peut émettre malgré think=False."""
    return _THINK.sub("", raw).strip()


def title_from_ollama(text: str, post=None, timeout_s: float = TIMEOUT_S) -> str | None:
    """Titre court via Ollama, ou None si la réponse est inexploitable."""
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


def warm_up(post=None) -> bool:
    """Charge le modèle de titrage en VRAM, hors du chemin critique.

    Sans cela le premier titre d'une session attend le chargement (30 s mesurées)
    et bascule sur le repli mots-clés. Ne lève jamais : Ollama absent est un cas
    normal, le repli existe pour ça.
    """
    if post is None:
        import requests

        post = requests.post
    try:
        post(OLLAMA_URL,
             json={"model": OLLAMA_MODEL, "prompt": "ok", "stream": False, "think": False},
             timeout=WARM_UP_TIMEOUT_S)
    except Exception:
        return False
    return True


def make_title(text: str, post=None) -> str:
    """Titre Ollama si possible, sinon les mots-clés. Ne lève jamais."""
    return make_title_tracked(text, post=post)[0]


def make_title_tracked(text: str, post=None) -> tuple[str, str]:
    """Comme make_title, mais dit d'où vient le résultat.

    L'interface a besoin de distinguer un titre produit par le modèle d'un
    repli sur les mots-clés : c'est une information utile quand on relit une
    liste de prises.
    """
    title = title_from_ollama(text, post=post)
    if title:
        return title, ORIGIN_TITLE
    return " ".join(keywords(text)), ORIGIN_KEYWORDS
