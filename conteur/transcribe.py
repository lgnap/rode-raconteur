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


CPU_COMPUTE_TYPE = "int8"

_last_device = None


def chosen_device() -> str | None:
    """Périphérique réellement retenu au dernier `load_model()`, ou None."""
    return _last_device


def load_model():
    """Charge large-v3, sur GPU si cuBLAS est chargeable, sinon sur CPU.

    Appelé une fois au démarrage. `chosen_device()` dit ce qui a été retenu.
    """
    global _last_device
    from faster_whisper import WhisperModel

    from conteur.cuda import preload_cuda_libraries

    # CTranslate2 ouvre cuBLAS par dlopen au premier encodage : construire le
    # modèle sur GPU sans lui échouerait plus tard, en pleine transcription.
    if preload_cuda_libraries():
        _last_device = DEVICE
        return WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)

    _last_device = "cpu"
    return WhisperModel(MODEL_NAME, device="cpu", compute_type=CPU_COMPUTE_TYPE)
