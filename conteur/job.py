"""Un travail de nommage : lire le WAV, décider, renommer. Sans Qt."""

import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from conteur.naming import (
    ORIGIN_SILENCE, ORIGIN_TIMECODE, ORIGIN_TRANSCRIPT, SILENCE,
    choose_name, slugify,
)
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


def _renamed(wav_path: Path, when: datetime, slug: str, origin: str) -> NameResult:
    target = unique_path(wav_path.parent, build_name(when, slug))
    wav_path.rename(target)
    return NameResult(path=target, slug=slug, origin=origin)


def name_recording(
    wav_path: Path,
    when: datetime,
    model,
    title_fn=make_title_tracked,
    threshold_s: float = 12.0,
) -> NameResult:
    """Nomme un enregistrement déjà écrit sur disque. Ne perd jamais le fichier."""
    samples = _read_wav(wav_path)

    if samples.size == 0:
        # Prise vide (récepteur parti avant le premier bloc) : c'est du
        # silence, pas un échec. Le fichier est conservé et nommé comme tel.
        return _renamed(wav_path, when, SILENCE, ORIGIN_SILENCE)

    if looks_like_timecode(samples):
        return NameResult(path=wav_path, slug=wav_path.stem, origin=ORIGIN_TIMECODE)

    text, speech_s = transcribe(model, to_whisper_input(samples))

    # `title_fn` rend (titre, origine) ; on capte l'origine au passage pour
    # distinguer un titre du modèle d'un repli sur mots-clés.
    seen_origin = ORIGIN_TRANSCRIPT

    def make_title(t: str) -> str:
        nonlocal seen_origin
        title, seen_origin = title_fn(t)
        return title

    slug = choose_name(text, speech_s, make_title, threshold_s=threshold_s)
    origin = ORIGIN_SILENCE if slug == SILENCE else seen_origin

    return _renamed(wav_path, when, slug, origin)


def rename_take(path: Path, new_text: str, when: datetime) -> Path:
    """Renomme une prise à la demande. Un nom vide laisse le fichier tel quel."""
    slug = slugify(new_text)
    if not slug:
        return path
    target = unique_path(path.parent, build_name(when, slug))
    path.rename(target)
    return target
