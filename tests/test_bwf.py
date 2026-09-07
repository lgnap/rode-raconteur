"""BWF parsing: the chunks the device writes, and the start time."""

import struct
from datetime import datetime
from pathlib import Path

from conteur.bwf import chunks, started_at, cue_points, split


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


def _cue(*positions: int) -> bytes:
    body = struct.pack("<I", len(positions))
    for i, pos in enumerate(positions, 1):
        body += struct.pack("<II4sIII", i, pos, b"data", 0, 0, pos)
    return _chunk(b"cue ", body)


def test_cue_points_are_read_as_sample_offsets(tmp_path):
    path = tmp_path / "m.wav"
    path.write_bytes(_riff(_fmt(), _cue(4800, 9600), _chunk(b"data", b"\x00" * 64)))
    with path.open("rb") as fh:
        assert cue_points(fh) == [4800, 9600]


def test_a_take_without_markers_has_no_cue_points(tmp_path):
    path = tmp_path / "n.wav"
    path.write_bytes(_riff(_fmt(), _cue(), _chunk(b"data", b"\x00" * 64)))
    with path.open("rb") as fh:
        assert cue_points(fh) == []


def test_splitting_keeps_every_byte_of_audio(tmp_path):
    """Nothing is discarded: a marker may mean a start, an end, or just a
    passage worth revisiting, and the tool cannot tell."""
    audio = bytes(range(256)) * 16          # 4096 bytes = 1024 frames
    path = tmp_path / "s.wav"
    path.write_bytes(_riff(_fmt(), _cue(400, 800), _chunk(b"data", audio)))

    parts = split(path, tmp_path / "out")
    assert len(parts) == 3

    joined = b""
    for part in parts:
        with part.open("rb") as fh:
            offset, size = chunks(fh)["data"]
            fh.seek(offset)
            joined += fh.read(size)
    assert joined == audio


def test_a_take_without_markers_is_not_split(tmp_path):
    path = tmp_path / "one.wav"
    path.write_bytes(_riff(_fmt(), _cue(), _chunk(b"data", b"\x00" * 64)))
    assert split(path, tmp_path / "out") == []


def test_the_caller_names_the_parts_and_is_told_where_each_one_starts(tmp_path):
    """bwf knows nothing of anyone's naming rules. It hands over each part's
    offset in samples and the file's own sample rate, which is what a caller
    needs to work out when that part started."""
    audio = bytes(range(256)) * 16          # 4096 bytes = 1024 frames
    path = tmp_path / "s.wav"
    path.write_bytes(_riff(_fmt(), _cue(400, 800), _chunk(b"data", audio)))
    seen = []

    def name_for(index, total, start_frame, rate):
        seen.append((index, total, start_frame, rate))
        return f"morceau-{index}-de-{total}.wav"

    parts = split(path, tmp_path / "out", name_for=name_for)
    assert seen == [(1, 3, 0, 48000), (2, 3, 400, 48000), (3, 3, 800, 48000)]
    assert [p.name for p in parts] == ["morceau-1-de-3.wav",
                                       "morceau-2-de-3.wav",
                                       "morceau-3-de-3.wav"]
