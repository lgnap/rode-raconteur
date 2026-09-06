"""Capture audio et écriture WAV."""

import threading
import wave
from pathlib import Path

import numpy as np

from conteur.rx_device import RxDevice
from conteur.signal import CAPTURE_RATE, left_channel

CHUNK = 1024
CHANNELS = 2
SAMPLE_WIDTH = 2


def record(pa, device: RxDevice, stop: threading.Event, on_block=None) -> np.ndarray:
    """Capture jusqu'à `stop`, rend le canal gauche en int16.

    Un échec de lecture (récepteur débranché) arrête proprement et rend ce qui
    a déjà été capté : aucun audio n'est perdu.

    `on_block`, si fourni, est appelé une fois par bloc capté avec le canal
    gauche seul (le canal droit peut porter du timecode plein niveau, inutile
    pour un niveau d'entrée). Une exception levée par ce rappel ne doit pas
    faire perdre la prise en cours.
    """
    stream = pa.open(
        format=pa.get_format_from_width(SAMPLE_WIDTH),
        channels=CHANNELS,
        rate=CAPTURE_RATE,
        input=True,
        input_device_index=device.index,
        frames_per_buffer=CHUNK,
    )
    blocks: list[np.ndarray] = []
    try:
        while not stop.is_set():
            try:
                raw = stream.read(CHUNK, exception_on_overflow=False)
            except OSError:
                break
            block = np.frombuffer(raw, dtype=np.int16)
            blocks.append(block)
            if on_block is not None:
                try:
                    on_block(left_channel(block))
                except Exception:
                    # Un rappel d'interface qui échoue ne doit pas faire perdre
                    # la prise en cours.
                    pass
    finally:
        try:
            stream.stop_stream()
        finally:
            stream.close()
    if not blocks:
        return np.zeros(0, dtype=np.int16)
    return left_channel(np.concatenate(blocks))


def write_wav(samples: np.ndarray, path: Path, rate: int = CAPTURE_RATE) -> None:
    """Écrit un WAV mono int16."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
