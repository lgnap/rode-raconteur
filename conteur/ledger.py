"""What has been imported from a card, and whether that card may be erased.

This is the only thing that authorises an erase, so it is the spine of the
import rather than a log kept on the side. It is written from one place only:
if two places could write it, we would have built a way to erase a card on a
half-written proof — exactly what the lock exists to prevent.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from conteur.card import Take, sha256_file


@dataclass(frozen=True)
class Record:
    digest: str
    card_name: str
    size: int
    path: Path
    imported_at: datetime


def ledger_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "conteur" / "imports"


class Ledger:
    """One file per card, named by the volume serial.

    Named by the serial and not by a hidraw number or a /dev/sdX: both change
    across a replug, the serial does not.
    """

    def __init__(self, serial: str, root: Path | None = None):
        self.serial = serial
        self.root = root if root is not None else ledger_root()
        self.path = self.root / f"{serial}.json"
        self._records = self._load()

    def _load(self) -> list[Record]:
        # An unreadable ledger behaves as an empty one, which keeps the erase
        # lock closed. The safe failure is the natural one; no guard needed.
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw.get("takes", []):
            try:
                out.append(Record(
                    digest=item["digest"],
                    card_name=item["card_name"],
                    size=int(item["size"]),
                    path=Path(item["path"]),
                    imported_at=datetime.fromisoformat(item["imported_at"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def records(self) -> list[Record]:
        return list(self._records)

    def has(self, digest: str) -> bool:
        """Identity is the digest, never the name.

        The take counter restarts at 00001 after an erase, so a name will come
        round again holding something else.
        """
        return any(r.digest == digest for r in self._records)

    def add(self, record: Record) -> None:
        self._records.append(record)
        self._write()

    def _write(self) -> None:
        # Atomic: a crash mid-write would otherwise take the proof of
        # everything recorded before it.
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "serial": self.serial,
            "takes": [
                {
                    "digest": r.digest,
                    "card_name": r.card_name,
                    "size": r.size,
                    "path": str(r.path),
                    "imported_at": r.imported_at.isoformat(),
                }
                for r in self._records
            ],
        }
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        temp.replace(self.path)

    def erase_allowed(self, takes: list[Take], digests: dict[str, str],
                      hasher=sha256_file) -> bool:
        """True only when every take on the card has a verified twin on disk.

        `digests` maps a card file name to the digest computed when it was
        imported, so an unimported take has no entry and closes the lock.

        The destination files are re-hashed rather than taken on trust: a file
        you moved or deleted closes the lock again. Re-reading the device
        instead would cost twelve minutes against eighty seconds locally, so
        the rigorous version is nearly free.
        """
        by_digest = {r.digest: r for r in self._records}
        for take in takes:
            digest = digests.get(take.name)
            if digest is None or digest not in by_digest:
                return False
            record = by_digest[digest]
            if not record.path.exists():
                return False
            if hasher(record.path) != digest:
                return False
        return True
