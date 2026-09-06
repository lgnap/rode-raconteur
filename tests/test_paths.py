from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from conteur.paths import build_name, destination_dir, music_dir, unique_path

WHEN = datetime(2026, 9, 6, 14, 32, 8)


def _runner(stdout):
    def run(*_args, **_kwargs):
        return SimpleNamespace(stdout=stdout, returncode=0)
    return run


def test_music_dir_uses_xdg(tmp_path):
    assert music_dir(runner=_runner(f"{tmp_path}\n")) == tmp_path


def test_music_dir_falls_back_to_home_music(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert music_dir(runner=_runner("\n")) == tmp_path / "Music"


def test_destination_dir_is_created_with_month_folder(tmp_path):
    out = destination_dir(WHEN, runner=_runner(f"{tmp_path}\n"))
    assert out == tmp_path / "Enregistrements" / "2026-09"
    assert out.is_dir()


def test_build_name_uses_timestamp_prefix():
    assert build_name(WHEN, "le-loup") == "2026-09-06_143208_le-loup.wav"


def test_unique_path_returns_plain_name_when_free(tmp_path):
    assert unique_path(tmp_path, "a.wav") == tmp_path / "a.wav"


def test_unique_path_suffixes_on_collision(tmp_path):
    (tmp_path / "a.wav").touch()
    assert unique_path(tmp_path, "a.wav") == tmp_path / "a-2.wav"
    (tmp_path / "a-2.wav").touch()
    assert unique_path(tmp_path, "a.wav") == tmp_path / "a-3.wav"
