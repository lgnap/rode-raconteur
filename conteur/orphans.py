"""Prises restées sans nom : rattrapage au démarrage.

Quand le nommage échoue — modèle indisponible, application tuée, plantage — le
WAV reste sur disque sous son nom provisoire. Sans rattrapage il y resterait pour
toujours, ce qui est d'autant plus gênant que ce sont les prises de terrain,
celles qu'on vient justement remettre en ordre devant le poste.
"""

import re
from datetime import datetime
from pathlib import Path

from conteur.naming import UNNAMED

# `<horodatage>_sans-nom.wav`, avec le suffixe de collision éventuel.
STAMP = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})_"
    r"(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})_"
)


def parse_timestamp(name: str) -> datetime | None:
    """Horodatage porté par le nom de fichier, ou None s'il n'en a pas."""
    match = STAMP.match(name)
    if match is None:
        return None
    g = match.groupdict()
    try:
        return datetime(
            int(g["y"]), int(g["mo"]), int(g["d"]),
            int(g["h"]), int(g["mi"]), int(g["s"]),
        )
    except ValueError:
        return None


def is_orphan(name: str) -> bool:
    """Vrai pour `<horodatage>_sans-nom.wav` et ses variantes de collision."""
    if not name.endswith(".wav") or parse_timestamp(name) is None:
        return False
    stem = name[: -len(".wav")]
    tail = stem.split("_", 2)[-1] if stem.count("_") >= 2 else ""
    return tail == UNNAMED or re.fullmatch(rf"{re.escape(UNNAMED)}-\d+", tail) is not None


def find_orphans(root: Path) -> list[tuple[Path, datetime]]:
    """Prises sans nom sous `root`, chronologiquement, avec leur horodatage.

    Le balayage est récursif : une prise d'un mois précédent ne doit pas être
    oubliée parce qu'on a changé de mois entre-temps.
    """
    found: list[tuple[Path, datetime]] = []
    if not root.is_dir():
        return found
    for path in root.rglob("*.wav"):
        if not is_orphan(path.name):
            continue
        when = parse_timestamp(path.name)
        if when is not None:
            found.append((path, when))
    return sorted(found, key=lambda pair: (pair[1], pair[0].name))
