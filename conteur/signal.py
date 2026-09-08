"""Signal processing. No I/O."""

import numpy as np
from scipy.signal import resample_poly

FULL_SCALE = 32768.0
CAPTURE_RATE = 48000
WHISPER_RATE = 16000
CREST_SQUARE_MAX = 1.5
TIMECODE_MIN_PEAK = 0.5
DBFS_FLOOR = -120.0


def left_channel(interleaved: np.ndarray) -> np.ndarray:
    """Extract the left channel of an interleaved stereo stream."""
    return np.ascontiguousarray(interleaved[0::2])


def crest_factor(samples: np.ndarray) -> float:
    """Peak-to-RMS ratio. ~1 for a square wave, >4 for speech."""
    x = samples.astype(np.float64)
    rms = float(np.sqrt(np.mean(x * x)))
    if rms == 0.0:
        return 0.0
    return float(np.max(np.abs(x))) / rms


def looks_like_timecode(samples: np.ndarray) -> bool:
    """True if the channel carries a full-scale binary signal, not a voice."""
    if samples.size == 0:
        # An empty capture is not timecode: it is silence, and `np.max` over
        # an empty array would raise.
        return False
    peak = float(np.max(np.abs(samples.astype(np.float64)))) / FULL_SCALE
    if peak < TIMECODE_MIN_PEAK:
        return False
    return crest_factor(samples) < CREST_SQUARE_MAX


def startup_burst(samples: np.ndarray, rate: int = CAPTURE_RATE,
                  window_ms: int = 10, max_ms: int = 300) -> int:
    """How many frames of power-on transient a take opens with, 0 if none.

    A transmitter starts recording the instant it leaves the charging case,
    before its microphone chain has settled, and the first take of an outing
    therefore opens with a block of full-scale square wave. Measured on eleven
    takes recorded weeks apart: 90-110 ms, peak 1.00 of full scale, crest
    factor 1.13 every time. The quietest ordinary take measures 2.84, so the
    threshold separates two populations rather than splitting one.

    The length is measured rather than assumed because the takes that do *not*
    have it open at speaking level: a fixed trim would cut their first words.
    Takes rolled over mid-session, when a recording reaches the file size
    limit, are exactly that case.

    Past `max_ms` the answer is 0, not "silence all of it": a signal that stays
    square for a third of a second is a timecode channel, which has the same
    signature and none of the same meaning.
    """
    window = max(1, int(rate * window_ms / 1000))
    limit = min(int(rate * max_ms / 1000), samples.size)

    def peak_of(chunk) -> float:
        return float(np.max(np.abs(chunk.astype(np.float64)))) / FULL_SCALE

    if samples.size < window:
        return 0
    opening = samples[:window]
    if peak_of(opening) <= TIMECODE_MIN_PEAK or crest_factor(opening) >= CREST_SQUARE_MAX:
        return 0

    # The signature is read once, at the opening; the extent is then the last
    # window still at full scale. Requiring every window to be square ended
    # the measurement at the first dip — and the real transient dips around
    # 10-20 ms before saturating again, so that reported 10 ms of a 100 ms
    # burst and left the rest of it in the file.
    last = frames = 0
    while frames + window <= limit:
        if peak_of(samples[frames:frames + window]) > TIMECODE_MIN_PEAK:
            last = frames + window
        frames += window
    return 0 if last >= limit else last


def to_whisper_input(samples: np.ndarray) -> np.ndarray:
    """48 kHz int16 -> 16 kHz float32 in [-1, 1]."""
    scaled = samples.astype(np.float32) / FULL_SCALE
    return resample_poly(scaled, WHISPER_RATE, CAPTURE_RATE).astype(np.float32)


def rms_dbfs(samples: np.ndarray) -> float:
    """RMS level in dBFS, floored at -120 for digital silence."""
    if samples.size == 0:
        return DBFS_FLOOR
    x = samples.astype(np.float64) / FULL_SCALE
    rms = float(np.sqrt(np.mean(x * x)))
    if rms <= 0.0:
        return DBFS_FLOOR
    return max(DBFS_FLOOR, 20.0 * np.log10(rms))
