"""BWF parsing: the chunks the device writes, and the start time."""

import struct
from datetime import datetime

from conteur.bwf import chunks, started_at


def _riff(*parts: bytes) -> bytes:
    body = b"WAVE" + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunk(cid: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def _fmt() -> bytes:
    # IEEE float, mono, 48 kHz, 32-bit — what the transmitters write.
    return _chunk(b"fmt ", struct.pack("<HHIIHH", 3, 1, 48000, 48000 * 4, 4, 32))


def _bext(date: bytes, time_: bytes) -> bytes:
    body = bytearray(602)
    body[256:256 + 17] = b"RODE Wireless PRO"
    body[320:330] = date
    body[330:338] = time_
    return _chunk(b"bext", bytes(body))


def test_chunks_are_located_by_id(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(_riff(_fmt(), _chunk(b"data", b"\x00" * 16)))
    with path.open("rb") as fh:
        found = chunks(fh)
    assert set(found) == {"fmt ", "data"}
    assert found["data"][1] == 16


def test_start_time_comes_from_bext(tmp_path):
    path = tmp_path / "b.wav"
    path.write_bytes(_riff(_fmt(), _bext(b"0026-09-07", b"11:10:30"),
                           _chunk(b"data", b"\x00" * 16)))
    with path.open("rb") as fh:
        # The closing time is deliberately wrong: bext must win.
        when = started_at(fh, closed_at=datetime(2026, 9, 7, 11, 14), rate=48000, frames=0)
    assert when == datetime(2026, 9, 7, 11, 10, 30)


def test_the_firmware_year_is_repaired(tmp_path):
    """The firmware writes 0026 rather than 2026, in bext and in iXML alike.

    Left alone, takes land in Enregistrements/0026-09/.
    """
    path = tmp_path / "c.wav"
    path.write_bytes(_riff(_fmt(), _bext(b"0026-01-02", b"03:04:05"),
                           _chunk(b"data", b"")))
    with path.open("rb") as fh:
        when = started_at(fh, closed_at=datetime(2026, 1, 2), rate=48000, frames=0)
    assert when.year == 2026


def test_without_bext_the_start_is_the_close_minus_the_duration(tmp_path):
    """Three takes out of eight carry no bext at all, with no visible rule.

    The fallback is the path of a third of the files, not an edge case.
    """
    path = tmp_path / "d.wav"
    path.write_bytes(_riff(_fmt(), _chunk(b"data", b"\x00" * 16)))
    with path.open("rb") as fh:
        when = started_at(fh, closed_at=datetime(2026, 9, 7, 11, 14, 32),
                          rate=48000, frames=48000 * 30)
    assert when == datetime(2026, 9, 7, 11, 14, 2)
