"""The whole chain, as a generator: inventory, copy, record, split, submit.

A generator rather than a thread, deliberately. The thread lives in app.py and
does nothing but drain this; here the sequence can be unrolled synchronously in
a test, with no Qt, no thread and no hardware — which is how the ordering that
matters gets verified rather than hoped for.
"""

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from conteur.bwf import chunks, cue_points, started_at
from conteur.bwf import split as split_at_markers
from conteur.card import VerificationError, copy_verified
from conteur.ledger import Ledger, Record, name_prefix
from conteur.naming import SPLIT, UNNAMED
from conteur.paths import unique_path
from conteur.signal import CAPTURE_RATE


@dataclass(frozen=True)
class CardVerdict:
    """What an import concluded about one card.

    `digests` holds every take on the card — copied now or recognised as
    already held. `complete` is false as soon as one take produced none, so a
    card with a failure cannot unlock erasing without any special case.
    `inventory` is the cheap fingerprint the erase re-checks before firing.
    """
    serial: str
    digests: frozenset[str]
    inventory: tuple
    complete: bool


def inventory_of(takes) -> tuple:
    """A fingerprint of what the card held, to notice a change later.

    Names, sizes and closing times — not digests, which would mean reading
    the card again. Comparing names is wrong in general — a name is not an
    identity, since the take counter restarts at 00001 after an erase — but
    sound here, and only here: this fingerprint spans the window between an
    import and the erase it may unlock, a window of seconds during which no
    erase can have happened, so no name has had the chance to come round
    again over different audio. Do not generalise the comparison elsewhere.
    """
    return tuple(sorted((t.name, t.size, t.closed_at.isoformat()) for t in takes))


@dataclass(frozen=True)
class Event:
    kind: str
    take: str | None = None
    detail: str | None = None
    verdict: "CardVerdict | None" = None


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%d_%H%M%S")


def import_name(started: datetime, card_name: str,
                slug: str = UNNAMED) -> str:
    """`<start>_<name on the card>__<slug>.wav`.

    The start time sorts it with the takes recorded directly. The card name is
    kept because it will not be reproducible — the take counter restarts at
    00001 after an erase. The double underscore before the slug is the
    idempotence marker that tools/nommer-morceaux.py already relies on.
    """
    return f"{_stamp(started)}_{Path(card_name).stem}__{slug}.wav"


def part_name(started: datetime, card_name: str, index: int, total: int) -> str:
    """`<the part's own start>_<name on the card>_NN_sur_MM__<slug>.wav`.

    A part is a take like any other: it carries its own start time, which is
    the whole reason the splitter shifts each part's BWF timestamp. Without it
    the name answers none of the three questions the take's name answers —
    when, from where, what — and worse, two pieces of machinery go blind:
    `parse_timestamp` returns None, so the part is stamped with the import
    time instead of its own, and `is_orphan` is False, so a part left unnamed
    by a crash is invisible to recovery for ever.
    """
    return (f"{_stamp(started)}_{Path(card_name).stem}"
            f"_{index:02d}_sur_{total:02d}__{UNNAMED}.wav")


def _started(path: Path, take) -> datetime:
    """The take's start time, from `bext` or from close time and duration.

    A file whose chunks cannot be parsed — not a WAVE file at all — falls back
    to the take's close time, same as when `bext` itself is simply absent.
    """
    with path.open("rb") as fh:
        try:
            found = chunks(fh)
        except ValueError:
            return take.closed_at
        frames = 0
        rate = CAPTURE_RATE
        if "fmt " in found and "data" in found:
            # The file's own rate, not the capture rate: a take recorded at
            # anything other than 48 kHz would otherwise have its duration —
            # and so its start time, on the third of files with no bext —
            # scaled by the ratio between the two.
            fh.seek(found["fmt "][0] + 4)
            rate = struct.unpack("<I", fh.read(4))[0] or CAPTURE_RATE
            fh.seek(found["fmt "][0] + 12)
            block_align = struct.unpack("<H", fh.read(2))[0] or 4
            frames = found["data"][1] // block_align
        return started_at(fh, closed_at=take.closed_at, rate=rate,
                          frames=frames)


def _marked(path: Path) -> bool:
    """Whether the take carries markers, read before it is named.

    Read here rather than after splitting so the name is settled before the
    ledger records the path — the ledger must never point at a name we are
    about to change. A file we cannot parse simply has no markers; the split
    that follows will report the real reason.
    """
    try:
        with path.open("rb") as fh:
            return bool(cue_points(fh))
    except (OSError, ValueError, struct.error):
        return False


