import pytest

import numpy as np
from conteur.signal import (
    left_channel, crest_factor, looks_like_timecode, to_whisper_input,
    rms_dbfs, startup_burst,
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
    # Full scale (the amplitude guard lets it through) but a high crest factor:
    # only the crest condition can reject this signal.
    rng = np.random.default_rng(2)
    loud = (rng.normal(0, 8000, 48000)).clip(-32768, 32767).astype(np.int16)
    assert np.max(np.abs(loud.astype(np.float64))) / 32768 > 0.5
    assert crest_factor(loud) > 1.5
    assert looks_like_timecode(loud) is False


def test_silence_is_not_timecode():
    assert looks_like_timecode(np.zeros(4800, dtype=np.int16)) is False


def test_quiet_square_wave_is_not_flagged():
    # A low-level square wave is not LTC: the guard requires an amplitude close
    # to full scale.
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


def test_rms_dbfs_of_full_scale_is_near_zero():
    x = np.full(1000, 32767, dtype=np.int16)
    assert rms_dbfs(x) == pytest.approx(0.0, abs=0.1)


def test_rms_dbfs_of_half_scale_is_near_minus_six():
    x = np.full(1000, 16384, dtype=np.int16)
    assert rms_dbfs(x) == pytest.approx(-6.0, abs=0.2)


def test_rms_dbfs_of_silence_is_floored():
    assert rms_dbfs(np.zeros(1000, dtype=np.int16)) == -120.0


def test_empty_capture_is_not_timecode():
    # An empty take must end up as "silence", not an error: `np.max` on an
    # empty array would raise ValueError.
    assert looks_like_timecode(np.zeros(0, dtype=np.int16)) is False


# --- startup_burst: the transmitter's power-on transient ------------------
#
# A take recorded on a transmitter's own card opens with a block of full-scale
# square wave when the take is the first one after it left the charging case:
# the recording starts before the microphone chain has settled. Measured on
# eleven takes recorded weeks apart, the block is 90-110 ms long, sits at
# 1.00 of full scale and has a crest factor of 1.13 every single time, where
# the quietest ordinary take measures 2.84 — a margin wide enough that the
# threshold is not a tuning knob.


def _quiet(n, amp=300, seed=0):
    return (np.random.default_rng(seed).normal(0, amp, n)).astype(np.int16)


def test_startup_burst_measures_a_saturated_opening():
    """The whole point: how long the transient lasts, not whether it is there.

    A fixed length would eat the start of the takes that do not have one."""
    samples = np.concatenate([_square(4800, amp=32767), _quiet(48000)])
    assert startup_burst(samples, rate=48000) == 4800


def test_startup_burst_spans_a_dip_inside_the_transient():
    """The real transient is not uniformly square: measured on hardware, it
    saturates, dips around 10-20 ms, saturates again, then falls away. Ending
    the measurement at the first window that is not square reported 10 ms for
    a transient that lasts 100, which is the whole burst left in the file."""
    samples = np.concatenate([
        _square(2400, amp=32767),           # 0-50 ms, saturated
        _quiet(480, amp=8000, seed=1),      # 50-60 ms, the dip
        _square(1920, amp=32767),           # 60-100 ms, saturated again
        _quiet(48000),                      # the take itself
    ])
    assert startup_burst(samples, rate=48000) == 4800


def test_startup_burst_is_zero_when_the_take_opens_normally():
    """Takes rolled over mid-session open at speaking level and must be left
    alone — silencing their first 100 ms would remove speech."""
    assert startup_burst(_quiet(48000, amp=3000), rate=48000) == 0


def test_startup_burst_refuses_a_block_longer_than_the_artifact():
    """A whole file of full-scale square is a timecode channel, not this
    defect. Past the cap the answer is "not this", not "silence all of it"."""
    samples = np.concatenate([_square(24000, amp=32767), _quiet(48000)])
    assert startup_burst(samples, rate=48000, max_ms=300) == 0
