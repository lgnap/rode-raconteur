"""L'outil de nommage par lot — ce que la suite de l'application ne couvre pas."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "nommer-morceaux.py"


@pytest.fixture(scope="module")
def outil():
    spec = importlib.util.spec_from_file_location("nommer_morceaux", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finds_wav_whatever_the_case(outil, tmp_path):
    """The device writes .WAV, the application writes .wav, and both coexist.
    Looking only for lowercase let every field take slip through."""
    (tmp_path / "appareil.WAV").write_bytes(b"")
    (tmp_path / "application.wav").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")
    found = {p.name for p in outil.collect([tmp_path])}
    assert found == {"appareil.WAV", "application.wav"}


def test_search_is_recursive(outil, tmp_path):
    sub = tmp_path / "Source-Baffle" / "00009"
    sub.mkdir(parents=True)
    (sub / "01_sur_03__00-00.000_a_01-20.799.wav").write_bytes(b"")
    assert len(outil.collect([tmp_path])) == 1


def test_hidden_files_are_ignored(outil, tmp_path):
    (tmp_path / "._resource.wav").write_bytes(b"")
    (tmp_path / "vrai.wav").write_bytes(b"")
    assert [p.name for p in outil.collect([tmp_path])] == ["vrai.wav"]


def test_an_explicit_file_is_taken_as_is(outil, tmp_path):
    path = tmp_path / "une.wav"
    path.write_bytes(b"")
    assert outil.collect([path]) == [path]


@pytest.mark.parametrize("stem, annote", [
    # Straight off the device: no "__" before annotation, one after.
    ("00003_Ambiance-Personne", False),
    ("00003_Ambiance-Personne__la-licorne", True),
    # Part cut at markers: one "__" from the start, its time range.
    ("01_sur_03__00-00.000_a_01-20.799", False),
    ("01_sur_03__00-00.000_a_01-20.799__la-licorne", True),
])
def test_detects_what_is_already_annotated(outil, stem, annote):
    """Counting "__" is not enough: the two sources do not start with the same
    number of them. Without this, a second pass would stack another slug."""
    assert outil.already_annotated(stem) is annote


def test_an_already_annotated_file_is_skipped(outil, tmp_path):
    path = tmp_path / "01_sur_03__00-00.000_a_01-20.799__la-licorne.wav"
    path.write_bytes(b"")
    assert outil.annotate(path, model=None) is None
    assert path.exists()


def test_a_device_file_is_not_annotated_twice(outil, tmp_path):
    path = tmp_path / "00003_Ambiance-Personne__la-licorne.WAV"
    path.write_bytes(b"")
    assert outil.annotate(path, model=None) is None
    assert path.exists()


@pytest.mark.parametrize("stem", [
    "00002_Source-Baffle__sans-nom",
    "00002_Source-Baffle__sans-nom-2",
])
def test_placeholder_names_are_not_considered_already_annotated(outil, stem):
    """Placeholder names must be processed again, not skipped.
    This pairs with the orphan recovery test to ensure the two functions agree."""
    assert not outil.already_annotated(stem)


def test_edge_case_card_name_with_double_underscore(outil):
    """Card names can contain __, and already_annotated still works."""
    # A card named "Source__Baffle" would produce this:
    assert not outil.already_annotated("00001_Source__Baffle__sans-nom")
    assert outil.already_annotated("00001_Source__Baffle__la-licorne")


def test_edge_case_empty_slug_after_double_underscore(outil):
    """An edge case: a file with __ but empty slug after it."""
    # Empty string does not match SLUG regex, so should not be annotated.
    assert not outil.already_annotated("00001_Source-Baffle__")


@pytest.mark.parametrize("stem, attendu", [
    # An unnamed import: the placeholder is replaced, not kept in the middle.
    ("2026-09-07_111030_00002_Source-Baffle__sans-nom",
     "2026-09-07_111030_00002_Source-Baffle__la-licorne"),
    # Its collision suffix counts as a placeholder too.
    ("2026-09-07_111030_00002_Source-Baffle__sans-nom-2",
     "2026-09-07_111030_00002_Source-Baffle__la-licorne"),
    # A part cut at markers: its time range is not a placeholder, so the slug
    # is appended and the range survives.
    ("01_sur_03__00-00.000_a_01-20.799",
     "01_sur_03__00-00.000_a_01-20.799__la-licorne"),
    # Straight off the device, no "__" at all.
    ("00003_Ambiance-Personne", "00003_Ambiance-Personne__la-licorne"),
])
def test_the_placeholder_is_replaced_never_kept_alongside(outil, stem, attendu):
    """Appending after `__sans-nom` fixed the placeholder in the middle of the
    name for good, and the application produced a different shape from the
    same file — whichever ran first decided which wrong name you got."""
    assert outil.named(stem, "la-licorne") == attendu
