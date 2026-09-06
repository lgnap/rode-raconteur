"""Décision du nom de fichier. N'importe ni Qt, ni PyAudio, ni Whisper."""

import re
import unicodedata


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
