"""Lecture des formats WAV que produit le matériel, pas seulement les nôtres."""

import struct
import wave

import numpy as np
import pytest

from conteur.wavread import read_samples


def _write_riff(path, tag, bits, channels, rate, payload):
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(payload)) + payload
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


def test_reads_our_own_16_bit_files(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(np.array([0, 1000, -1000], dtype=np.int16).tobytes())
    assert read_samples(path).tolist() == [0, 1000, -1000]


def test_reads_32_bit_float_which_the_wave_module_refuses(tmp_path):
    """Le format des enregistrements embarqués RØDE. `wave` lève dessus."""
    path = tmp_path / "float.wav"
    _write_riff(path, 3, 32, 1, 48000,
                np.array([0.0, 0.5, -0.5], dtype="<f4").tobytes())

    with pytest.raises(wave.Error):
        wave.open(str(path), "rb")

    out = read_samples(path)
    assert out.dtype == np.int16
    assert out.tolist() == [0, 16384, -16384]


def test_float_above_full_scale_is_clipped_not_wrapped(tmp_path):
    path = tmp_path / "hot.wav"
    _write_riff(path, 3, 32, 1, 48000, np.array([2.0, -2.0], dtype="<f4").tobytes())
    out = read_samples(path)
    assert out.tolist() == [32767, -32768]


def test_reads_24_bit_pcm(tmp_path):
    path = tmp_path / "p24.wav"
    # 0x000000, 0x004000 (0.5), 0xFFC000 (-0.5), petit-boutiste sur 3 octets
    payload = bytes([0, 0, 0]) + bytes([0, 0x00, 0x40]) + bytes([0, 0x00, 0xC0])
    _write_riff(path, 1, 24, 1, 48000, payload)
    assert read_samples(path).tolist() == [0, 16384, -16384]


def test_only_the_first_channel_is_kept(tmp_path):
    path = tmp_path / "stereo.wav"
    interleaved = np.array([1, -1, 2, -2, 3, -3], dtype="<i2").tobytes()
    _write_riff(path, 1, 16, 2, 48000, interleaved)
    assert read_samples(path).tolist() == [1, 2, 3]


def test_extensible_resolves_to_its_subformat(tmp_path):
    path = tmp_path / "ext.wav"
    fmt = struct.pack("<HHIIHH", 0xFFFE, 1, 48000, 48000 * 4, 4, 32)
    # cbSize, wValidBitsPerSample, dwChannelMask, puis le GUID SubFormat :
    # le format réel occupe ses deux premiers octets, à l'offset 24 du chunk.
    fmt += struct.pack("<HHI", 22, 32, 0x4)
    fmt += struct.pack("<H", 3) + b"\x00" * 14
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    payload = np.array([0.25], dtype="<f4").tobytes()
    body += b"data" + struct.pack("<I", len(payload)) + payload
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    assert read_samples(path).tolist() == [8192]


def test_an_unsupported_format_says_so(tmp_path):
    path = tmp_path / "bad.wav"
    _write_riff(path, 99, 12, 1, 48000, b"\0\0")
    with pytest.raises(ValueError, match="non pris en charge"):
        read_samples(path)


def test_a_file_that_is_not_wave_is_refused(tmp_path):
    path = tmp_path / "nope.wav"
    path.write_bytes(b"pas du tout un RIFF")
    with pytest.raises(ValueError):
        read_samples(path)
