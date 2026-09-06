from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pytest

from conteur.job import name_recording, rename_take
from conteur.recorder import write_wav

WHEN = datetime(2026, 9, 6, 14, 32, 8)


@dataclass
class Seg:
    start: float
    end: float
    text: str


class FakeModel:
    def __init__(self, segments):
        self._segments = segments

    def transcribe(self, audio, **kwargs):
        return iter(self._segments), object()


def _wav(tmp_path, samples, name="2026-09-06_143208_sans-nom.wav"):
    path = tmp_path / name
    write_wav(samples, path)
    return path


def _voice(n=48000 * 3):
    rng = np.random.default_rng(1)
    return (rng.normal(0, 2000, n)).clip(-32768, 32767).astype(np.int16)


def _square(n=48000 * 3, period=50):
    t = np.arange(n)
    return np.where((t // (period // 2)) % 2 == 0, 32000, -32000).astype(np.int16)


def test_short_recording_is_named_from_the_transcript(tmp_path):
    path = _wav(tmp_path, _voice())
    model = FakeModel([Seg(0.0, 4.0, "Note pour le chapitre trois")])
    result = name_recording(path, WHEN, model,
                            title_fn=lambda t: ("jamais", "title"))
    assert result.path.name == "2026-09-06_143208_note-pour-le-chapitre-trois.wav"
    assert result.origin == "transcript"
    assert result.path.exists()
    assert not path.exists()


def test_long_recording_is_named_from_the_title(tmp_path):
    path = _wav(tmp_path, _voice())
    model = FakeModel([Seg(0.0, 40.0, "il etait une fois")])
    result = name_recording(
        path, WHEN, model,
        title_fn=lambda t: ("Le loup et les chevreaux", "title"))
    assert result.path.name == "2026-09-06_143208_le-loup-et-les-chevreaux.wav"
    assert result.origin == "title"


def test_keyword_fallback_is_reported_as_such(tmp_path):
    path = _wav(tmp_path, _voice())
    model = FakeModel([Seg(0.0, 40.0, "il etait une fois")])
    result = name_recording(path, WHEN, model,
                            title_fn=lambda t: ("loup chevreaux", "keywords"))
    assert result.origin == "keywords"
    assert result.path.name == "2026-09-06_143208_loup-chevreaux.wav"


def test_audible_without_speech_is_not_called_silence(tmp_path):
    """Mesuré sur du matériel réel : des prises culminant à -0,1 dBFS étaient
    nommées « silence » parce que le VAD n'y trouvait pas de parole."""
    path = _wav(tmp_path, _voice())          # bruit fort, aucune parole reconnue
    result = name_recording(path, WHEN, FakeModel([]),
                            title_fn=lambda t: ("x", "title"))
    assert result.slug == "sans-parole"
    assert result.origin == "sans-parole"
    assert result.path.exists()


def test_true_silence_is_still_called_silence(tmp_path):
    import numpy as np

    path = _wav(tmp_path, np.zeros(48000, dtype=np.int16))
    result = name_recording(path, WHEN, FakeModel([]),
                            title_fn=lambda t: ("x", "title"))
    assert result.origin == "silence"
    assert result.path.exists()


def test_timecode_channel_is_refused_and_file_kept(tmp_path):
    path = _wav(tmp_path, _square())
    result = name_recording(path, WHEN, FakeModel([Seg(0.0, 4.0, "bruit")]),
                            title_fn=lambda t: ("x", "title"))
    assert result.origin == "timecode"
    # Renommée, pas laissée sous son nom provisoire : sinon elle serait
    # redétectée comme orpheline à chaque lancement. L'audio est conservé.
    assert result.path.name == "2026-09-06_143208_timecode.wav"
    assert result.path.exists()
    assert not path.exists()


def test_collision_gets_a_suffix(tmp_path):
    (tmp_path / "2026-09-06_143208_bonjour.wav").touch()
    path = _wav(tmp_path, _voice(), name="provisoire.wav")
    model = FakeModel([Seg(0.0, 2.0, "Bonjour")])
    result = name_recording(path, WHEN, model,
                            title_fn=lambda t: ("x", "title"))
    assert result.path.name == "2026-09-06_143208_bonjour-2.wav"


def test_rename_take_slugifies_and_keeps_the_timestamp(tmp_path):
    path = tmp_path / "2026-09-06_143208_ancien.wav"
    path.touch()
    out = rename_take(path, "Le Loup Gris !", WHEN)
    assert out.name == "2026-09-06_143208_le-loup-gris.wav"
    assert out.exists()
    assert not path.exists()


def test_rename_take_avoids_collision(tmp_path):
    (tmp_path / "2026-09-06_143208_cible.wav").touch()
    path = tmp_path / "2026-09-06_143208_source.wav"
    path.touch()
    out = rename_take(path, "cible", WHEN)
    assert out.name == "2026-09-06_143208_cible-2.wav"


def test_rename_take_rejects_an_empty_name(tmp_path):
    path = tmp_path / "2026-09-06_143208_garde.wav"
    path.touch()
    assert rename_take(path, "!!!", WHEN) == path
    assert path.exists()


def test_empty_capture_is_named_silence_not_failed(tmp_path):
    # Récepteur parti avant le premier bloc : la prise est vide. Elle doit
    # ressortir en « silence », pas faire lever le travail de nommage — la
    # ligne afficherait alors « échec ».
    path = _wav(tmp_path, np.zeros(0, dtype=np.int16))
    result = name_recording(path, WHEN, FakeModel([]),
                            title_fn=lambda t: ("x", "title"))
    assert result.origin == "silence"
    assert result.path.exists()
    assert result.path.name == "2026-09-06_143208_silence.wav"


# --- on n'écoute que le début pour nommer ---


class RecordingModel:
    """Doublure qui note la durée de ce qu'on lui donne à transcrire."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.durations = []

    def transcribe(self, audio, **kwargs):
        self.durations.append(len(audio) / 16000)
        segs = self.replies.pop(0) if self.replies else []
        return iter(segs), object()


def test_only_the_head_is_transcribed(tmp_path):
    """Transcrire une heure d'audio pour produire trois mots est un gâchis."""
    import numpy as np

    from conteur.job import NAMING_SAMPLE_S

    long_take = np.zeros(48000 * 600, dtype=np.int16)      # 10 minutes
    long_take[::7] = 8000                                  # sonore
    path = _wav(tmp_path, long_take)
    model = RecordingModel([[Seg(0.0, 30.0, "Il etait une fois")]])

    name_recording(path, WHEN, model, title_fn=lambda t: ("La licorne", "title"))

    assert len(model.durations) == 1
    assert model.durations[0] == pytest.approx(NAMING_SAMPLE_S, abs=1.0)


def test_a_silent_opening_gets_a_second_window(tmp_path):
    """Une prise dont le début est muet garde sa chance."""
    import numpy as np

    long_take = np.zeros(48000 * 600, dtype=np.int16)
    long_take[::7] = 8000
    path = _wav(tmp_path, long_take)
    model = RecordingModel([[], [Seg(0.0, 4.0, "Bonjour")]])

    result = name_recording(path, WHEN, model, title_fn=lambda t: ("x", "title"))

    assert len(model.durations) == 2
    assert result.slug == "bonjour"


def test_a_short_take_is_transcribed_whole(tmp_path):
    import numpy as np

    short = np.zeros(48000 * 5, dtype=np.int16)
    short[::7] = 8000
    path = _wav(tmp_path, short)
    model = RecordingModel([[Seg(0.0, 4.0, "Bonjour")]])

    name_recording(path, WHEN, model, title_fn=lambda t: ("x", "title"))

    assert model.durations[0] == pytest.approx(5.0, abs=0.5)
