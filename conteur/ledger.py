"""What has been imported from a card, and whether that card may be erased.

This is the only thing that authorises an erase, so it is the spine of the
import rather than a log kept on the side. The half that authorises — `takes`
— is written from one place only: if two places could write it, we would have
built a way to erase a card on a half-written proof, exactly what the lock
exists to prevent.

The file also carries `erasures`, an append-only annexe recording what was
done to the card. It has a second writer, and may: nothing reads it back.
`_load` never lets it reach `_records`, and neither `has` nor `erase_allowed`
consults it, so no entry here can open the lock. It exists because an erase
is the one irreversible act of the app and, until it was written down, the
only proof it had happened was a line of status text in a window since closed.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from conteur.card import sha256_file


@dataclass(frozen=True)
class Record:
    digest: str
    card_name: str
    size: int
    folder: Path
    prefix: str
    imported_at: datetime


@dataclass(frozen=True)
class Erasure:
    """One attempt at erasing this card, and what came of it.

    `takes` is what the verdict accounted for; `remaining` is what re-reading
    the card found afterwards. `remaining` is None whenever nobody looked: an
    outcome of "unknown" — the transmitter said nothing and the state is
    undetermined — or a card that could not be re-read, which is the normal
    end of an erase rather than a failure.

    In practice it is always None: a transmitter that has just been erased is
    re-enumerating, and the read fails. Expect the pair "erased, remaining
    unknown", and read a count here as the exception. The field earns its keep
    by not being a number that was never measured — an earlier version reported
    one, and it was the card as it stood before the erase.
    """

    at: datetime
    outcome: str
    takes: int
    remaining: int | None


def name_prefix(path: Path) -> str:
    """Everything before the last `__` — the part of a name that never changes.

    Naming replaces only the slug after the last `__`, and so does renaming by
    hand (`job._target_name`), so this survives every path a file's name can
    take. Storing it rather than the path is what lets a record find its file
    again after it has been named: a stored path points at nothing the moment
    the take stops being called `sans-nom`.
    """
    stem = path.stem
    return stem.rsplit("__", 1)[0] if "__" in stem else stem


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
        self._records, self._erasures = self._load()

    def _load(self) -> tuple[list[Record], list[Erasure]]:
        # An unreadable ledger behaves as an empty one, which keeps the erase
        # lock closed. The safe failure is the natural one; no guard needed.
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            out = []
            for item in raw.get("takes", []):
                try:
                    folder = item.get("folder")
                    prefix = item.get("prefix")
                    if folder is None or prefix is None:
                        # Written before the prefix existed. Dropping these
                        # would silently close the lock on every card
                        # imported earlier.
                        old = Path(item["path"])
                        folder, prefix = str(old.parent), name_prefix(old)
                    out.append(Record(
                        digest=item["digest"],
                        card_name=item["card_name"],
                        size=int(item["size"]),
                        folder=Path(folder),
                        prefix=prefix,
                        imported_at=datetime.fromisoformat(item["imported_at"]),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            return out, self._load_erasures(raw)
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            return [], []

    @staticmethod
    def _load_erasures(raw) -> list[Erasure]:
        """One bad entry costs itself, never the file.

        A ledger written before this annexe existed simply has none, and every
        card imported so far has such a file: reading it must keep working, or
        the proofs it holds would go down with it.
        """
        out = []
        for item in raw.get("erasures", []):
            try:
                remaining = item["remaining"]
                out.append(Erasure(
                    at=datetime.fromisoformat(item["at"]),
                    outcome=item["outcome"],
                    takes=int(item["takes"]),
                    remaining=None if remaining is None else int(remaining),
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

    def erasures(self) -> list[Erasure]:
        return list(self._erasures)

    def note_erasure(self, outcome: str, takes: int,
                     remaining: int | None = None,
                     at: datetime | None = None) -> None:
        """Record that the card was erased, or that nobody can say it wasn't.

        Append-only, and deliberately not conditional on the outcome: an
        attempt that went unanswered is the one a user will fail to recall,
        and leaving it out would make silence mean both "never tried" and
        "tried, no answer".
        """
        self._erasures.append(Erasure(at=at or datetime.now(),
                                      outcome=outcome, takes=takes,
                                      remaining=remaining))
        self._write()

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
                    "folder": str(r.folder),
                    "prefix": r.prefix,
                    "imported_at": r.imported_at.isoformat(),
                }
                for r in self._records
            ],
            "erasures": [
                {
                    "at": e.at.isoformat(),
                    "outcome": e.outcome,
                    "takes": e.takes,
                    "remaining": e.remaining,
                }
                for e in self._erasures
            ],
        }
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        temp.replace(self.path)

    def locate(self, record: Record) -> Path | None:
        """The file this record stands for, or None.

        Exactly one candidate, or none: two files sharing a prefix means we
        cannot say which one the record is about, and this decides what may
        be destroyed. A guess is not acceptable here.
        """
        try:
            found = sorted(record.folder.glob(f"{record.prefix}__*.wav"))
        except OSError:
            return None
        return found[0] if len(found) == 1 else None

    def erase_allowed(self, digests: Iterable[str], hasher=sha256_file) -> bool:
        """True only when every digest is held and its file is still intact.

        The caller passes the digests of **every** take on the card, computed
        by reading it. There is deliberately no name-keyed map: the take
        counter restarts at 00001 after an erase, so a name is not an
        identity and a map built from the ledger's own names would authorise
        erasing a card whose takes were never copied.

        Files are re-hashed rather than trusted: one you moved or replaced
        closes the lock again.
        """
        held = {r.digest: r for r in self._records}
        for digest in digests:
            record = held.get(digest)
            if record is None:
                return False
            found = self.locate(record)
            if found is None or hasher(found) != digest:
                return False
        return True
