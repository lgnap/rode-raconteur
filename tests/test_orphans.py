"""Recovery of takes left without a name."""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from conteur.orphans import find_orphans, is_orphan, is_placeholder, parse_timestamp

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "nommer-morceaux.py"


@pytest.fixture(scope="module")
def outil():
    """Import the CLI naming tool for agreement tests."""
    spec = importlib.util.spec_from_file_location("nommer_morceaux", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_an_unnamed_import_is_recovered_too():
    """Imported takes carry the card's name between the stamp and the slug.

    Splitting on the first underscore, as the original did, made them
    unrecognisable — an import whose naming failed would have stayed unnamed
    for ever, silently.
    """
    assert is_orphan("2026-09-07_111030_00002_Source-Baffle__sans-nom.wav")
    assert is_orphan("2026-09-07_111030_00002_Source-Baffle__sans-nom-2.wav")


def test_a_named_import_is_not_an_orphan():
    assert not is_orphan("2026-09-07_111030_00002_Source-Baffle__la-licorne.wav")


def test_is_placeholder():
    """The placeholder detection is shared by both naming functions."""
    assert is_placeholder("sans-nom")
    assert is_placeholder("sans-nom-2")
    assert is_placeholder("sans-nom-13")
    assert not is_placeholder("la-licorne")
    assert not is_placeholder("silence")


def test_edge_case_card_name_with_double_underscore():
    """Card names can contain __, and we still extract the slug correctly."""
    # A card named "Source__Baffle" would produce this:
    assert is_orphan("2026-09-07_111030_00001_Source__Baffle__sans-nom.wav")
    assert not is_orphan("2026-09-07_111030_00001_Source__Baffle__la-licorne.wav")


def test_edge_case_empty_slug_after_double_underscore():
    """An edge case: a file with __ but empty slug after it."""
    # This would be malformed but we should handle it gracefully.
    # Empty string is not the placeholder, so should not be orphan.
    assert not is_orphan("2026-09-07_111030_00001_Source-Baffle__.wav")


def test_the_two_naming_rules_agree_on_the_placeholder(outil):
    """orphans.is_orphan and the CLI's already_annotated must not disagree:
    a file one calls "still unnamed" and the other calls "already named"
    would be skipped by the tool for ever.

    This test directly compares the two functions on the names that matter.
    """
    # Placeholder names: is_orphan should return True, already_annotated should return False
    placeholder_names = [
        "sans-nom",
        "sans-nom-2",
        "00002_Source-Baffle__sans-nom",
        "00002_Source-Baffle__sans-nom-2",
    ]
    for stem in placeholder_names:
        assert is_orphan(f"2026-09-07_111030_{stem}.wav"), f"is_orphan should see {stem} as orphan"
        assert not outil.already_annotated(stem), f"already_annotated should not skip {stem}"

    # Named files: is_orphan should return False, already_annotated should return True
    named_stems = [
        "00002_Source-Baffle__la-licorne",
        "00002_Source-Baffle__silence",
    ]
    for stem in named_stems:
        assert not is_orphan(f"2026-09-07_111030_{stem}.wav"), f"is_orphan should not see {stem} as orphan"
        assert outil.already_annotated(stem), f"already_annotated should recognize {stem} as named"
