"""The ledger: what has been imported, and whether erasing is allowed."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from conteur.ledger import Ledger, Record, name_prefix

WHEN = datetime(2026, 9, 7, 11, 10, 30)


def _record(tmp_path, name, size, content=None):
    """Write a file and record it under its real digest and stable prefix.

    The digest has to be the file's actual hash: erase_allowed re-reads the
    destination file and compares, so a made-up value could never match.
    """
    path = tmp_path / name
    body = content if content is not None else b"x" * size
    path.write_bytes(body)
    return Record(digest=hashlib.sha256(body).hexdigest(), card_name=name,
                  size=len(body), folder=path.parent,
                  prefix=name_prefix(path), imported_at=WHEN)


def test_the_prefix_is_everything_before_the_last_double_underscore():
    assert name_prefix(Path("2026-09-07_111225_00002_S-B_02_sur_06__la-licorne.wav")) \
        == "2026-09-07_111225_00002_S-B_02_sur_06"
    assert name_prefix(Path("2026-09-07_111030_sans-nom.wav")) \
        == "2026-09-07_111030_sans-nom"


def test_a_record_survives_a_reload(tmp_path):
    """Kept on disk, not in memory: unplug, come back tomorrow, and the proof
    must still be there."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source.WAV", 10)
    led.add(record)
    assert Ledger("800A-F63E", root=tmp_path).has(record.digest)


def test_takes_are_identified_by_digest_not_by_name(tmp_path):
    """The take counter restarts at 00001 after an erase, so in two months
    there will be another 00002_Source-Baffle.WAV holding something else. A
    ledger keyed by name would believe it already owned it."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00002_Source.WAV", 10)
    led.add(record)
    assert led.has(record.digest)
    assert not led.has("bb" * 32)


def test_a_record_is_found_again_after_the_file_is_renamed(tmp_path):
    """Naming renames the file. The ledger used to hold a path, which then
    pointed at nothing, and the lock closed for ever after a successful
    import. Everything before the last `__` survives every renaming path.
    """
    led = Ledger("800A-F63E", root=tmp_path)
    rec = _record(tmp_path, "2026-09-07_111030_00002_S-B__sans-nom.wav", 10)
    led.add(rec)
    assert led.locate(rec) == rec.folder / "2026-09-07_111030_00002_S-B__sans-nom.wav"
    (rec.folder / "2026-09-07_111030_00002_S-B__sans-nom.wav").rename(
        rec.folder / "2026-09-07_111030_00002_S-B__la-licorne.wav")
    assert led.locate(rec).name == "2026-09-07_111030_00002_S-B__la-licorne.wav"


def test_an_ambiguous_prefix_locates_nothing(tmp_path):
    """Two candidates is not a match: guessing which one is the take would be
    a guess about which file may be destroyed."""
    led = Ledger("800A-F63E", root=tmp_path)
    rec = _record(tmp_path, "2026-09-07_111030_00002_S-B__sans-nom.wav", 10)
    led.add(rec)
    (rec.folder / "2026-09-07_111030_00002_S-B__autre.wav").write_bytes(b"y" * 10)
    assert led.locate(rec) is None


def test_erasing_is_refused_while_one_take_is_unaccounted_for(tmp_path):
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source__sans-nom.wav", 10)
    led.add(record)
    assert led.erase_allowed([record.digest, "bb" * 32]) is False


def test_erasing_is_allowed_when_every_digest_is_held_and_intact(tmp_path):
    led = Ledger("800A-F63E", root=tmp_path)
    rec = _record(tmp_path, "2026-09-07_111030_00002_S-B__sans-nom.wav", 10)
    led.add(rec)
    assert led.erase_allowed([rec.digest]) is True
    assert led.erase_allowed([rec.digest, "aa" * 32]) is False


def test_a_take_added_after_the_import_locks_erasing_again(tmp_path):
    """This happened in a working session: a story recorded onto a
    transmitter after its card had been imported. A digest the ledger never
    saw is exactly the new take's digest, and erasing must close on it.
    """
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source__sans-nom.wav", 10)
    led.add(record)
    assert led.erase_allowed([record.digest]) is True
    assert led.erase_allowed([record.digest, "cc" * 32]) is False


def test_a_deleted_destination_file_locks_erasing_again(tmp_path):
    """The lock is computed from the files, not from the ledger's word. Move
    your takes elsewhere and erasing closes again, which is what you want."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source__sans-nom.wav", 10)
    led.add(record)
    led.locate(record).unlink()
    assert led.erase_allowed([record.digest]) is False


def test_a_renamed_file_still_unlocks_but_a_tampered_one_does_not(tmp_path):
    led = Ledger("800A-F63E", root=tmp_path)
    rec = _record(tmp_path, "2026-09-07_111030_00002_S-B__sans-nom.wav", 10)
    led.add(rec)
    found = led.locate(rec)
    found.rename(found.with_name("2026-09-07_111030_00002_S-B__la-licorne.wav"))
    assert led.erase_allowed([rec.digest]) is True
    led.locate(rec).write_bytes(b"tampered")
    assert led.erase_allowed([rec.digest]) is False


def test_a_tampered_destination_file_locks_erasing_again(tmp_path):
    """The lock is computed from the bytes on disk, not from what the ledger
    remembers. A file replaced or corrupted in place must close it again."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source__sans-nom.wav", 10)
    led.add(record)
    assert led.erase_allowed([record.digest]) is True
    led.locate(record).write_bytes(b"tampered")
    assert led.erase_allowed([record.digest]) is False


def test_an_unreadable_ledger_behaves_as_an_empty_one(tmp_path):
    """Failing safe by construction: an empty ledger keeps the lock closed, so
    a corrupt file cannot authorise an erase."""
    (tmp_path / "800A-F63E.json").write_text("{ this is not json")
    led = Ledger("800A-F63E", root=tmp_path)
    assert led.records() == []
    assert led.erase_allowed(["aa" * 32]) is False


def test_a_malformed_ledger_file_behaves_as_an_empty_one(tmp_path):
    """Well-formed JSON of the wrong shape must not crash Ledger.__init__.
    A bare list, null, a scalar, or 'takes' bound to a non-list all return
    an empty ledger that keeps the erase lock closed."""
    # Test bare list
    (tmp_path / "800A-F63E.json").write_text(json.dumps([1, 2, 3]))
    led = Ledger("800A-F63E", root=tmp_path)
    assert led.records() == []
    assert led.erase_allowed(["aa" * 32]) is False

    # Test 'takes' as non-list
    (tmp_path / "800A-F63E.json").write_text(json.dumps({"takes": 7}))
    led = Ledger("800A-F63E", root=tmp_path)
    assert led.records() == []
    assert led.erase_allowed(["aa" * 32]) is False


def test_a_ledger_written_before_the_prefix_existed_is_still_read(tmp_path):
    """Records on disk carry `path`. Dropping them would silently close the
    lock on every card imported before this change."""
    (tmp_path / "800A-F63E.json").write_text(json.dumps({
        "serial": "800A-F63E",
        "takes": [{
            "digest": "aa" * 32, "card_name": "00001_S.WAV", "size": 10,
            "path": str(tmp_path / "2026-09-07_111030_00001_S__sans-nom.wav"),
            "imported_at": WHEN.isoformat(),
        }],
    }))
    led = Ledger("800A-F63E", root=tmp_path)
    rec = led.records()[0]
    assert rec.prefix == "2026-09-07_111030_00001_S"
    assert rec.folder == tmp_path
