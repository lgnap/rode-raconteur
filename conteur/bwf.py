"""Broadcast WAV: the chunks the transmitters write, and what we read from them.

Markers, start time and splitting all read the same chunks, so they live in one
reader rather than three that would drift apart.
"""

import struct
from datetime import datetime, timedelta

# The firmware writes the origination year as 0026 instead of 2026, in bext and
# in iXML alike — two independent sources, so it is the firmware, not our
# decoding. Uncorrected, takes are filed under Enregistrements/0026-09/.
FIRMWARE_YEAR_BUG = 2000


def chunks(fh) -> dict[str, tuple[int, int]]:
    """Map chunk id to (payload offset, size). Stops after `data`.

    The audio payload is never walked: `data` is the last thing we need, and on
    a 700 MB take walking past it would mean reading all of it.
    """
    fh.seek(0)
    header = fh.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise ValueError("not a WAVE file")
    found: dict[str, tuple[int, int]] = {}
    while True:
        head = fh.read(8)
        if len(head) < 8:
            return found
        cid, size = struct.unpack("<4sI", head)
        found[cid.decode("latin1")] = (fh.tell(), size)
        if cid == b"data":
            return found
        fh.seek(size + (size & 1), 1)


def _bext_origination(fh, offset: int, size: int) -> datetime | None:
    if size < 338:
        return None
    fh.seek(offset + 320)
    raw = fh.read(18)
    try:
        date = raw[:10].decode("latin1")
        clock = raw[10:18].decode("latin1")
        when = datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H:%M:%S")
    except (ValueError, UnicodeDecodeError):
        return None
    if when.year < 1000:
        when = when.replace(year=when.year + FIRMWARE_YEAR_BUG)
    return when


def started_at(fh, closed_at: datetime, rate: int, frames: int) -> datetime:
    """When the take started.

    `bext` carries the start; the filesystem carries the close. Where `bext` is
    missing — a third of the files, with no visible rule — the start is derived
    from the close and the duration.
    """
    found = chunks(fh)
    if "bext" in found:
        when = _bext_origination(fh, *found["bext"])
        if when is not None:
            return when
    return closed_at - timedelta(seconds=frames / rate if rate else 0)
