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
from conteur.card import VerificationError, copy_verified, sha256_file
from conteur.devices import default_hidraw_lister
from conteur.erase import FAILED, REENUMERATED, REFUSED, SUCCESS, UNKNOWN
from conteur.erase import erase as erase_over_hid
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

    `takes` and `verified` are counts, not lengths of the two sets above, and
    neither can be derived from them: two takes holding identical bytes share
    one digest, and `takes` counts the open take the card may be writing right
    now, which has no digest and never will until it is finalised. They exist
    so the window can say *how many* takes are still missing — the only
    question the locked row is there to answer.
    """
    serial: str
    digests: frozenset[str]
    inventory: tuple
    complete: bool
    takes: int
    verified: int


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


def _hid_node_for(serial: str, lister) -> Path | None:
    """The node currently announcing this serial as its HID_UNIQ, or None.

    Both sides are folded the same way find_storage folds them: the volume
    serial comes out of the boot sector uppercased with a dash, HID_UNIQ is
    whatever the firmware wrote.
    """
    key = serial.replace("-", "").upper()
    try:
        nodes = lister()
    except OSError:
        return None
    for uniq, node in nodes.items():
        if uniq.replace("-", "").upper() == key:
            return node
    return None


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
    # Read straight after takes(), which is what sets it: a zero-byte entry
    # is an open recording, not an empty file, so the card is mid-take and
    # cannot be fully copied by definition.
    open_takes = getattr(card, "open_takes", 0)
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
    #
    # An open take counts as a take that was not copied. takes() cannot copy
    # it — it has no size and its clusters are still being written — so the
    # card holds audio this import does not, and the lock has to stay shut.
    total = len(takes) + open_takes
    yield Event("verdict", verdict=CardVerdict(
        serial=getattr(card, "serial", ""),
        digests=frozenset(seen.values()),
        inventory=inventory_of(takes),
        complete=len(seen) == total,
        takes=total,
        verified=len(seen),
    ))
    yield Event("done")


# French wording for the terminal outcomes erase() can report, other than
# SUCCESS/REENUMERATED (a success) and UNKNOWN (its own outcome below). Kept
# out of the f-string so the window never shows a raw internal token such as
# "refused" spliced into otherwise-French text.
_FAILURE_MESSAGES = {
    FAILED: "l'effacement a échoué",
    REFUSED: "la carte a refusé la commande d'effacement",
}


def _fresh_takes(card):
    """The card's takes, read again rather than from anything cached.

    The FAT read during the import describes the card as it was then; this
    call exists precisely to find out whether it still is.
    """
    refresh = getattr(card, "refresh", None)
    if refresh is not None:
        refresh()
    return card.takes()


def _note_erasure(ledger: Ledger, outcome: str, takes: int,
                  remaining: int | None) -> Iterator[Event]:
    """Write the attempt down, and never let that failing look like the card
    was spared.

    The command has already gone through by the time this runs. A card is
    erased whatever the disk has to say about it, so an unwritable ledger is
    reported as its own thing rather than folded into the erase's outcome:
    told the erase failed, a user runs it again, which is the one direction
    it is dangerous to be imprecise in.

    Only the two ends that leave the card's state in doubt are recorded —
    erased, and undetermined. A refusal sends nothing, and a transmitter that
    answers with a failure has kept everything: neither needs accounting for.
    """
    try:
        ledger.note_erasure(outcome=outcome, takes=takes, remaining=remaining)
    except OSError as error:
        yield Event("unlogged", detail=f"effacement non consigné : {error}")


def erase_card(card, ledger: Ledger, verdict: CardVerdict, node,
               eraser=erase_over_hid, hasher=sha256_file,
               hidraw_lister=default_hidraw_lister) -> Iterator[Event]:
    """Erase one card, and only if it is still safe to.

    Four things must hold, and each is checked here rather than trusted: the
    verdict must be about *this* card, not some other one paired with it by
    mistake; the import must have accounted for every take; the ledger must
    still find an intact local twin for every digest; and the card must hold
    exactly what the import saw. A transmitter can leave the case, record,
    and come back between the import and the click, which would make the
    verdict stale.

    The serial check comes first and matters most once two transmitters can
    be docked at once: nothing else here reads the card's own serial, so a
    caller that mismatches verdict and card — or verdict and HID node — would
    otherwise sail through every other guard undetected.

    Re-reading the directory is cheap and sound here only: no erase has
    happened in between, so no name has been recycled.

    A verdict holding no digests is refused rather than treated as a card
    with nothing to lose. `takes()` reports a filtered view, not the card's
    contents: an open take — a recording in progress, its clusters already
    holding audio — is dropped from it, as is anything in a subdirectory. So
    "the import saw nothing" and "there is nothing there" are different
    statements, and only the second would make erasing harmless. A proof of
    zero takes out of zero is not a proof, and every guard below passes
    vacuously on it.

    The HID node is re-identified last, after the guards. It has to be: the
    long part of this function is `erase_allowed` re-hashing every local
    copy — 80 s for 30 GB — and hidraw minor numbers are reused, so a user
    who lifts both transmitters out and re-docks them in the other order
    during that wait would have the command land on the node that is now the
    *other* card. The block side has always read the serial from the device
    it actually opened; this is the same check on the side that gets written
    to.
    """
    if verdict.serial != getattr(card, "serial", None):
        yield Event("refused",
                    detail="le verdict ne correspond pas à cette carte")
        return
    if node is None:
        yield Event("refused", detail="aucun nœud HID pour cet appareil")
        return
    if not verdict.complete:
        yield Event("refused", detail="toutes les prises n'ont pas été copiées")
        return
    if not verdict.digests:
        yield Event("refused",
                    detail="aucune prise vérifiée sur cette carte")
        return
    fresh = _fresh_takes(card)
    if inventory_of(fresh) != verdict.inventory:
        yield Event("refused",
                    detail="la carte a changé depuis la récupération")
        return
    if getattr(card, "open_takes", 0):
        # Not covered by the inventory comparison: an open take is filtered
        # out of both sides of it, so a card that started recording since the
        # import compares equal while holding audio nobody has copied.
        yield Event("refused",
                    detail="un enregistrement est en cours sur cette carte")
        return
    if not ledger.erase_allowed(verdict.digests, hasher=hasher):
        yield Event("refused", detail="une copie manque ou a changé")
        return

    # Last, and deliberately after the re-hashing above: minutes may have
    # passed since the node was resolved, and a re-dock reuses hidraw minors.
    if _hid_node_for(verdict.serial, hidraw_lister) != node:
        yield Event("refused",
                    detail="le nœud HID ne correspond plus à cette carte")
        return

    yield Event("erasing", detail=verdict.serial)
    result = eraser(node)
    if result.verdict in (SUCCESS, REENUMERATED):
        yield Event("erased", detail=verdict.serial)
        # Assert nothing: look, and report what the look finds. On hardware it
        # finds nothing — the transmitter is re-enumerating, the read fails,
        # and `remaining` is None after every real erase (measured on two
        # cards, 2026-09-08). This said the opposite until then, on the
        # evidence of a re-read that came back with a count: the card does not
        # come back readable straight away, it only looked that way because
        # the kernel still held the pages from before. The erase travels over
        # HID and invalidates nothing on the block side, so what was read back
        # was the card as it was — the one answer the re-read exists to avoid.
        # A card that cannot be re-read is not a failure: mid-reenumeration is
        # the normal end of an erase, so the count is reported as unknown.
        try:
            remaining = len(_fresh_takes(card))
        except (OSError, ValueError, struct.error):
            remaining = None
        yield from _note_erasure(ledger, "erased", verdict.takes, remaining)
        yield Event("reinventoried",
                    detail=None if remaining is None else str(remaining))
    elif result.verdict == UNKNOWN:
        yield from _note_erasure(ledger, "unknown", verdict.takes, None)
        # Silence from the transmitter is not a failure: the command may well
        # have gone through, and conteur.erase.erase's own contract is that
        # the honest answer here is "re-read the card", not "it failed" —
        # telling the user it failed when nobody knows is the wrong direction
        # to be imprecise in, since they might re-run it or trust it is safe.
        yield Event("unknown",
                    detail=(f"{verdict.serial} : état indéterminé, "
                            "il faut relire la carte"))
    else:
        reason = _FAILURE_MESSAGES.get(result.verdict, result.verdict)
        yield Event("failed", detail=f"{verdict.serial} : {reason}")
