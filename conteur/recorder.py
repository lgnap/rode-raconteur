"""Audio capture and WAV writing."""

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
    """Capture until `stop`, return the left channel as int16.

    A failed read (receiver unplugged) stops cleanly and returns what was
    already captured: no audio is lost.

    `on_block`, when given, is called once per captured block with the left
    channel alone (the right channel may carry full-scale timecode, useless as
    an input level). An exception raised by that callback must not lose the
    take in progress.
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
            # The left channel is extracted block by block: otherwise a short
            # read with an odd sample count would swap left and right for
            # everything that follows, and the file would end up on the
            # timecode channel.
            left = left_channel(np.frombuffer(raw, dtype=np.int16))
            blocks.append(left)
            if on_block is not None:
                try:
                    on_block(left)
                except Exception:
                    # A failing UI callback must not lose the take in progress.
                    pass
    finally:
        # On a device that physically vanished, both stopping and closing can
        # raise: the end of the stream must never take the take with it.
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
    if not blocks:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(blocks)


def write_wav(samples: np.ndarray, path: Path, rate: int = CAPTURE_RATE) -> None:
    """Write a mono int16 WAV."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
