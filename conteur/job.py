"""Un travail de nommage : lire le WAV, décider, renommer. Sans Qt."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from conteur.naming import (
    NO_SPEECH, ORIGIN_NO_SPEECH, ORIGIN_SILENCE, ORIGIN_TIMECODE,
    ORIGIN_TRANSCRIPT, SILENCE,
    choose_name, slugify,
)
from conteur.paths import build_name, unique_path
from conteur.signal import (
    CAPTURE_RATE, looks_like_timecode, rms_dbfs, to_whisper_input,
)
from conteur.wavread import read_samples
from conteur.titler import make_title_tracked
from conteur.transcribe import transcribe


# Au-dessus de ce niveau RMS, un fichier est audible : s'il ne contient pas de
# parole, ce n'est pas du silence.
AUDIBLE_DBFS = -50.0

# Pour nommer un fichier, il suffit d'en écouter le début : transcrire une heure
# d'audio pour produire trois mots est un gâchis. En deçà de ce plafond la prise
# est transcrite en entier, ce qui donne de meilleures statistiques de mots-clés
# et plus de contexte au titrage. Deux fenêtres au plus sont examinées — la
# seconde ne sert qu'aux prises dont le début est muet.
NAMING_SAMPLE_S = 900.0
NAMING_WINDOWS = 2


@dataclass(frozen=True)
class NameResult:
    path: Path
    slug: str
    origin: str


def _read_wav(path: Path) -> np.ndarray:
    # Pas `wave` : il refuse le format 3 (flottant IEEE), celui des
    # enregistrements embarqués RØDE. Sans cela l'application ne saurait
    # nommer que ses propres fichiers.
    return read_samples(path)


def _renamed(wav_path: Path, when: datetime, slug: str, origin: str) -> NameResult:
    target = unique_path(wav_path.parent, build_name(when, slug))
    wav_path.rename(target)
    return NameResult(path=target, slug=slug, origin=origin)


def _transcribe_sample(model, samples) -> tuple[str, float]:
    """Transcrit le début, et une seconde fenêtre seulement s'il est muet.

    Nommer ne demande pas d'entendre tout le fichier : une histoire se
    caractérise par son ouverture. Une prise dont les premières minutes sont
    silencieuses garde toutefois sa chance avec une fenêtre suivante.
    """
    window = int(NAMING_SAMPLE_S * CAPTURE_RATE)
    for index in range(NAMING_WINDOWS):
        start = index * window
        if start >= samples.size:
            break
        chunk = samples[start:start + window]
        text, speech_s = transcribe(model, to_whisper_input(chunk))
        if text.strip():
            return text, speech_s
    return "", 0.0


def decide_name(samples, model, title_fn=make_title_tracked,
                threshold_s: float = 12.0) -> tuple[str, str]:
    """Décide (slug, origine) pour un signal déjà lu. Sans I/O ni renommage.

    Partagée avec les outils hors interface, pour que la reconnaissance y soit
    la même que dans l'application plutôt qu'une copie qui divergerait.
    """
    if samples.size == 0:
        # Prise vide (récepteur parti avant le premier bloc) : c'est du
        # silence, pas un échec.
        return SILENCE, ORIGIN_SILENCE

    if looks_like_timecode(samples):
        return ORIGIN_TIMECODE, ORIGIN_TIMECODE

    text, speech_s = _transcribe_sample(model, samples)

    # `title_fn` rend (titre, origine) ; on capte l'origine au passage pour
    # distinguer un titre du modèle d'un repli sur mots-clés.
    seen_origin = ORIGIN_TRANSCRIPT

    def make_title(t: str) -> str:
        nonlocal seen_origin
        title, seen_origin = title_fn(t)
        return title

    slug = choose_name(text, speech_s, make_title, threshold_s=threshold_s)
    origin = ORIGIN_SILENCE if slug == SILENCE else seen_origin

    if slug == SILENCE and rms_dbfs(samples) > AUDIBLE_DBFS:
        # Sonore mais sans parole reconnue : l'appeler « silence » serait faux.
        slug, origin = NO_SPEECH, ORIGIN_NO_SPEECH
    return slug, origin


def name_recording(
    wav_path: Path,
    when: datetime,
    model,
    title_fn=make_title_tracked,
    threshold_s: float = 12.0,
) -> NameResult:
    """Nomme un enregistrement déjà écrit sur disque. Ne perd jamais le fichier."""
    slug, origin = decide_name(_read_wav(wav_path), model, title_fn, threshold_s)
    # Une prise timecode est renommée elle aussi : sinon le rattrapage des
    # orphelins la redétecterait à chaque lancement, indéfiniment.
    return _renamed(wav_path, when, slug, origin)


def rename_take(path: Path, new_text: str, when: datetime) -> Path:
    """Renomme une prise à la demande. Un nom vide laisse le fichier tel quel."""
    slug = slugify(new_text)
    if not slug:
        return path
    target = unique_path(path.parent, build_name(when, slug))
    path.rename(target)
    return target
