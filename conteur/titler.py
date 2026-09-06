"""Fabrication d'un titre. Le client HTTP est injectable pour les tests."""

import re
import unicodedata
from collections import Counter

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
