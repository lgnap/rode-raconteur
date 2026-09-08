"""Read-only FAT32, on the raw block device.

The card is read-only at device level — the kernel logs `Write Protect is on`
and /sys/block/sdX/ro reads 1 — so there is nothing to gain from mounting it.
Reading /dev/sdX directly also means the volume serial, our join key with the
transmitter's HID_UNIQ, is available without waiting for udev to re-probe,
which it has not done immediately after an erase.

We need exactly three things from a card: its serial, the list of takes, and
the bytes of one take. Everything else FAT can do is irrelevant here, and
nothing writes.
"""

import hashlib
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ATTR_LFN = 0x0F
ATTR_VOLUME = 0x08
ATTR_DIRECTORY = 0x10
ENTRY_FREE = 0xE5
ENTRY_END = 0x00
END_OF_CHAIN = 0x0FFFFFF8

# A copy in flight is written under this suffix and only renamed once its
# digest is confirmed. An interrupted file is then recognisable at a glance,
# never mistaken for a valid take, and cannot reach the ledger — recording
# happens after the rename.
PART_SUFFIX = ".part"


class VerificationError(Exception):
    """The copy on disk does not match what was read from the card."""


@dataclass(frozen=True)
class Take:
    name: str
    size: int
    cluster: int
    closed_at: datetime


def _fat_datetime(date: int, time_: int) -> datetime:
    year = 1980 + (date >> 9)
    month = max(1, (date >> 5) & 0x0F)
    day = max(1, date & 0x1F)
    hour = min(23, time_ >> 11)
    minute = min(59, (time_ >> 5) & 0x3F)
    second = min(59, (time_ & 0x1F) * 2)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return datetime(1980, 1, 1)


