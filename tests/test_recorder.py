import threading
import wave

import numpy as np

from conteur.recorder import record, write_wav
from conteur.rx_device import RxDevice


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def read(self, _frames, exception_on_overflow=True):
        if not self._chunks:
            raise OSError("stream exhausted")
        return self._chunks.pop(0)

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class FakePyAudio:
    def __init__(self, stream):
        self._stream = stream
        self.opened_with = None

    def open(self, **kwargs):
        self.opened_with = kwargs
        return self._stream

    def get_format_from_width(self, width):
        return width


def test_record_returns_left_channel_only():
    interleaved = np.array([1, -1, 2, -2, 3, -3, 4, -4], dtype=np.int16)
    stream = FakeStream([interleaved.tobytes()])
    pa = FakePyAudio(stream)
    stop = threading.Event()

    def stop_after_first_read(_frames, exception_on_overflow=True):
        stop.set()
        return interleaved.tobytes()

    stream.read = stop_after_first_read
    out = record(pa, RxDevice(0, "Wireless PRO RX"), stop)
    assert out.tolist() == [1, 2, 3, 4]


def test_record_opens_stereo_48k_on_the_right_device():
    stream = FakeStream([])
    pa = FakePyAudio(stream)
    stop = threading.Event()
    stop.set()
    record(pa, RxDevice(7, "Wireless PRO RX"), stop)
    assert pa.opened_with["rate"] == 48000
    assert pa.opened_with["channels"] == 2
    assert pa.opened_with["input_device_index"] == 7
    assert pa.opened_with["input"] is True
    assert stream.closed is True


def test_write_wav_roundtrip(tmp_path):
    samples = np.array([0, 1000, -1000, 32767], dtype=np.int16)
    path = tmp_path / "out.wav"
    write_wav(samples, path)
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 48000
        assert np.frombuffer(w.readframes(w.getnframes()),
                             dtype=np.int16).tolist() == samples.tolist()


def test_record_calls_on_block_with_mono_left_channel_per_chunk():
    chunk1 = np.array([1, -1, 2, -2], dtype=np.int16)
    chunk2 = np.array([3, -3, 4, -4], dtype=np.int16)
    stream = FakeStream([chunk1.tobytes(), chunk2.tobytes()])
    pa = FakePyAudio(stream)
    calls = []

    out = record(
        pa, RxDevice(0, "Wireless PRO RX"), threading.Event(),
        on_block=lambda block: calls.append(block.tolist()),
    )

    # Le canal droit (timecode potentiel, plein niveau) n'est jamais transmis
    # au rappel : seul le gauche, celui qui atterrit dans le fichier.
    assert calls == [[1, 2], [3, 4]]
    assert out.tolist() == [1, 2, 3, 4]


def test_record_survives_a_raising_on_block():
    chunk1 = np.array([1, -1, 2, -2], dtype=np.int16)
    chunk2 = np.array([3, -3, 4, -4], dtype=np.int16)
    stream = FakeStream([chunk1.tobytes(), chunk2.tobytes()])
    pa = FakePyAudio(stream)

    def boom(_block):
        raise RuntimeError("le rappel casse")

    out = record(pa, RxDevice(0, "Wireless PRO RX"), threading.Event(), on_block=boom)

    # Le rappel a échoué deux fois, mais la capture n'a rien perdu.
    assert out.tolist() == [1, 2, 3, 4]


def test_record_keeps_audio_when_the_receiver_disappears():
    # Le récepteur est débranché en cours de prise : la lecture lève, la boucle
    # s'arrête, et ce qui a déjà été capté est conservé.
    interleaved = np.array([1, -1, 2, -2], dtype=np.int16)
    reads = []

    def read_then_fail(_frames, exception_on_overflow=True):
        if reads:
            raise OSError("device disconnected")
        reads.append(1)
        return interleaved.tobytes()

    stream = FakeStream([])
    stream.read = read_then_fail
    pa = FakePyAudio(stream)

    # `stop` n'est jamais armé : seule l'erreur de lecture peut sortir de la boucle.
    out = record(pa, RxDevice(0, "Wireless PRO RX"), threading.Event())

    assert out.tolist() == [1, 2]
    assert stream.closed is True
