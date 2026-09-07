"""The Whisper boundary. The model is injected everywhere but load_model()."""

from collections.abc import Iterable

import numpy as np

MODEL_NAME = "large-v3"
DEVICE = "cuda"
COMPUTE_TYPE = "int8_float16"
LANGUAGE = "fr"

# The default Silero threshold (0.5) rejects short takes that are audible
# nonetheless: measured on real material, it returned "silence" for files
# peaking at -0.1 dBFS. At 0.2 their content is found again.
VAD_PARAMETERS = {"threshold": 0.2}


def speech_duration(segments: Iterable) -> float:
    """Sum of the durations of the speech segments returned by the VAD."""
    return float(sum(s.end - s.start for s in segments))


def transcribe(model, audio16k: np.ndarray) -> tuple[str, float]:
    """Return (text, speech duration). The duration comes from the VAD, not
    from the file."""
    segments, _info = model.transcribe(
        audio16k, language=LANGUAGE, vad_filter=True,
        vad_parameters=dict(VAD_PARAMETERS),
    )
    segments = list(segments)
    text = " ".join(s.text.strip() for s in segments if s.text.strip())
    return text.strip(), speech_duration(segments)


CPU_COMPUTE_TYPE = "int8"

_last_device = None


def chosen_device() -> str | None:
    """The device actually used by the last `load_model()`, or None."""
    return _last_device


def load_model():
    """Load large-v3, on GPU if cuBLAS can be loaded, on CPU otherwise.

    Called once at startup. `chosen_device()` says what was used.
    """
    global _last_device
    from faster_whisper import WhisperModel

    from conteur.cuda import preload_cuda_libraries

    # CTranslate2 opens cuBLAS with dlopen at the first encode: building the
    # model on the GPU without it would fail later, mid-transcription.
    if preload_cuda_libraries():
        _last_device = DEVICE
        return WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)

    _last_device = "cpu"
    return WhisperModel(MODEL_NAME, device="cpu", compute_type=CPU_COMPUTE_TYPE)