class Card:
    """A FAT32 volume. Takes an open binary file object, never a path.

    Passing the file object rather than a path is what lets the tests run
    against an image built in tmp_path with no device present.
    """

    def __init__(self, fh):
        self.fh = fh
        boot = self._at(0, 512)
        if boot[82:87] != b"FAT32":
            raise ValueError("not FAT32")
        self.bytes_per_sector = struct.unpack("<H", boot[11:13])[0]
        self.sectors_per_cluster = boot[13]
        reserved = struct.unpack("<H", boot[14:16])[0]
        num_fats = boot[16]
        self.sectors_per_fat = struct.unpack("<I", boot[36:40])[0]
        self.root_cluster = struct.unpack("<I", boot[44:48])[0]
        self.volume_id = struct.unpack("<I", boot[67:71])[0]
        self.label = boot[71:82].decode("latin1").strip()
        self.oem = boot[3:11].decode("latin1").strip()
        self.cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        self.fat_start = reserved * self.bytes_per_sector
        self.data_start = ((reserved + num_fats * self.sectors_per_fat)
                           * self.bytes_per_sector)
        self._fat: bytes | None = None
        # How many zero-byte entries the last takes() dropped. Read it after
        # takes(), never before: nothing has been read from the card yet.
        self.open_takes = 0

    def refresh(self) -> None:
        """Forget the cached FAT, so the next read sees the card as it is now.

        The FAT is cached because reading it costs 3.6 MB, which is right for
        the length of one import. It is wrong across an erase, and wrong for
        the freshness check that asks whether the card changed since the
        import: both need the table on the card, not the one from before.

        Our own cache is only half of it. The reads that follow go back
        through the same descriptor, and the kernel answers them from the
        pages it cached for this block device. An erase travels over HID, so
        nothing on the block side tells the kernel those pages are now a
        description of a card that no longer exists — and the re-inventory
        comes back with the card as it was, which is the one answer this
        method exists to prevent. POSIX_FADV_DONTNEED drops them; they are
        read-only and therefore clean, so the kernel has nothing to write back
        and nothing to refuse.
        """
        self._fat = None
        os.posix_fadvise(self.fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)

    @property
    def serial(self) -> str:
        return f"{self.volume_id >> 16:04X}-{self.volume_id & 0xFFFF:04X}"

    def _at(self, offset: int, size: int) -> bytes:
        self.fh.seek(offset)
        return self.fh.read(size)

    def _fat_table(self) -> bytes:
        # The whole FAT at once: 3.6 MB for a 28.9 GB card, against one seek
        # per cluster if the chain were walked read by read.
        if self._fat is None:
            self._fat = self._at(self.fat_start,
                                 self.sectors_per_fat * self.bytes_per_sector)
        return self._fat

    def _chain(self, start: int) -> list[int]:
        fat = self._fat_table()
        cluster, out = start, []
        while 2 <= cluster < END_OF_CHAIN and cluster * 4 + 4 <= len(fat):
            out.append(cluster)
            cluster = struct.unpack("<I", fat[cluster * 4:cluster * 4 + 4])[0] & 0x0FFFFFFF
        return out

    def _cluster_offset(self, cluster: int) -> int:
        return self.data_start + (cluster - 2) * self.cluster_size

    def takes(self) -> list[Take]:
        """The takes on the card, in directory order.

        This is a filtered view, not the card's contents, and the difference
        decides whether the card may be erased:

        Zero-byte entries are dropped: a transmitter starts recording the
        instant it leaves the charging case, so a device you have just
        connected always shows one open take with a size of zero. It is not an
        empty file, it is a recording in progress — its clusters already hold
        audio, which the transmitter finalises when it is docked again. Such
        an entry is therefore counted in `open_takes`, so a caller can tell
        "I saw no takes" from "there are no takes": a card mid-recording holds
        audio that no import can have copied, and must never be erased.

        Directory entries are skipped and deliberately not counted: a FAT
        volume that has been mounted read-write anywhere carries a `System
        Volume Information` folder, so treating a directory as hidden audio
        would lock erasing for ever on cards that are perfectly accounted
        for. The recorder itself writes takes flat at the root.
        """
        raw = b"".join(self._at(self._cluster_offset(c), self.cluster_size)
                       for c in self._chain(self.root_cluster))
        found: list[Take] = []
        self.open_takes = 0
        long_parts: list[tuple[int, str]] = []
        for i in range(0, len(raw), 32):
            entry = raw[i:i + 32]
            if len(entry) < 32 or entry[0] == ENTRY_END:
                break
            if entry[0] == ENTRY_FREE:
                long_parts = []
                continue
            attrs = entry[11]
            if attrs == ATTR_LFN:
                chars = entry[1:11] + entry[14:26] + entry[28:32]
                long_parts.append((entry[0] & 0x3F,
                                   chars.decode("utf-16-le", "ignore")))
                continue
            if attrs & (ATTR_VOLUME | ATTR_DIRECTORY):
                long_parts = []
                continue
            if long_parts:
                name = "".join(p for _, p in sorted(long_parts))
                name = name.split("\x00")[0].rstrip("￿")
            else:
                stem = entry[:8].decode("latin1").strip()
                ext = entry[8:11].decode("latin1").strip()
                name = f"{stem}.{ext}" if ext else stem
            long_parts = []
            size = struct.unpack("<I", entry[28:32])[0]
            if size == 0:
                self.open_takes += 1
                continue
            cluster = ((struct.unpack("<H", entry[20:22])[0] << 16)
                       | struct.unpack("<H", entry[26:28])[0])
            time_, date = struct.unpack("<HH", entry[22:26])
            found.append(Take(name, size, cluster, _fat_datetime(date, time_)))
        return found

    def stream(self, take: Take, chunk: int = 1 << 20) -> Iterator[bytes]:
        """Yield the take's bytes. Contiguous clusters are read in one go."""
        left = take.size
        run_start = run_len = None
        for cluster in [*self._chain(take.cluster), None]:
            if run_start is not None and cluster == run_start + run_len:
                run_len += 1
                continue
            if run_start is not None:
                self.fh.seek(self._cluster_offset(run_start))
                remaining = run_len * self.cluster_size
                while remaining > 0 and left > 0:
                    block = self.fh.read(min(chunk, remaining, left))
                    if not block:
                        return
                    left -= len(block)
                    remaining -= len(block)
                    yield block
            run_start, run_len = cluster, 1


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def copy_verified(card: Card, take: Take, dest: Path, attempts: int = 2,
                  verifier=sha256_file) -> str:
    """Copy one take and prove it arrived. Returns its SHA-256.

    The stream is hashed as it is written, so the device is read once. The
    written file is then re-read locally to confirm it — which costs about
    1.3 % of the copy, because the local disk is ten times faster than the
    device. There is no trade-off worth making here.

    The length is checked as well as the hash, and it is not a formality: the
    hash only proves the disk holds what was read, never that the read reached
    the end of the take. `stream()` stops quietly when the cluster chain runs
    out or a read comes back empty, so a truncated take would otherwise hash
    consistently, be renamed out of `.part`, be recorded, and unlock the erase
    on a tenth of a recording. A short chain truncates every take at once.

    One retry: a passing USB error is plausible, two in a row are not. A short
    read is retried like any other failure, since a truncated read can be
    transient too.
    """
    part = dest.with_name(dest.name + PART_SUFFIX)
    last: str | None = None
    for attempt in range(attempts):
        digest = hashlib.sha256()
        written = 0
        with part.open("wb") as out:
            for block in card.stream(take):
                digest.update(block)
                written += len(block)
                out.write(block)
        streamed = digest.hexdigest()
        if written != take.size:
            last = f"{written} bytes read of {take.size}"
            continue
        if verifier(part) == streamed:
            part.rename(dest)
            return streamed
        last = f"digest {streamed}"
    raise VerificationError(
        f"{take.name}: the copy does not match what was read ({last})")
