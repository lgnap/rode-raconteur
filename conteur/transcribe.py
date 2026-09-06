"""Frontière Whisper. Le modèle est injecté partout sauf dans load_model()."""

from collections.abc import Iterable

import numpy as np

MODEL_NAME = "large-v3"
DEVICE = "cuda"
COMPUTE_TYPE = "int8_float16"
LANGUAGE = "fr"


def speech_duration(segments: Iterable) -> float:
    """Somme des durées des segments de parole rendus par le VAD."""
    return float(sum(s.end - s.start for s in segments))


def transcribe(model, audio16k: np.ndarray) -> tuple[str, float]:
    """Rend (texte, durée de parole). La durée vient du VAD, pas du fichier."""
    segments, _info = model.transcribe(
        audio16k, language=LANGUAGE, vad_filter=True,
    )
    segments = list(segments)
    text = " ".join(s.text.strip() for s in segments if s.text.strip())
    return text.strip(), speech_duration(segments)


def load_model():
    """Charge large-v3 en int8_float16 sur GPU. Appelé une fois au démarrage."""
    from faster_whisper import WhisperModel

    return WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
