"""Traitement du signal. Aucune I/O."""

import numpy as np
from scipy.signal import resample_poly

FULL_SCALE = 32768.0
CAPTURE_RATE = 48000
WHISPER_RATE = 16000
CREST_SQUARE_MAX = 1.5
TIMECODE_MIN_PEAK = 0.5
DBFS_FLOOR = -120.0


def left_channel(interleaved: np.ndarray) -> np.ndarray:
    """Extrait le canal gauche d'un flux stéréo entrelacé."""
    return np.ascontiguousarray(interleaved[0::2])


def crest_factor(samples: np.ndarray) -> float:
    """Rapport crête sur RMS. ~1 pour un carré, >4 pour de la parole."""
    x = samples.astype(np.float64)
    rms = float(np.sqrt(np.mean(x * x)))
    if rms == 0.0:
        return 0.0
    return float(np.max(np.abs(x))) / rms


def looks_like_timecode(samples: np.ndarray) -> bool:
    """Vrai si le canal porte un signal binaire pleine échelle, pas de la voix."""
    if samples.size == 0:
        # Une capture vide n'est pas du timecode : c'est du silence, et
        # `np.max` sur un tableau vide lèverait.
        return False
    peak = float(np.max(np.abs(samples.astype(np.float64)))) / FULL_SCALE
    if peak < TIMECODE_MIN_PEAK:
        return False
    return crest_factor(samples) < CREST_SQUARE_MAX


def to_whisper_input(samples: np.ndarray) -> np.ndarray:
    """48 kHz int16 -> 16 kHz float32 dans [-1, 1]."""
    scaled = samples.astype(np.float32) / FULL_SCALE
    return resample_poly(scaled, WHISPER_RATE, CAPTURE_RATE).astype(np.float32)


def rms_dbfs(samples: np.ndarray) -> float:
    """Niveau RMS en dBFS, plancher à -120 pour le silence numérique."""
    if samples.size == 0:
        return DBFS_FLOOR
    x = samples.astype(np.float64) / FULL_SCALE
    rms = float(np.sqrt(np.mean(x * x)))
    if rms <= 0.0:
        return DBFS_FLOOR
    return max(DBFS_FLOOR, 20.0 * np.log10(rms))
