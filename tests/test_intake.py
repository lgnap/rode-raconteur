"""The whole chain, unrolled synchronously. No Qt, no thread, no hardware."""

from datetime import datetime, timedelta

import pytest
from pathlib import Path

from conteur.card import Take, VerificationError
from conteur.intake import Event, import_name, intake, part_name
from conteur.ledger import Ledger
from conteur.orphans import is_orphan, parse_timestamp

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


def _marked_wav(markers=(4800,)) -> bytes:
    """A minimal BWF carrying cue points, so intake sees a marked take."""
    import struct as _s
    fmt = _s.pack("<HHIIHH", 3, 1, 48000, 48000 * 4, 4, 32)
    cue = _s.pack("<I", len(markers))
    for i, pos in enumerate(markers, 1):
        cue += _s.pack("<II4sIII", i, pos, b"data", 0, 0, pos)
    data = b"\x00" * 64

    def chunk(cid, payload):
        pad = b"\x00" if len(payload) & 1 else b""
        return cid + _s.pack("<I", len(payload)) + payload + pad

    body = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"cue ", cue) + chunk(b"data", data)
    return b"RIFF" + _s.pack("<I", len(body)) + body


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
                         submitted.append, splitter=lambda path, out, **kwargs: []))
    assert [e.kind for e in events] == [
        "inventory", "copy", "verified", "recorded", "submitted", "done",
    ]
    assert len(submitted) == 1


def test_a_take_already_recorded_is_skipped(tmp_path):
    """Same name *and* same bytes: the digest branch after the copy is what
    recognises it. The copy still happens — it is the only way to know the
    digest — so "copy" precedes "skipped", and no second file is left behind.
    """
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    dest_for = lambda when: tmp_path / "out"
    list(intake(card, led, dest_for, lambda p: None,
                splitter=lambda path, out, **kwargs: []))
    events = list(intake(card, led, dest_for, lambda p: None,
                         splitter=lambda path, out, **kwargs: []))
    assert [e.kind for e in events] == ["inventory", "copy", "skipped", "done"]
    assert len(led.records()) == 1
    assert len(list((tmp_path / "out").iterdir())) == 1


def test_the_same_name_with_new_content_is_a_new_take(tmp_path):
    """The counter restarts at 00001 after an erase, so the next card holds
    names the ledger already knows, carrying takes it has never seen. Judging
    on the name would discard a real story in silence and tell the user it was
    already imported."""
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    dest_for = lambda when: tmp_path / "out"

    first = FakeCard([Take("00001_Source.WAV", len(b"the first story"), 3, WHEN)],
                     content=b"the first story")
    list(intake(first, led, dest_for, lambda p: None,
                splitter=lambda path, out, **kwargs: []))
    assert len(led.records()) == 1

    # Erased, re-recorded: the counter is back to 00001 and the take under
    # that name is a different one.
    again = FakeCard([Take("00001_Source.WAV", len(b"a second story"), 3, WHEN)],
                     content=b"a second story")
    events = list(intake(again, led, dest_for, lambda p: None,
                         splitter=lambda path, out, **kwargs: []))

    kinds = [e.kind for e in events]
    assert "skipped" not in kinds
    assert kinds == ["inventory", "copy", "verified", "recorded", "submitted",
                     "done"]
    assert len(led.records()) == 2
    assert len({r.digest for r in led.records()}) == 2
    assert len(list((tmp_path / "out").iterdir())) == 2


def test_a_failed_verification_stops_before_recording(tmp_path):
    """The order that is not negotiable: a ledger noting an unverified copy is
    a lock that opens on nothing."""
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")

    def refuse(*args, **kwargs):
        raise VerificationError("mismatch")

    events = list(intake(card, led, lambda when: tmp_path / "out",
                         lambda p: None, copier=refuse,
                         splitter=lambda path, out, **kwargs: []))
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
                         splitter=lambda path, out, **kwargs: []))
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
                         submitted.append, splitter=lambda path, out, **kwargs: parts))
    kinds = [e.kind for e in events]
    assert kinds.index("recorded") < kinds.index("split")
    # Only the parts. The whole take was cut, so submitting it as well would
    # transcribe the same audio twice on a queue that runs one job at a time.
    # It keeps a name of its own — `decoupee` — so orphan recovery leaves it
    # alone without anyone having to listen to it.
    assert submitted == parts


