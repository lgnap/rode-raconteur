"""The startup-transient tool — what it silences, and what it must not touch."""

import importlib.util
import json
import struct
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "mute-startup.py"

RATE = 48000
BURST_MS = 100
RAMP_MS = 5


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("mute_startup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chunk(cid: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def _riff(*parts: bytes) -> bytes:
    body = b"WAVE" + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _fmt_float() -> bytes:
    return _chunk(b"fmt ", struct.pack("<HHIIHH", 3, 1, RATE, RATE * 4, 4, 32))


def _fmt_pcm16() -> bytes:
    return _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16))


def _bext() -> bytes:
    body = bytearray(602)
    body[256:256 + 17] = b"RODE Wireless PRO"
    body[320:330] = b"0026-09-08"
    body[330:338] = b"07:15:45"
    return _chunk(b"bext", bytes(body))


def _cue() -> bytes:
    # One cue point at frame 96000 — the marker the splitter reads.
    point = struct.pack("<II4sIII", 1, 96000, b"data", 0, 0, 96000)
    return _chunk(b"cue ", struct.pack("<I", 1) + point)


def _square(frames: int, period: int = 50) -> np.ndarray:
    t = np.arange(frames)
    return np.where((t // (period // 2)) % 2 == 0, 1.0, -1.0)


def _speech(frames: int, amp: float = 0.02, seed: int = 3) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, amp, frames)


def _write(path: Path, samples: np.ndarray, fmt=_fmt_float, pcm16=False) -> Path:
    if pcm16:
        raw = np.clip(samples * 32767, -32768, 32767).astype("<i2").tobytes()
    else:
        raw = samples.astype("<f4").tobytes()
    path.write_bytes(_riff(fmt(), _bext(), _cue(), _chunk(b"data", raw)))
    return path


def _take_with_transient(frames_of_speech: int = RATE) -> np.ndarray:
    return np.concatenate([_square(RATE * BURST_MS // 1000),
                           _speech(frames_of_speech)])


def _data_of(path: Path, dtype="<f4") -> np.ndarray:
    from conteur.bwf import chunks
    with path.open("rb") as fh:
        off, size = chunks(fh)["data"]
        fh.seek(off)
        return np.frombuffer(fh.read(size), dtype=dtype)


def test_the_transient_is_silenced_and_the_take_is_not(tool, tmp_path):
    """The point of the tool: the saturated opening goes to zero, and the
    first sample of the recording proper is left exactly as it was."""
    path = _write(tmp_path / "take.wav", _take_with_transient())
    before = _data_of(path)
    burst = RATE * BURST_MS // 1000

    assert tool.mute_startup(path) == burst

    after = _data_of(path)
    silent = burst - RATE * RAMP_MS // 1000
    assert not after[:silent].any()
    assert after[burst:].tobytes() == before[burst:].tobytes()


def test_the_junction_is_ramped_rather_than_cut(tool, tmp_path):
    """A hard step back to full audio is itself a click — the defect we are
    removing. The last 5 ms rise instead, which is a splice, not a fade-in."""
    path = _write(tmp_path / "take.wav", _take_with_transient())
    before = _data_of(path)
    burst = RATE * BURST_MS // 1000
    ramp = RATE * RAMP_MS // 1000

    tool.mute_startup(path)

    after = _data_of(path)
    junction = after[burst - ramp:burst]
    original = before[burst - ramp:burst]
    assert abs(junction[0]) < abs(original[0])          # starts from silence
    assert junction[-1] == pytest.approx(original[-1], rel=0.05)
    assert junction.any()                                # not silence either


def test_the_other_chunks_and_the_length_survive(tool, tmp_path):
    """bext carries the timestamp and cue carries the markers: lose either and
    the file can no longer be split, or claims the wrong time of day."""
    from conteur.bwf import chunks
    path = _write(tmp_path / "take.wav", _take_with_transient())
    size_before = path.stat().st_size
    with path.open("rb") as fh:
        found = chunks(fh)
        fh.seek(found["bext"][0]); bext_before = fh.read(found["bext"][1])
        fh.seek(found["cue "][0]); cue_before = fh.read(found["cue "][1])

    tool.mute_startup(path)

    assert path.stat().st_size == size_before
    with path.open("rb") as fh:
        found = chunks(fh)
        fh.seek(found["bext"][0]); assert fh.read(found["bext"][1]) == bext_before
        fh.seek(found["cue "][0]); assert fh.read(found["cue "][1]) == cue_before


def test_a_take_that_opens_normally_is_not_touched(tool, tmp_path):
    """Takes rolled over mid-session open at speaking level. Silencing their
    first 100 ms would remove speech, so the file must come back untouched."""
    path = _write(tmp_path / "take.wav", _speech(RATE * 2, amp=0.1))
    before = path.read_bytes()

    assert tool.mute_startup(path) == 0
    assert path.read_bytes() == before


def test_a_16_bit_recording_is_handled_too(tool, tmp_path):
    """The application records 16-bit PCM from the receiver; the cards hold
    32-bit float. One tool, both formats, or half the library is unreachable."""
    path = _write(tmp_path / "take.wav", _take_with_transient(),
                  fmt=_fmt_pcm16, pcm16=True)
    burst = RATE * BURST_MS // 1000

    assert tool.mute_startup(path) == burst

    after = _data_of(path, dtype="<i2")
    assert not after[:burst - RATE * RAMP_MS // 1000].any()


def test_replacing_in_place_refuses_a_file_the_ledger_still_holds(tool, tmp_path,
                                                                 isolated_data_home):
    """Patching the audio changes the digest, and the digest is what allows a
    card to be erased. Doing this between an import and the erase closes the
    lock for good: the copy no longer matches, and the card can no longer be
    proved safe to wipe."""
    from conteur.card import sha256_file
    path = _write(tmp_path / "take.wav", _take_with_transient())
    imports = isolated_data_home / "conteur" / "imports"
    imports.mkdir(parents=True)
    (imports / "800A-92D6.json").write_text(json.dumps({"takes": [{
        "digest": sha256_file(path), "card_name": "00001_Source-Baffle.WAV",
        "size": path.stat().st_size, "folder": str(tmp_path),
        "prefix": path.stem, "imported_at": "2026-09-08T07:00:00",
    }]}), encoding="utf-8")

    assert tool.held_by_ledger(path) is True

    before = path.read_bytes()
    with pytest.raises(tool.StillHeld):
        tool.mute_startup(path, guard=True)
    assert path.read_bytes() == before

    assert tool.mute_startup(path, guard=False) > 0
