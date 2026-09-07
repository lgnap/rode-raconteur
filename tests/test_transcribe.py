from dataclasses import dataclass

import numpy as np
import pytest

from conteur.transcribe import speech_duration, transcribe


@dataclass
class Seg:
    start: float
    end: float
    text: str = ""


def test_speech_duration_sums_segments():
    assert speech_duration([Seg(0.0, 2.5), Seg(10.0, 13.5)]) == pytest.approx(6.0)


def test_speech_duration_of_nothing_is_zero():
    assert speech_duration([]) == 0.0


class FakeModel:
    def __init__(self, segments):
        self._segments = segments
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(self._segments), object()


def test_transcribe_joins_text_and_reports_speech_duration():
    model = FakeModel([Seg(0.0, 1.0, " Il etait "), Seg(2.0, 4.0, "une fois ")])
    text, speech = transcribe(model, np.zeros(16000, dtype=np.float32))
    assert text == "Il etait une fois"
    assert speech == pytest.approx(3.0)


def test_transcribe_requests_french_and_vad():
    model = FakeModel([])
    transcribe(model, np.zeros(16000, dtype=np.float32))
    assert model.kwargs["language"] == "fr"
    assert model.kwargs["vad_filter"] is True


def test_transcribe_of_silence_yields_empty_text():
    text, speech = transcribe(FakeModel([]), np.zeros(16000, dtype=np.float32))
    assert text == ""
    assert speech == 0.0


# --- device selection ---


class _FakeWhisperModel:
    calls = []

    def __init__(self, name, device=None, compute_type=None):
        _FakeWhisperModel.calls.append((name, device, compute_type))


def _install_fake_whisper(monkeypatch):
    import sys
    import types

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _FakeWhisperModel
    _FakeWhisperModel.calls = []
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def test_the_model_goes_to_the_gpu_when_cublas_is_loadable(monkeypatch):
    import conteur.cuda as cuda_mod
    import conteur.transcribe as t

    _install_fake_whisper(monkeypatch)
    monkeypatch.setattr(cuda_mod, "preload_cuda_libraries", lambda: True)
    t.load_model()
    assert _FakeWhisperModel.calls == [("large-v3", "cuda", "int8_float16")]
    assert t.chosen_device() == "cuda"


def test_the_model_falls_back_to_cpu_without_cublas(monkeypatch):
    """Without cuBLAS, building on the GPU would fail at the first encode, in
    mid-transcription — so long after loading."""
    import conteur.cuda as cuda_mod
    import conteur.transcribe as t

    _install_fake_whisper(monkeypatch)
    monkeypatch.setattr(cuda_mod, "preload_cuda_libraries", lambda: False)
    t.load_model()
    assert _FakeWhisperModel.calls == [("large-v3", "cpu", "int8")]
    assert t.chosen_device() == "cpu"


def test_the_vad_threshold_is_loosened():
    """The default Silero threshold (0.5) rejected short takes that were
    audible nonetheless, wrongly named "silence"."""
    from conteur.transcribe import VAD_PARAMETERS

    assert VAD_PARAMETERS["threshold"] < 0.5


def test_transcribe_passes_the_vad_parameters():
    model = FakeModel([])
    transcribe(model, np.zeros(16000, dtype=np.float32))
    assert model.kwargs["vad_parameters"]["threshold"] < 0.5