def test_splitting_only_happens_once_the_record_has_landed(tmp_path):
    """Splitting destroys the file whose hash was taken, so the proof must be
    written first. Asserting on event order alone would not catch an inversion
    that left the yields in place."""
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    seen_at_split = []

    def splitter(path, out, **kwargs):
        seen_at_split.append(len(led.records()))
        return []

    list(intake(card, led, lambda when: tmp_path / "out", lambda p: None,
                splitter=splitter))
    assert seen_at_split == [1]


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
                copier=counting, splitter=lambda path, out, **kwargs: [], stop=Stop()))
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
                             copier=copier, splitter=lambda path, out, **kwargs: []))
    finally:
        intake_module._started = original

    recorded = next(e for e in events if e.kind == "recorded")
    final = Path(recorded.detail)
    assert final.parent == tmp_path / "2026-08"
    assert not (tmp_path / "2026-09" / final.name).exists()
    assert started in seen_months
    assert take.closed_at in seen_months


def test_the_same_content_under_a_new_name_is_skipped_not_recorded_twice(tmp_path):
    """The counter restarts at 00001 after an erase, so the same bytes can
    turn up again under a name the ledger has never seen. Identity is the
    digest, never the name, so this must be recognised and not duplicated."""
    led = Ledger("800A-F63E", root=tmp_path / "ledger")

    def same_content(card_, take, dest, **kwargs):
        dest.write_bytes(b"identical bytes")
        return "11" * 32

    first_card = FakeCard([_take("00001_Source.WAV")])
    list(intake(first_card, led, lambda when: tmp_path / "out", lambda p: None,
                copier=same_content, splitter=lambda path, out, **kwargs: []))
    assert len(led.records()) == 1
    before = sorted(p.name for p in (tmp_path / "out").iterdir())

    # Same digest, different card name — as if the card had been erased and
    # the counter had wrapped back to a name already used before.
    second_card = FakeCard([Take("00003_Other.WAV", 3, 3, WHEN)])
    events = list(intake(second_card, led, lambda when: tmp_path / "out",
                         lambda p: None, copier=same_content,
                         splitter=lambda path, out, **kwargs: []))

    assert [e.kind for e in events] == ["inventory", "copy", "skipped", "done"]
    assert len(led.records()) == 1
    after = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert after == before


def test_a_part_carries_its_own_start_the_card_name_and_its_rank():
    assert part_name(datetime(2026, 9, 7, 11, 12, 25),
                     "00002_Source-Baffle.WAV", 2, 6) == \
        "2026-09-07_111225_00002_Source-Baffle_02_sur_06__sans-nom.wav"


