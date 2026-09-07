"""Recovery of takes left without a name."""

from datetime import datetime
from pathlib import Path

import pytest

from conteur.orphans import find_orphans, is_orphan, parse_timestamp


@pytest.mark.parametrize("name, expected", [
    ("2026-09-06_162542_sans-nom.wav", True),
    ("2026-09-06_162542_sans-nom-2.wav", True),      # variante de collision
    ("2026-09-06_162542_sans-nom-13.wav", True),
    ("2026-09-06_162542_il-etait-une-fois.wav", False),
    ("2026-09-06_162542_silence.wav", False),
    ("2026-09-06_162542_timecode.wav", False),
    ("sans-nom.wav", False),                          # pas d'horodatage
    ("2026-09-06_162542_sans-nom.txt", False),        # pas un WAV
    ("2026-13-06_162542_sans-nom.wav", False),        # mois impossible
])
def test_is_orphan(name, expected):
    assert is_orphan(name) is expected


def test_parse_timestamp():
    assert parse_timestamp("2026-09-06_162542_sans-nom.wav") == datetime(2026, 9, 6, 16, 25, 42)
    assert parse_timestamp("bonjour.wav") is None


def test_find_orphans_is_recursive_and_chronological(tmp_path):
    # An earlier month must not be forgotten because the month has changed.
    for month, stamp in [("2026-08", "2026-08-31_235959"), ("2026-09", "2026-09-06_162542")]:
        d = tmp_path / month
        d.mkdir()
        (d / f"{stamp}_sans-nom.wav").write_bytes(b"")
    (tmp_path / "2026-09" / "2026-09-06_170000_deja-nomme.wav").write_bytes(b"")

    found = find_orphans(tmp_path)
    assert [p.name for p, _ in found] == [
        "2026-08-31_235959_sans-nom.wav",
        "2026-09-06_162542_sans-nom.wav",
    ]
    assert found[0][1] == datetime(2026, 8, 31, 23, 59, 59)


def test_find_orphans_on_a_missing_directory_is_empty(tmp_path):
    assert find_orphans(tmp_path / "jamais-cree") == []


def test_find_orphans_ignores_everything_already_named(tmp_path):
    (tmp_path / "2026-09-06_162542_le-loup.wav").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")
    assert find_orphans(tmp_path) == []
