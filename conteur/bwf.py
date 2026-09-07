"""Broadcast WAV: the chunks the transmitters write, and what we read from them.

Markers, start time and splitting all read the same chunks, so they live in one
reader rather than three that would drift apart.
"""

import struct
from datetime import datetime, timedelta
from pathlib import Path

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


COPY_BLOCK = 1 << 20
# Offset of TimeReference inside the bext structure (256 + 32 + 32 + 10 + 8).
BEXT_TIME_REFERENCE = 338


def cue_points(fh) -> list[int]:
    """Marker positions, in samples. Empty when the take carries none.

    The device always writes a `cue ` chunk, usually with a count of zero, and
    reserves a zero-filled `PAD ` chunk after it so markers can be written in
    place later without rewriting a multi-megabyte file.
    """
    found = chunks(fh)
    if "cue " not in found:
        return []
    offset, size = found["cue "]
    fh.seek(offset)
    payload = fh.read(size)
    if len(payload) < 4:
        return []
    count = struct.unpack("<I", payload[:4])[0]
    points = []
    for i in range(count):
        base = 4 + i * 24
        if base + 24 > len(payload):
            break
        points.append(struct.unpack("<I", payload[base + 20:base + 24])[0])
    return sorted(set(points))


def _shift_bext(bext: bytes, frames: int) -> bytes:
    """Move the BWF timestamp, or every part would claim to start at zero."""
    if len(bext) < BEXT_TIME_REFERENCE + 8:
        return bext
    low, high = struct.unpack("<II", bext[BEXT_TIME_REFERENCE:BEXT_TIME_REFERENCE + 8])
    value = (high << 32 | low) + frames
    patched = struct.pack("<II", value & 0xFFFFFFFF, value >> 32)
    return bext[:BEXT_TIME_REFERENCE] + patched + bext[BEXT_TIME_REFERENCE + 8:]


def _raw_chunk(cid: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def default_part_name(index: int, total: int, start_frame: int,
                      rate: int) -> str:
    """Position only. This module knows nothing of anyone's naming rules.

    A caller with rules of its own — a timestamp, the name the card carried —
    passes `name_for` instead. It gets the part's offset in samples and the
    file's sample rate, which is everything needed to work out when that part
    started, and bwf stays ignorant of what is done with it.
    """
    return f"{index:02d}_sur_{total:02d}.wav"


def split(path: Path, out_dir: Path, name_for=default_part_name) -> list[Path]:
    """Cut at every marker, keeping all the audio. Returns the parts written.

    Returns an empty list when the take has no markers: there is nothing to cut,
    and writing a single part identical to the source would only duplicate it.

    `name_for(index, total, start_frame, rate)` names each part; see
    `default_part_name`.

    Known limitation: the parts carry `fmt ` and `bext`, not `cue ` or `PAD `.
    Rejoining them restores the audio byte for byte but not the header, so a
    rejoined file has lost its markers and cannot be split again.
    """
    with path.open("rb") as fh:
        found = chunks(fh)
        if "fmt " not in found or "data" not in found:
            raise ValueError(f"{path.name}: missing fmt or data chunk")
        fmt_offset, fmt_size = found["fmt "]
        fh.seek(fmt_offset)
        fmt = fh.read(fmt_size)
        bext = None
        if "bext" in found:
            fh.seek(found["bext"][0])
            bext = fh.read(found["bext"][1])
        data_offset, data_size = found["data"]
        rate = struct.unpack("<I", fmt[4:8])[0]
        block_align = struct.unpack("<H", fmt[12:14])[0]
        frames = data_size // block_align
        marks = [p for p in cue_points(fh) if 0 < p < frames]
        if not marks:
            return []
        edges = [0, *marks, frames]
        bounds = [(a, b) for a, b in zip(edges, edges[1:]) if b > a]

        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        total = len(bounds)
        for index, (start, end) in enumerate(bounds, 1):
            target = out_dir / name_for(index, total, start, rate)
            head = _raw_chunk(b"fmt ", fmt)
            if bext is not None:
                head += _raw_chunk(b"bext", _shift_bext(bext, start))
            size = (end - start) * block_align
            with target.open("wb") as out:
                out.write(b"RIFF"
                          + struct.pack("<I", 4 + len(head) + 8 + size + (size & 1))
                          + b"WAVE")
                out.write(head)
                out.write(b"data" + struct.pack("<I", size))
                fh.seek(data_offset + start * block_align)
                remaining = size
                while remaining:
                    block = fh.read(min(COPY_BLOCK, remaining))
                    if not block:
                        raise IOError(f"{path.name}: short read")
                    out.write(block)
                    remaining -= len(block)
                if size & 1:
                    out.write(b"\x00")
            written.append(target)
        return written
