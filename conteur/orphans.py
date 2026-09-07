"""Takes left without a name: recovery at startup.

When naming fails — model unavailable, application killed, crash — the WAV
stays on disk under its provisional name. Without recovery it would stay there
forever, which is all the more annoying because these are the field takes, the
ones you have just come back to the desk to sort out.
"""

import re
from datetime import datetime
from pathlib import Path

from conteur.naming import UNNAMED

# `<timestamp>_sans-nom.wav`, with its collision suffix if any.
STAMP = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})_"
    r"(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})_"
)


def parse_timestamp(name: str) -> datetime | None:
    """The timestamp carried by a file name, or None if it has none."""
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
    """True for a take still carrying the provisional slug.

    Two shapes exist. A take recorded here is `<stamp>_sans-nom.wav`. An
    imported one carries the card's name in between:
    `<stamp>_00002_Source-Baffle__sans-nom.wav`.

    The slug is therefore read from the last `__` segment when there is one,
    the same rule tools/nommer-morceaux.py uses to decide a file is already
    named. Splitting on the first underscore made every imported take
    invisible to recovery.
    """
    if not name.endswith(".wav") or parse_timestamp(name) is None:
        return False
    stem = name[: -len(".wav")]
    if "__" in stem:
        tail = stem.rsplit("__", 1)[1]
    else:
        tail = stem.split("_", 2)[-1] if stem.count("_") >= 2 else ""
    return tail == UNNAMED or re.fullmatch(rf"{re.escape(UNNAMED)}-\d+", tail) is not None


def find_orphans(root: Path) -> list[tuple[Path, datetime]]:
    """Unnamed takes under `root`, in order, with their timestamp.

    The scan is recursive: a take from an earlier month must not be forgotten
    because the month has changed in the meantime.
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