def test_a_part_is_dated_visible_to_recovery_and_beside_its_take(tmp_path):
    """The old <stem>/NN_sur_MM.wav shape fell out of every convention at
    once: no timestamp, so parse_timestamp returned None and the part was
    stamped with the import time; no __ tail, so is_orphan was False and a
    part left unnamed by a crash was invisible to recovery for ever."""
    started = datetime(2026, 9, 7, 11, 10, 30)
    card = FakeCard([_take("00002_Source-Baffle.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    out = tmp_path / "out"

    def splitter(path, out_dir, name_for=None):
        # Two parts, the second starting 115 seconds in at 48 kHz.
        written = []
        for index, offset in ((1, 0), (2, 115 * 48000)):
            target = out_dir / name_for(index, 2, offset, 48000)
            target.write_bytes(b"x")
            written.append(target)
        return written

    import conteur.intake as intake_module
    original = intake_module._started
    intake_module._started = lambda path, take: started
    try:
        list(intake(card, led, lambda when: out, lambda p: None,
                    splitter=splitter))
    finally:
        intake_module._started = original

    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "2026-09-07_111030_00002_Source-Baffle_01_sur_02__sans-nom.wav",
        "2026-09-07_111030_00002_Source-Baffle__sans-nom.wav",
        "2026-09-07_111225_00002_Source-Baffle_02_sur_02__sans-nom.wav",
    ]
    for name in names:
        assert parse_timestamp(name) is not None
        assert is_orphan(name)


def test_a_take_that_cannot_be_split_does_not_cost_the_rest_of_the_card(tmp_path):
    """bwf.split raises ValueError on a missing chunk. Outside the per-take
    guard that escaped the loop, and the second take was never attempted —
    against intake's own promise that a failure on one take does not stop the
    others."""
    card = FakeCard([_take("00001_Source.WAV"), _take("00002_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    submitted = []
    seen = []

    def distinct(card_, take, dest, **kwargs):
        dest.write_bytes(take.name.encode())
        return take.name[:5] * 12 + "abcd"

    def splitter(path, out_dir, name_for=None):
        seen.append(path.name)
        if len(seen) == 1:
            raise ValueError("missing fmt or data chunk")
        return []

    events = list(intake(card, led, lambda when: tmp_path / "out",
                         submitted.append, copier=distinct, splitter=splitter))
    kinds = [e.kind for e in events]
    assert len(seen) == 2
    assert kinds.count("recorded") == 2
    assert kinds.count("failed") == 1
    # The take that could not be split was still copied, recorded and named.
    assert len(submitted) == 2
    assert len(led.records()) == 2


def test_a_broken_header_after_the_copy_does_not_stop_the_next_take(tmp_path):
    """_started unpacks the header of the file just written; a malformed one
    raises struct.error, which is neither OSError nor ValueError."""
    import struct

    card = FakeCard([_take("00001_Source.WAV"), _take("00002_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    seen = []

    def distinct(card_, take, dest, **kwargs):
        dest.write_bytes(take.name.encode())
        return take.name[:5] * 12 + "abcd"

    def broken(path, take):
        seen.append(take.name)
        if len(seen) == 1:
            raise struct.error("unpack requires a buffer of 2 bytes")
        return WHEN

    import conteur.intake as intake_module
    original = intake_module._started
    intake_module._started = broken
    try:
        events = list(intake(card, led, lambda when: tmp_path / "out",
                             lambda p: None, copier=distinct,
                             splitter=lambda path, out, **kwargs: []))
    finally:
        intake_module._started = original

    kinds = [e.kind for e in events]
    assert seen == ["00001_Source.WAV", "00002_Source.WAV"]
    assert kinds.count("failed") == 1
    assert kinds.count("recorded") == 1
    assert len(led.records()) == 1


def test_a_ledger_that_cannot_be_written_still_stops_everything(tmp_path):
    """The one failure that is deliberately not caught: copying without
    recording means recopying for ever, and an erase lock with no proof."""
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")

    def refuse(record):
        raise OSError("read-only file system")

    led.add = refuse
    with pytest.raises(OSError):
        list(intake(card, led, lambda when: tmp_path / "out", lambda p: None,
                    splitter=lambda path, out, **kwargs: []))


def test_the_duration_uses_the_file_own_sample_rate(tmp_path):
    """A take with no bext is dated close - duration, and a duration computed
    at 48 kHz for a file recorded at 24 kHz is out by a factor of two. A third
    of the files take this path, and the design calls a non-48 kHz take
    ordinary."""
    import struct as _struct

    from conteur.intake import _started

    frames = 24000 * 7                      # seven seconds at 24 kHz
    fmt = _struct.pack("<HHIIHH", 1, 1, 24000, 24000 * 2, 2, 16)
    data = b"\x00" * (frames * 2)
    path = tmp_path / "take.wav"
    path.write_bytes(
        b"RIFF" + _struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
        + b"fmt " + _struct.pack("<I", len(fmt)) + fmt
        + b"data" + _struct.pack("<I", len(data)) + data)

    take = Take("00001_Source.WAV", len(data), 3, WHEN)
    assert _started(path, take) == WHEN - timedelta(seconds=7)


# --- a split take is not transcribed a second time ---


def test_a_split_take_is_named_decoupee_and_not_submitted(tmp_path):
    """Transcribing a take and then its own parts is the same audio twice.

    Measured on real material: a 242 s take cut into six parts cost 484 s of
    naming for 242 s of sound — 38 % of the batch wasted on the queue that is
    bounded by VRAM and runs one job at a time. The whole take still has to
    carry a name, or orphan recovery hunts it down at every launch; it just
    does not have to be listened to for one.
    """
    wav = _marked_wav()
    card = FakeCard([_take("00001_Source.WAV", size=len(wav))], content=wav)
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    submitted = []
    parts = [tmp_path / "out" / "p1.wav", tmp_path / "out" / "p2.wav"]

    def splitter(path, out, **kwargs):
        out.mkdir(parents=True, exist_ok=True)
        for p in parts:
            p.write_bytes(b"x")
        return parts

    list(intake(card, led, lambda w: tmp_path / "out", submitted.append,
                splitter=splitter))
    assert submitted == parts
    kept = led.records()[0].path.name
    assert kept.endswith("__decoupee.wav"), kept
    assert not is_orphan(kept)


def test_a_take_whose_split_fails_is_still_submitted(tmp_path):
    """Named decoupee but never cut: it must still get a real name."""
    wav = _marked_wav()
    card = FakeCard([_take("00001_Source.WAV", size=len(wav))], content=wav)
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    submitted = []

    def splitter(path, out, **kwargs):
        raise ValueError("missing fmt or data chunk")

    list(intake(card, led, lambda w: tmp_path / "out", submitted.append,
                splitter=splitter))
    assert len(submitted) == 1
    assert submitted[0] == led.records()[0].path


def test_a_take_without_markers_is_submitted_as_before(tmp_path):
    card = FakeCard([_take("00001_Source.WAV")])
    led = Ledger("800A-F63E", root=tmp_path / "ledger")
    submitted = []
    list(intake(card, led, lambda w: tmp_path / "out", submitted.append,
                splitter=lambda path, out, **kwargs: []))
    assert len(submitted) == 1
    assert submitted[0].name.endswith("__sans-nom.wav")
