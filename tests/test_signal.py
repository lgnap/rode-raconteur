import numpy as np
from conteur.signal import (
    left_channel, crest_factor, looks_like_timecode, to_whisper_input,
)


def test_left_channel_deinterleaves():
    interleaved = np.array([1, -1, 2, -2, 3, -3], dtype=np.int16)
    assert left_channel(interleaved).tolist() == [1, 2, 3]


def test_left_channel_output_is_contiguous_int16():
    out = left_channel(np.zeros(10, dtype=np.int16))
    assert out.dtype == np.int16
    assert out.flags["C_CONTIGUOUS"]


def _square(n=48000, period=50, amp=32000):
    t = np.arange(n)
    return (np.where((t // (period // 2)) % 2 == 0, amp, -amp)).astype(np.int16)


def test_square_wave_is_detected_as_timecode():
    assert crest_factor(_square()) < 1.5
    assert looks_like_timecode(_square()) is True


def test_noise_is_not_timecode():
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 3000, 48000)).clip(-32768, 32767).astype(np.int16)
    assert crest_factor(noise) > 2.0
    assert looks_like_timecode(noise) is False


def test_loud_noise_is_not_timecode():
    # Pleine échelle (la garde d'amplitude laisse passer) mais facteur de crête
    # élevé : seule la condition de crête peut rejeter ce signal.
    rng = np.random.default_rng(2)
    loud = (rng.normal(0, 8000, 48000)).clip(-32768, 32767).astype(np.int16)
    assert np.max(np.abs(loud.astype(np.float64))) / 32768 > 0.5
    assert crest_factor(loud) > 1.5
    assert looks_like_timecode(loud) is False


def test_silence_is_not_timecode():
    assert looks_like_timecode(np.zeros(4800, dtype=np.int16)) is False


def test_quiet_square_wave_is_not_flagged():
    # Un signal carré de faible niveau n'est pas du LTC : le garde-fou exige
    # une amplitude proche de la pleine échelle.
    assert looks_like_timecode(_square(amp=2000)) is False


def test_to_whisper_input_resamples_to_16k_float32():
    x = np.zeros(48000, dtype=np.int16)
    out = to_whisper_input(x)
    assert out.dtype == np.float32
    assert abs(len(out) - 16000) <= 1


def test_to_whisper_input_scales_to_unit_range():
    x = np.full(4800, 16384, dtype=np.int16)
    out = to_whisper_input(x)
    assert 0.4 < float(np.max(out)) < 0.6
