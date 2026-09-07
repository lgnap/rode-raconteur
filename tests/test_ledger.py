"""The ledger: what has been imported, and whether erasing is allowed."""

import hashlib
from datetime import datetime
from pathlib import Path

from conteur.card import Take
from conteur.ledger import Ledger, Record

WHEN = datetime(2026, 9, 7, 11, 10, 30)


def _take(name, size, digest_source=None):
    return Take(name, size, 3, WHEN)


def _record(tmp_path, name, size, content=None):
    """Write a file and record it under its real digest.

    The digest has to be the file's actual hash: erase_allowed re-reads the
    destination file and compares, so a made-up value could never match.
    """
    path = tmp_path / name
    body = content if content is not None else b"x" * size
    path.write_bytes(body)
    return Record(digest=hashlib.sha256(body).hexdigest(), card_name=name,
                  size=len(body), path=path, imported_at=WHEN)


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


def test_erasing_is_refused_while_one_take_is_unaccounted_for(tmp_path):
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source.WAV", 10)
    led.add(record)
    takes = [_take("00001_Source.WAV", 10), _take("00002_Source.WAV", 20)]
    assert led.erase_allowed(takes, {"00001_Source.WAV": record.digest}) is False


def test_erasing_is_allowed_once_every_take_is_accounted_for(tmp_path):
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source.WAV", 10)
    led.add(record)
    takes = [_take("00001_Source.WAV", 10)]
    assert led.erase_allowed(takes, {"00001_Source.WAV": record.digest}) is True


def test_a_take_added_after_the_import_locks_erasing_again(tmp_path):
    """This happened in a working session: a story recorded onto a transmitter
    after its card had been imported. Erasing must close again on its own."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source.WAV", 10)
    led.add(record)
    assert led.erase_allowed([_take("00001_Source.WAV", 10)],
                             {"00001_Source.WAV": record.digest}) is True
    later = [_take("00001_Source.WAV", 10), _take("00003_Source.WAV", 30)]
    assert led.erase_allowed(later, {"00001_Source.WAV": record.digest}) is False


def test_a_deleted_destination_file_locks_erasing_again(tmp_path):
    """The lock is computed from the files, not from the ledger's word. Move
    your takes elsewhere and erasing closes again, which is what you want."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source.WAV", 10)
    led.add(record)
    record.path.unlink()
    assert led.erase_allowed([_take("00001_Source.WAV", 10)],
                             {"00001_Source.WAV": record.digest}) is False


def test_an_unreadable_ledger_behaves_as_an_empty_one(tmp_path):
    """Failing safe by construction: an empty ledger keeps the lock closed, so
    a corrupt file cannot authorise an erase."""
    (tmp_path / "800A-F63E.json").write_text("{ this is not json")
    led = Ledger("800A-F63E", root=tmp_path)
    assert led.records() == []
    assert led.erase_allowed([_take("00001_Source.WAV", 10)], {}) is False


def test_a_malformed_ledger_file_behaves_as_an_empty_one(tmp_path):
    """Well-formed JSON of the wrong shape must not crash Ledger.__init__.
    A bare list, null, a scalar, or 'takes' bound to a non-list all return
    an empty ledger that keeps the erase lock closed."""
    import json

    # Test bare list
    (tmp_path / "800A-F63E.json").write_text(json.dumps([1, 2, 3]))
    led = Ledger("800A-F63E", root=tmp_path)
    assert led.records() == []
    assert led.erase_allowed([_take("00001_Source.WAV", 10)], {}) is False

    # Test 'takes' as non-list
    (tmp_path / "800A-F63E.json").write_text(json.dumps({"takes": 7}))
    led = Ledger("800A-F63E", root=tmp_path)
    assert led.records() == []
    assert led.erase_allowed([_take("00001_Source.WAV", 10)], {}) is False


def test_a_tampered_destination_file_locks_erasing_again(tmp_path):
    """The lock is computed from the bytes on disk, not from what the ledger
    remembers. A file replaced or corrupted in place must close it again."""
    led = Ledger("800A-F63E", root=tmp_path)
    record = _record(tmp_path, "00001_Source.WAV", 10)
    led.add(record)
    assert led.erase_allowed([_take("00001_Source.WAV", 10)],
                             {"00001_Source.WAV": record.digest}) is True
    record.path.write_bytes(b"tampered")
    assert led.erase_allowed([_take("00001_Source.WAV", 10)],
                             {"00001_Source.WAV": record.digest}) is False
