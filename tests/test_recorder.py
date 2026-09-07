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

    # The callback failed twice, but the capture lost nothing.
    assert out.tolist() == [1, 2, 3, 4]


def test_record_keeps_audio_when_the_receiver_disappears():
    # The receiver is unplugged mid-take: the read raises, the loop stops, and
    # what was already captured is kept.
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

    # `stop` is never set: only the read error can leave the loop.
    out = record(pa, RxDevice(0, "Wireless PRO RX"), threading.Event())

    assert out.tolist() == [1, 2]
    assert stream.closed is True


def test_channel_parity_survives_a_short_odd_block():
    # A short read with an odd sample count: if the left channel were extracted
    # only once, at the end, everything after it would shift onto the right
    # channel — the one that carries the timecode.
    odd = np.array([1, -1, 2], dtype=np.int16)
    even = np.array([3, -3, 4, -4], dtype=np.int16)
    stream = FakeStream([odd.tobytes(), even.tobytes()])
    pa = FakePyAudio(stream)

    out = record(pa, RxDevice(0, "Wireless PRO RX"), threading.Event())

    assert out.tolist() == [1, 2, 3, 4]


def test_teardown_failure_never_loses_the_capture():
    # Receiver yanked out: `stop_stream` raises, as PortAudio does on a device
    # that has vanished. The audio already captured must survive.
    interleaved = np.array([1, -1, 2, -2], dtype=np.int16)
    reads = []

    def read_then_fail(_frames, exception_on_overflow=True):
        if reads:
            raise OSError("device disconnected")
        reads.append(1)
        return interleaved.tobytes()

    class BrokenStream(FakeStream):
        def stop_stream(self):
            raise OSError("device unplugged")

        def close(self):
            self.closed = True
            raise OSError("device unplugged")

    stream = BrokenStream([])
    stream.read = read_then_fail
    pa = FakePyAudio(stream)

    out = record(pa, RxDevice(0, "Wireless PRO RX"), threading.Event())

    assert out.tolist() == [1, 2]
    assert stream.closed is True
