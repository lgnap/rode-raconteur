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
    """Measured on real material: takes peaking at -0.1 dBFS were named
    "silence" because the VAD found no speech in them."""
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
    # Renamed, not left under its provisional name: otherwise it would be
    # detected as an orphan at every launch. The audio is kept.
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
    # Receiver gone before the first block: the take is empty. It must come
    # out as "silence", not make the naming job raise — the row would then
    # display "failed".
    path = _wav(tmp_path, np.zeros(0, dtype=np.int16))
    result = name_recording(path, WHEN, FakeModel([]),
                            title_fn=lambda t: ("x", "title"))
    assert result.origin == "silence"
    assert result.path.exists()
    assert result.path.name == "2026-09-06_143208_silence.wav"


# --- only the beginning is listened to for naming ---


class RecordingModel:
    """Stand-in that records the duration of what it is given to transcribe."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.durations = []

    def transcribe(self, audio, **kwargs):
        self.durations.append(len(audio) / 16000)
        segs = self.replies.pop(0) if self.replies else []
        return iter(segs), object()


def test_only_the_head_is_transcribed(tmp_path, monkeypatch):
    """Transcribing an hour of audio to produce three words is a waste.

    The cap is lowered in the test: what is verified is that it is honoured,
    not its current value.
    """
    import conteur.job as job

    monkeypatch.setattr(job, "NAMING_SAMPLE_S", 30.0)
    long_take = np.zeros(48000 * 120, dtype=np.int16)      # 2 minutes
    long_take[::7] = 8000                                  # sonore
    path = _wav(tmp_path, long_take)
    model = RecordingModel([[Seg(0.0, 20.0, "Il etait une fois")]])

    name_recording(path, WHEN, model, title_fn=lambda t: ("La licorne", "title"))

    assert len(model.durations) == 1
    assert model.durations[0] == pytest.approx(30.0, abs=1.0)


def test_a_silent_opening_gets_a_second_window(tmp_path, monkeypatch):
    """A take whose beginning is silent keeps its chance."""
    import conteur.job as job

    monkeypatch.setattr(job, "NAMING_SAMPLE_S", 30.0)
    long_take = np.zeros(48000 * 120, dtype=np.int16)
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


def test_the_audible_threshold_leaves_room_below_a_faint_take(tmp_path):
    """Measured on a real field take: -51.6 dBFS RMS with a -15 dBFS peak, so
    genuinely audible, was named "silence" under a -50 threshold."""
    from conteur.job import AUDIBLE_DBFS

    assert AUDIBLE_DBFS <= -55.0


def test_naming_an_import_keeps_the_name_the_card_carried(tmp_path):
    """Decision 8 keeps the card's name on purpose: it will not be
    reproducible, since the take counter restarts at 00001 after an erase.
    Rebuilding the name from the timestamp and the slug alone threw it away."""
    path = _wav(tmp_path, _voice(),
                name="2026-09-07_111030_00002_Source-Baffle__sans-nom.wav")
    model = FakeModel([Seg(0.0, 4.0, "La licorne")])
    result = name_recording(path, WHEN, model,
                            title_fn=lambda t: ("jamais", "title"))
    assert result.path.name == \
        "2026-09-07_111030_00002_Source-Baffle__la-licorne.wav"
    assert not path.exists()


def test_naming_a_split_part_keeps_its_rank_and_its_own_start(tmp_path):
    path = _wav(tmp_path, _voice(),
                name="2026-09-07_111225_00002_Source-Baffle_02_sur_06__sans-nom.wav")
    model = FakeModel([Seg(0.0, 4.0, "Le loup arrive")])
    result = name_recording(path, WHEN, model,
                            title_fn=lambda t: ("jamais", "title"))
    assert result.path.name == \
        "2026-09-07_111225_00002_Source-Baffle_02_sur_06__le-loup-arrive.wav"


def test_naming_a_take_recorded_here_is_unchanged(tmp_path):
    """No `__` segment, so the name is still rebuilt from the timestamp."""
    path = _wav(tmp_path, _voice())
    model = FakeModel([Seg(0.0, 4.0, "La licorne")])
    result = name_recording(path, WHEN, model,
                            title_fn=lambda t: ("jamais", "title"))
    assert result.path.name == "2026-09-06_143208_la-licorne.wav"
