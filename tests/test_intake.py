"""The whole chain, unrolled synchronously. No Qt, no thread, no hardware."""

from datetime import datetime
from pathlib import Path

from conteur.card import Take, VerificationError
from conteur.intake import Event, import_name, intake
from conteur.ledger import Ledger

WHEN = datetime(2026, 9, 7, 11, 14, 32)


class FakeCard:
    serial = "800A-F63E"

    def __init__(self, takes, content=b"abc"):
        self._takes = takes
        self.content = content

    def takes(self):
        return list(self._takes)

    def stream(self, take, chunk=1 << 20):
        yield self.content


def _take(name, size=3):
    return Take(name, size, 3, WHEN)


def test_the_name_carries_start_card_and_slug():
    assert import_name(datetime(2026, 9, 7, 11, 10, 30),
                       "00002_Source-Baffle.WAV") == \
        "2026-09-07_111030_00002_Source-Baffle__sans-nom.wav"


def test_the_sequence_of_events_is_the_designed_order(tmp_path):
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    submitted = []
    events = list(intake(card, led, lambda when: tmp_path / "out",
                         submitted.append, splitter=lambda path, out: []))
    assert [e.kind for e in events] == [
        "inventory", "copy", "verified", "recorded", "submitted", "done",
    ]
    assert len(submitted) == 1


def test_a_take_already_recorded_is_skipped(tmp_path):
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    dest_for = lambda when: tmp_path / "out"
    list(intake(card, led, dest_for, lambda p: None,
                splitter=lambda path, out: []))
    events = list(intake(card, led, dest_for, lambda p: None,
                         splitter=lambda path, out: []))
    assert [e.kind for e in events] == ["inventory", "skipped", "done"]


def test_a_failed_verification_stops_before_recording(tmp_path):
    """The order that is not negotiable: a ledger noting an unverified copy is
    a lock that opens on nothing."""
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")

    def refuse(*args, **kwargs):
        raise VerificationError("mismatch")

    events = list(intake(card, led, lambda when: tmp_path / "out",
                         lambda p: None, copier=refuse,
                         splitter=lambda path, out: []))
    kinds = [e.kind for e in events]
    assert kinds == ["inventory", "copy", "failed", "done"]
    assert "recorded" not in kinds
    assert "split" not in kinds
    assert led.records() == []


def test_a_failure_does_not_stop_the_following_takes(tmp_path):
    card = FakeCard([_take("00001_Source.WAV"), _take("00002_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    calls = []

    def flaky(card_, take, dest, **kwargs):
        calls.append(take.name)
        if take.name.startswith("00001"):
            raise VerificationError("mismatch")
        dest.write_bytes(b"abc")
        return "cc" * 32

    events = list(intake(card, led, lambda when: tmp_path / "out",
                         lambda p: None, copier=flaky,
                         splitter=lambda path, out: []))
    assert calls == ["00001_Source.WAV", "00002_Source.WAV"]
    assert [e.kind for e in events].count("recorded") == 1


def test_splitting_happens_after_recording_and_submits_the_parts(tmp_path):
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    submitted = []
    parts = [tmp_path / "p1.wav", tmp_path / "p2.wav"]
    for p in parts:
        p.write_bytes(b"x")
    events = list(intake(card, led, lambda when: tmp_path / "out",
                         submitted.append, splitter=lambda path, out: parts))
    kinds = [e.kind for e in events]
    assert kinds.index("recorded") < kinds.index("split")
    assert submitted == parts


def test_the_stop_flag_is_honoured_between_takes(tmp_path):
    card = FakeCard([_take("00001_Source.WAV"), _take("00002_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    seen = []

    class Stop:
        def is_set(self):
            return len(seen) >= 1

    def counting(card_, take, dest, **kwargs):
        seen.append(take.name)
        dest.write_bytes(b"abc")
        return "dd" * 32 if len(seen) == 1 else "ee" * 32

    list(intake(card, led, lambda when: tmp_path / "out", lambda p: None,
                copier=counting, splitter=lambda path, out: [], stop=Stop()))
    assert seen == ["00001_Source.WAV"]


def test_dest_for_is_called_with_the_true_start_time(tmp_path):
    """A take recorded last month must be filed under last month's folder,
    even though the provisional copy starts out under the folder for the
    close time (when the start time is not yet known)."""
    started = datetime(2026, 8, 3, 9, 0, 0)
    take = _take("00001_Source.WAV")
    card = FakeCard([take])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    seen_months = []

    def dest_for(when):
        seen_months.append(when)
        directory = tmp_path / when.strftime("%Y-%m")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def copier(card_, take_, dest, **kwargs):
        dest.write_bytes(b"abc")
        return "ff" * 32

    def fake_started(path, take_):
        return started

    import conteur.intake as intake_module
    original = intake_module._started
    intake_module._started = fake_started
    try:
        events = list(intake(card, led, dest_for, lambda p: None,
                             copier=copier, splitter=lambda path, out: []))
    finally:
        intake_module._started = original

    recorded = next(e for e in events if e.kind == "recorded")
    final = Path(recorded.detail)
    assert final.parent == tmp_path / "2026-08"
    assert not (tmp_path / "2026-09" / final.name).exists()
    assert started in seen_months
    assert take.closed_at in seen_months
