"""WAV reading that tolerates the formats the hardware produces.

The standard library's `wave` module refuses format 3 (IEEE float), which is
exactly what the RØDE onboard recordings use: 48 kHz, 32-bit float. Without
this the application could only name its own files.

Everything is brought back to the int16 scale the rest of the code expects.
"""

import struct
from pathlib import Path

import numpy as np

from conteur.signal import FULL_SCALE

WAVE_PCM = 1
WAVE_FLOAT = 3
WAVE_EXTENSIBLE = 0xFFFE


def _chunks(f):
    header = f.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise ValueError("not a WAVE file")
    while True:
        head = f.read(8)
        if len(head) < 8:
            return
        cid, size = struct.unpack("<4sI", head)
        pos = f.tell()
        yield cid, size, pos
        # Absolute, not relative: the caller may have moved the position to
        # read the chunk's content before resuming iteration.
        f.seek(pos + size + (size & 1))


def read_samples(path: Path, max_frames: int | None = None) -> np.ndarray:
    """Return the first channel as int16, whatever the source format.

    `max_frames` bounds the read: naming a file only requires listening to its
    beginning, and reading the 691 MB of an hour-long take to transcribe five
    minutes of it burns CPU for nothing.
    """
    fmt = data = None
    with Path(path).open("rb") as f:
        for cid, size, pos in _chunks(f):
            if cid == b"fmt ":
                f.seek(pos)
                fmt = f.read(size)
            elif cid == b"data":
                f.seek(pos)
                wanted = size
                if max_frames is not None and fmt is not None:
                    block = struct.unpack("<H", fmt[12:14])[0]
                    wanted = min(size, max_frames * block)
                data = f.read(wanted)
            if fmt is not None and data is not None:
                break
    if fmt is None or data is None:
        raise ValueError(f"{Path(path).name}: missing fmt or data chunk")

    tag, channels = struct.unpack("<HH", fmt[:4])
    bits = struct.unpack("<H", fmt[14:16])[0]
    if tag == WAVE_EXTENSIBLE and len(fmt) >= 26:
        # The real format is the start of the SubFormat GUID.
        tag = struct.unpack("<H", fmt[24:26])[0]

    samples = _decode(data, tag, bits)
    if channels > 1:
        samples = np.ascontiguousarray(samples[::channels])
    return samples


def _decode(data: bytes, tag: int, bits: int) -> np.ndarray:
    if tag == WAVE_FLOAT and bits == 32:
        floats = np.frombuffer(data, dtype="<f4")
        scaled = np.clip(floats * FULL_SCALE, -FULL_SCALE, FULL_SCALE - 1)
        return scaled.astype(np.int16)
    if tag == WAVE_FLOAT and bits == 64:
        floats = np.frombuffer(data, dtype="<f8")
        scaled = np.clip(floats * FULL_SCALE, -FULL_SCALE, FULL_SCALE - 1)
        return scaled.astype(np.int16)
    if tag == WAVE_PCM and bits == 16:
        return np.frombuffer(data, dtype="<i2")
    if tag == WAVE_PCM and bits == 8:
        # 8-bit PCM is unsigned, centred on 128.
        return ((np.frombuffer(data, dtype=np.uint8).astype(np.int16) - 128) << 8)
    if tag == WAVE_PCM and bits == 24:
        raw = np.frombuffer(data[: len(data) // 3 * 3], dtype=np.uint8).reshape(-1, 3)
        return (raw[:, 1].astype(np.int16) | (raw[:, 2].astype(np.int8).astype(np.int16) << 8))
    if tag == WAVE_PCM and bits == 32:
        return (np.frombuffer(data, dtype="<i4") >> 16).astype(np.int16)
    raise ValueError(f"unsupported WAV format: tag={tag} bits={bits}")
