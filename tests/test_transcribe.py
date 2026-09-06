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