def intake(card, ledger: Ledger, dest_for: Callable[[datetime], Path], submit,
           copier=copy_verified, splitter=split_at_markers,
           stop=None) -> Iterator[Event]:
    """Import one card, yielding an event at every step.

    `dest_for` maps a recording's start time to the folder it belongs in — a
    take is filed by the month it was recorded, not by the month it happens to
    be imported. The provisional copy is written under `dest_for(take.closed_at)`
    because the true start time can only be read once the file exists; once it
    is known, the file is moved to `dest_for(started)` under its final name.
    When both resolve to the same folder — the common case — that move is a
    plain rename.

    Two orders are not negotiable. Recording comes *after* verification: a
    ledger noting an unverified copy is an erase lock that opens on nothing.
    And splitting comes *after* recording: it destroys comparability, since a
    split take no longer exists in the form that was hashed.

    A failure on one take does not stop the others — the source is read-only,
    so a failed import costs time and never data.
    """
    takes = card.takes()
    yield Event("inventory", detail=str(len(takes)))
    # Card name -> digest, for every take this import could account for —
    # copied now or already held. This is not the identity lookup the
    # ledger itself refuses to build from names: it is scoped to this one
    # card, read moments ago, and it exists only to size up the verdict
    # below. A card cannot be erased unless it has just been read like this.
    seen: dict[str, str] = {}

    for take in takes:
        if stop is not None and stop.is_set():
            break

        # Every take is copied before it is judged, even one whose name the
        # ledger already holds. There is no shortcut: a digest cannot be known
        # without reading, and the name is not an identity — the counter
        # restarts at 00001 after an erase, so the card comes back full of
        # names already recorded, holding different takes. Skipping on the
        # name would discard them all in silence and open the erase lock on
        # their stale twins. Re-reading the card costs seconds.
        yield Event("copy", take=take.name)
        provisional_dir = dest_for(take.closed_at)
        provisional_dir.mkdir(parents=True, exist_ok=True)
        provisional = unique_path(provisional_dir, f"{take.name}.incoming")
        try:
            digest = copier(card, take, provisional)
        except (VerificationError, OSError) as error:
            yield Event("failed", take=take.name, detail=str(error))
            continue
        if ledger.has(digest):
            provisional.unlink(missing_ok=True)
            seen[take.name] = digest
            yield Event("skipped", take=take.name)
            continue
        yield Event("verified", take=take.name)

        # Everything after the copy is guarded too, and broadly: _started
        # raises struct.error on a malformed header, the rename raises OSError
        # across filesystems, and dest_for is the caller's code. Left
        # unguarded, the first bad take ended the whole card — while the
        # promise above is that a failure on one take does not stop the
        # others. The ledger write stays outside on purpose: if the proof
        # cannot be written we must stop, or we copy without recording and
        # recopy for ever.
        try:
            started = _started(provisional, take)
            marked = _marked(provisional)
            final_dir = dest_for(started)
            final_dir.mkdir(parents=True, exist_ok=True)
            final = unique_path(
                final_dir, import_name(started, take.name,
                                       SPLIT if marked else UNNAMED))
            provisional.rename(final)
        except (OSError, ValueError, struct.error) as error:
            yield Event("failed", take=take.name, detail=str(error))
            continue

        ledger.add(Record(digest=digest, card_name=take.name, size=take.size,
                          folder=final.parent, prefix=name_prefix(final),
                          imported_at=datetime.now()))
        seen[take.name] = digest
        yield Event("recorded", take=take.name, detail=str(final))

        # The parts land in the take's own folder, not in a subfolder of their
        # own, each under its own start time — the take's start plus the
        # part's offset in samples. Everything downstream reads that name:
        # `parse_timestamp` to date it, `is_orphan` to find it again after a
        # crash, `destination_dir` to file it by month.
        def name_part(index, total, offset, rate, when=started, take=take):
            start = when + timedelta(seconds=offset / rate) if rate else when
            return unique_path(
                final.parent, part_name(start, take.name, index, total)).name

        # A take that cannot be split is still a take that was copied,
        # verified and recorded: it is reported and then submitted whole,
        # rather than costing the rest of the card.
        try:
            parts = splitter(final, final.parent, name_for=name_part)
        except (OSError, ValueError, struct.error) as error:
            parts = []
            yield Event("failed", take=take.name, detail=str(error))
        if parts:
            yield Event("split", take=take.name, detail=str(len(parts)))
            for part in parts:
                submit(part)
                yield Event("submitted", take=part.name)
        else:
            # Only a take that was NOT split is submitted. Naming a take and
            # then its own parts transcribes the same audio twice: measured on
            # real material, a 242 s take cut into six cost 484 s of a queue
            # that runs one job at a time because two models will not fit in
            # 8 GB of VRAM. A split take is named `decoupee` instead — it
            # keeps a name, so orphan recovery leaves it alone, without
            # anyone having to listen to it. A take whose split FAILED lands
            # here too, and is submitted: it was never cut, so it deserves a
            # real name.
            submit(final)
            yield Event("submitted", take=final.name)

    # complete compares counts of *takes*, not of digests: two takes holding
    # identical bytes share one digest, and both are held — the card is
    # still fully accounted for.
    yield Event("verdict", verdict=CardVerdict(
        serial=getattr(card, "serial", ""),
        digests=frozenset(seen.values()),
        inventory=inventory_of(takes),
        complete=len(seen) == len(takes),
    ))
    yield Event("done")
