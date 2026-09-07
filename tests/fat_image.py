"""Build a minimal FAT32 image, so the reader can be tested without hardware.

Not mkfs.vfat: that would make the suite depend on an external binary, and we
could not choose the geometry. Here we can deliberately fragment a file, which
is the case the reader is most likely to get wrong and the one the device may
never produce on its own.
"""

import struct
from pathlib import Path

SECTOR = 512
SECTORS_PER_CLUSTER = 1
RESERVED = 32
NUM_FATS = 1
SECTORS_PER_FAT = 64
CLUSTER = SECTOR * SECTORS_PER_CLUSTER
DATA_START = (RESERVED + NUM_FATS * SECTORS_PER_FAT) * SECTOR
ROOT_CLUSTER = 2


def _boot(serial: int, label: bytes, total_clusters: int = 4096) -> bytes:
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x58\x90"
    b[3:11] = b"RODE    "
    b[11:13] = struct.pack("<H", SECTOR)
    b[13] = SECTORS_PER_CLUSTER
    b[14:16] = struct.pack("<H", RESERVED)
    b[16] = NUM_FATS
    b[21] = 0xF8                                    # BPB_Media
    b[24:26] = struct.pack("<H", 63)               # BPB_SecPerTrk
    b[26:28] = struct.pack("<H", 255)              # BPB_NumHeads
    b[32:36] = struct.pack("<I", RESERVED + NUM_FATS * SECTORS_PER_FAT
                                  + total_clusters * SECTORS_PER_CLUSTER)
    b[36:40] = struct.pack("<I", SECTORS_PER_FAT)
    b[44:48] = struct.pack("<I", ROOT_CLUSTER)
    b[66] = 0x29                                    # BS_BootSig
    b[67:71] = struct.pack("<I", serial)
    b[71:82] = label.ljust(11)[:11]
    b[82:87] = b"FAT32"
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def _short_name(name: str) -> bytes:
    stem, _, ext = name.rpartition(".")
    stem = (stem or name)[:8].upper().ljust(8)
    return (stem + ext[:3].upper().ljust(3)).encode("latin1")


def _lfn_entries(name: str, checksum: int) -> list[bytes]:
    padded = name.encode("utf-16-le") + b"\x00\x00" + b"\xff" * 64
    parts = [padded[i:i + 26] for i in range(0, len(name) * 2 + 2, 26)]
    out = []
    for i, part in enumerate(parts, 1):
        part = part.ljust(26, b"\xff")
        seq = i | (0x40 if i == len(parts) else 0)
        out.append(bytes([seq]) + part[0:10] + b"\x0f\x00" + bytes([checksum])
                   + part[10:22] + b"\x00\x00" + part[22:26])
    return list(reversed(out))


def _checksum(short: bytes) -> int:
    total = 0
    for c in short:
        total = (((total & 1) << 7) + (total >> 1) + c) & 0xFF
    return total


def build(path: Path, files: dict[str, bytes], serial: int = 0x800AF63E,
          fragment: bool = False) -> None:
    """Write a FAT32 image holding `files`. `fragment` interleaves the clusters.

    Interleaving matters: a reader that assumes contiguity passes every test on
    a freshly written card and fails on a real one.
    """
    total_clusters = 4096
    fat = [0x0FFFFFF8, 0x0FFFFFFF] + [0] * (total_clusters - 2)
    fat[ROOT_CLUSTER] = 0x0FFFFFFF
    data = bytearray(total_clusters * CLUSTER)

    chains: dict[str, list[int]] = {}
    next_free = ROOT_CLUSTER + 1
    counts = {n: max(1, (len(c) + CLUSTER - 1) // CLUSTER) for n, c in files.items()}
    if fragment:
        # round-robin: file A, file B, file A, file B …
        remaining = dict(counts)
        while any(remaining.values()):
            for name in files:
                if remaining[name]:
                    chains.setdefault(name, []).append(next_free)
                    next_free += 1
                    remaining[name] -= 1
    else:
        for name in files:
            chains[name] = list(range(next_free, next_free + counts[name]))
            next_free += counts[name]

    for name, content in files.items():
        for i, cluster in enumerate(chains[name]):
            piece = content[i * CLUSTER:(i + 1) * CLUSTER]
            start = (cluster - 2) * CLUSTER
            data[start:start + len(piece)] = piece
            nxt = chains[name][i + 1] if i + 1 < len(chains[name]) else 0x0FFFFFFF
            fat[cluster] = nxt

    entries = b""
    for name, content in files.items():
        short = _short_name(name)
        for lfn in _lfn_entries(name, _checksum(short)):
            entries += lfn
        first = chains[name][0]
        entries += (short + bytes([0x20]) + bytes(8)
                    + struct.pack("<H", first >> 16)     # high word of first cluster
                    + struct.pack("<H", 0x5AE7)          # write time 11:23:14
                    + struct.pack("<H", 0x5CE7)          # write date 2026-07-07
                    + struct.pack("<H", first & 0xFFFF)  # low word of first cluster
                    + struct.pack("<I", len(content)))
    root = (ROOT_CLUSTER - 2) * CLUSTER
    data[root:root + len(entries)] = entries

    with path.open("wb") as fh:
        fh.write(_boot(serial, b"WirelessPRO", total_clusters))
        fh.write(bytes(RESERVED * SECTOR - SECTOR))
        fh.write(b"".join(struct.pack("<I", e) for e in fat)
                 .ljust(SECTORS_PER_FAT * SECTOR, b"\x00"))
        fh.write(bytes(data))
