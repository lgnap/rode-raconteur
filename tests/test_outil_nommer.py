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
