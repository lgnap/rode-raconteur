"""Preloading the CUDA libraries shipped by the nvidia-* pip packages."""

import ctypes
import pathlib

from conteur import cuda


def test_no_nvidia_package_is_not_an_error(monkeypatch):
    # Une machine sans GPU est un cas normal, pas une panne.
    monkeypatch.setattr(cuda, "nvidia_library_dirs", lambda: [])
    monkeypatch.setattr(ctypes, "CDLL", _raising)
    assert cuda.preload_cuda_libraries() is False


def _raising(*_a, **_kw):
    raise OSError("absent")


def test_cublas_found_in_a_pip_directory(monkeypatch, tmp_path):
    (tmp_path / cuda.CUBLAS).write_bytes(b"")
    (tmp_path / "libautre.so.1").write_bytes(b"")
    monkeypatch.setattr(cuda, "nvidia_library_dirs", lambda: [tmp_path])
    loaded = []
    monkeypatch.setattr(ctypes, "CDLL", lambda p, mode=0: loaded.append(pathlib.Path(p).name))
    assert cuda.preload_cuda_libraries() is True
    assert cuda.CUBLAS in loaded


def test_a_library_that_will_not_load_is_skipped(monkeypatch, tmp_path):
    (tmp_path / "libcasse.so.1").write_bytes(b"")
    monkeypatch.setattr(cuda, "nvidia_library_dirs", lambda: [tmp_path])
    monkeypatch.setattr(ctypes, "CDLL", _raising)
    assert cuda.preload_cuda_libraries() is False


def test_system_cublas_is_tried_when_pip_has_none(monkeypatch):
    monkeypatch.setattr(cuda, "nvidia_library_dirs", lambda: [])
    monkeypatch.setattr(ctypes, "CDLL", lambda p, mode=0: None)
    assert cuda.preload_cuda_libraries() is True
