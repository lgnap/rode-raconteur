"""Erasing a card: what must hold before the command is sent."""

from datetime import datetime
from pathlib import Path

from conteur.card import Take
from conteur.erase import FAILED, REENUMERATED, REFUSED, SUCCESS, UNKNOWN, EraseResult
from conteur.intake import CardVerdict, erase_card, inventory_of
from conteur.ledger import Ledger, Record, name_prefix

WHEN = datetime(2026, 9, 7, 11, 14, 32)


class FakeCard:
    serial = "800A-F63E"

    def __init__(self, takes, open_takes=0, after_erase=None):
        self._takes = takes
        self.open_takes = open_takes
        # What a re-read finds once the card has been erased, so the
        # re-inventory can be exercised without a device.
        self._after_erase = after_erase
        self.erased = False
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def takes(self):
        if self.erased and self._after_erase is not None:
            return list(self._after_erase)
        return list(self._takes)


def _take(name="00001_S.WAV", size=3):
    return Take(name, size, 3, WHEN)


def _held(tmp_path, digest="aa" * 32):
    """A ledger holding one take whose file is on disk and matches."""
    path = tmp_path / "2026-09-07_111030_00001_S__sans-nom.wav"
    path.write_bytes(b"abc")
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    led.add(Record(digest=digest, card_name="00001_S.WAV", size=3,
                   folder=tmp_path, prefix=name_prefix(path), imported_at=WHEN))
    return led


def _verdict(takes, digests, complete=True):
    return CardVerdict(serial="800A-F63E", digests=frozenset(digests),
                       inventory=inventory_of(takes), complete=complete,
                       takes=len(takes), verified=len(takes))


def _lister(node=Path("/dev/hidrawX"), serial="800A-F63E"):
    """A stand-in for devices.default_hidraw_lister: HID_UNIQ -> node.

    Faked in every test here, so nothing in this file ever reads /sys or
    opens a hidraw node.
    """
    return lambda: {serial: node}


def _hasher(digest):
    return lambda path: digest


def test_a_complete_verdict_sends_the_command(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    sent = []

    def eraser(node, **kwargs):
        sent.append(node)
        return EraseResult(SUCCESS, list(range(0, 101, 5)))

    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]),
                             Path("/dev/hidrawX"), eraser=eraser,
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "erased", "reinventoried"]
    assert sent == [Path("/dev/hidrawX")]


def test_an_incomplete_verdict_refuses_without_sending_anything(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    sent = []
    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32], complete=False),
                             Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_take_appearing_since_the_import_refuses(tmp_path):
    """A transmitter can leave the case, record, and come back between the
    import and the click. The verdict would be stale and we would erase a take
    nobody had copied."""
    card = FakeCard([_take(), _take("00002_S.WAV")])
    led = _held(tmp_path)
    stale = _verdict([_take()], ["aa" * 32])
    sent = []
    events = list(erase_card(card, led, stale, Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_missing_local_file_refuses(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    for p in tmp_path.glob("*.wav"):
        p.unlink()
    sent = []
    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]),
                             Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_reenumeration_is_a_success(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: EraseResult(REENUMERATED, [0, 5, 10]),
        hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "erased", "reinventoried"]


def test_a_failed_erase_is_reported_as_such(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: EraseResult(FAILED, []),
        hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "failed"]


def test_a_refused_erase_is_reported_as_such(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: EraseResult(REFUSED, []),
        hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "failed"]
    assert events[-1].kind != "erased"


def test_an_unknown_outcome_is_reported_as_its_own_kind(tmp_path):
    """Silence means the command may well have gone through — telling the
    user it failed would be the wrong direction to be imprecise in."""
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: EraseResult(UNKNOWN, []),
        hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "unknown"]
    assert events[-1].kind != "erased"


def test_a_mismatched_serial_refuses_without_sending_anything(tmp_path):
    """The verdict must be about *this* card, not one paired with it by
    mistake — the pairing bug two docked transmitters would invite."""
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    other_verdict = CardVerdict(serial="OTHER-SERIAL",
                                digests=frozenset(["aa" * 32]),
                                inventory=inventory_of(takes), complete=True,
                                takes=1, verified=1)
    sent = []
    events = list(erase_card(card, led, other_verdict, Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_without_a_hid_node_it_refuses(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]), None,
                             eraser=lambda node, **k: None,
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]


def test_a_node_that_now_names_another_card_refuses(tmp_path):
    """hidraw minors are reused, and the node is resolved before the guards.

    The long part of erase_card is re-hashing every local copy — 80 s for
    30 GB — and a user can lift both transmitters out and re-dock them in the
    other order while it runs. The node written to must still be the one that
    announces this card's serial.
    """
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    sent = []
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: sent.append(node),
        hasher=_hasher("aa" * 32),
        # The card is still there, but under another node: /dev/hidrawX is
        # now the other transmitter.
        hidraw_lister=_lister(node=Path("/dev/hidrawY"))))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_node_no_longer_announcing_the_serial_refuses(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    sent = []
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: sent.append(node),
        hasher=_hasher("aa" * 32),
        hidraw_lister=lambda: {}))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_card_reporting_nothing_refuses(tmp_path):
    """Zero takes out of zero is not a proof: takes() is a filtered view, so
    "the import saw nothing" is not "there is nothing there"."""
    card, led = FakeCard([]), _held(tmp_path)
    empty = CardVerdict(serial="800A-F63E", digests=frozenset(),
                        inventory=(), complete=True, takes=0, verified=0)
    sent = []
    events = list(erase_card(card, led, empty, Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_recording_started_since_the_import_refuses(tmp_path):
    """An open take is filtered out of both inventories, so the freshness
    comparison cannot see it. Its clusters already hold audio."""
    takes = [_take()]
    card = FakeCard(takes, open_takes=1)
    led = _held(tmp_path)
    sent = []
    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]),
                             Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_success_is_verified_by_re_reading_the_card(tmp_path):
    """The card comes back readable immediately, so the erase is looked at
    rather than asserted."""
    takes = [_take()]
    card = FakeCard(takes, after_erase=[])
    led = _held(tmp_path)

    def eraser(node, **kwargs):
        card.erased = True
        return EraseResult(SUCCESS, list(range(0, 101, 5)))

    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]),
                             Path("/dev/hidrawX"), eraser=eraser,
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "erased", "reinventoried"]
    assert events[-1].detail == "0"
    # Read again, not served from the FAT cached before the erase.
    assert card.refreshed >= 2


def test_a_card_unreadable_after_the_erase_is_still_a_success(tmp_path):
    """A transmitter mid-reenumeration is the normal end of an erase."""
    takes = [_take()]
    led = _held(tmp_path)

    class Vanishing(FakeCard):
        def takes(self):
            if self.erased:
                raise OSError("no such device")
            return list(self._takes)

    card = Vanishing(takes)

    def eraser(node, **kwargs):
        card.erased = True
        return EraseResult(SUCCESS, list(range(0, 101, 5)))

    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]),
                             Path("/dev/hidrawX"), eraser=eraser,
                             hasher=_hasher("aa" * 32),
                             hidraw_lister=_lister()))
    assert [e.kind for e in events] == ["erasing", "erased", "reinventoried"]
    assert events[-1].detail is None
