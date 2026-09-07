"""Erasing a card: what must hold before the command is sent."""

from datetime import datetime
from pathlib import Path

from conteur.card import Take
from conteur.erase import FAILED, REENUMERATED, SUCCESS, EraseResult
from conteur.intake import CardVerdict, erase_card, inventory_of
from conteur.ledger import Ledger, Record, name_prefix

WHEN = datetime(2026, 9, 7, 11, 14, 32)


class FakeCard:
    serial = "800A-F63E"

    def __init__(self, takes):
        self._takes = takes

    def takes(self):
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
                       inventory=inventory_of(takes), complete=complete)


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
                             hasher=_hasher("aa" * 32)))
    assert [e.kind for e in events] == ["erasing", "erased"]
    assert sent == [Path("/dev/hidrawX")]


def test_an_incomplete_verdict_refuses_without_sending_anything(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    sent = []
    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32], complete=False),
                             Path("/dev/hidrawX"),
                             eraser=lambda node, **k: sent.append(node),
                             hasher=_hasher("aa" * 32)))
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
                             hasher=_hasher("aa" * 32)))
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
                             hasher=_hasher("aa" * 32)))
    assert [e.kind for e in events] == ["refused"]
    assert sent == []


def test_a_reenumeration_is_a_success(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: EraseResult(REENUMERATED, [0, 5, 10]),
        hasher=_hasher("aa" * 32)))
    assert [e.kind for e in events] == ["erasing", "erased"]


def test_a_failed_erase_is_reported_as_such(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(
        card, led, _verdict(takes, ["aa" * 32]), Path("/dev/hidrawX"),
        eraser=lambda node, **k: EraseResult(FAILED, []),
        hasher=_hasher("aa" * 32)))
    assert [e.kind for e in events] == ["erasing", "failed"]


def test_without_a_hid_node_it_refuses(tmp_path):
    takes = [_take()]
    card, led = FakeCard(takes), _held(tmp_path)
    events = list(erase_card(card, led, _verdict(takes, ["aa" * 32]), None,
                             eraser=lambda node, **k: None,
                             hasher=_hasher("aa" * 32)))
    assert [e.kind for e in events] == ["refused"]
