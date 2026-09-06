"""Un travail de nommage : lire le WAV, décider, renommer. Sans Qt."""

import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from conteur.naming import SILENCE, choose_name
from conteur.paths import build_name, unique_path
from conteur.signal import looks_like_timecode, to_whisper_input
from conteur.titler import make_title_tracked
from conteur.transcribe import transcribe


@dataclass(frozen=True)
class NameResult:
    path: Path
    slug: str
    origin: str


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def name_recording(
    wav_path: Path,
    when: datetime,
    model,
    title_fn=make_title_tracked,
    threshold_s: float = 12.0,
) -> NameResult:
    """Nomme un enregistrement déjà écrit sur disque. Ne perd jamais le fichier."""
    samples = _read_wav(wav_path)

    if looks_like_timecode(samples):
        return NameResult(path=wav_path, slug=wav_path.stem, origin="timecode")

    text, speech_s = transcribe(model, to_whisper_input(samples))

    # `title_fn` rend (titre, origine) ; on capte l'origine au passage pour
    # distinguer un titre du modèle d'un repli sur mots-clés.
    seen_origin = "transcript"

    def make_title(t: str) -> str:
        nonlocal seen_origin
        title, seen_origin = title_fn(t)
        return title

    slug = choose_name(text, speech_s, make_title, threshold_s=threshold_s)
    origin = "silence" if slug == SILENCE else seen_origin

    target = unique_path(wav_path.parent, build_name(when, slug))
    wav_path.rename(target)
    return NameResult(path=target, slug=slug, origin=origin)
