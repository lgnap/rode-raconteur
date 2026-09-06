"""Bout en bout : signal capté -> fichier nommé. Whisper et Ollama bouchonnés."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from conteur.job import name_recording
from conteur.paths import build_name
from conteur.recorder import write_wav

WHEN = datetime(2026, 9, 6, 14, 32, 8)


def _fail_if_called():
    raise AssertionError("title_fn ne doit pas être appelé pour une prise courte")


@dataclass
class Seg:
    start: float
    end: float
    text: str


class FakeModel:
    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, audio, **kwargs):
        assert kwargs["language"] == "fr"
        assert kwargs["vad_filter"] is True
        return iter(self._segments), object()


def _voice(seconds=3.0):
    rng = np.random.default_rng(7)
    n = int(48000 * seconds)
    return (rng.normal(0, 2500, n)).clip(-32768, 32767).astype(np.int16)


def test_short_take_end_to_end(tmp_path):
    provisional = tmp_path / build_name(WHEN, "sans-nom")
    write_wav(_voice(), provisional)

    result = name_recording(
        provisional, WHEN,
        FakeModel([Seg(0.0, 6.0, "Idée pour la scène du marché")]),
        title_fn=lambda _t: _fail_if_called(),
    )

    assert result.origin == "transcript"
    assert result.path.name == "2026-09-06_143208_idee-pour-la-scene-du-marche.wav"
    assert result.path.exists()
    assert not provisional.exists()




def test_long_take_end_to_end_with_ollama_down(tmp_path):
    from conteur.titler import make_title_tracked

    provisional = tmp_path / build_name(WHEN, "sans-nom")
    write_wav(_voice(), provisional)

    def failing_post(*_a, **_kw):
        raise OSError("connection refused")

    story = ("Le loup rodait autour de la maison des chevreaux. "
             "Les chevreaux avaient peur du loup. La mere chevre revint.")

    result = name_recording(
        provisional, WHEN,
        FakeModel([Seg(0.0, 45.0, story)]),
        title_fn=lambda t: make_title_tracked(t, post=failing_post),
    )

    assert result.origin == "keywords"
    assert "loup" in result.path.name
    assert "chevreaux" in result.path.name
    assert result.path.exists()
