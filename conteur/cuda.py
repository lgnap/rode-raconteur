"""Loading the CUDA libraries shipped by the `nvidia-*` pip packages.

CTranslate2 opens `libcublas.so.12` with `dlopen` at the first encode, not when
the model is loaded — so a visible GPU guarantees nothing. On a machine where
CUDA comes from pip packages rather than from the distribution, those libraries
are not on the linker path, and `LD_LIBRARY_PATH` is read when the process
starts, hence too late to fix from Python.

The only approach that works at run time is to preload them with RTLD_GLOBAL:
the later `dlopen` then finds them already in memory.
"""

import ctypes
import pathlib

CUBLAS = "libcublas.so.12"


def nvidia_library_dirs() -> list[pathlib.Path]:
    """Library directories of the `nvidia-*` pip packages, if they exist."""
    try:
        import nvidia
    except ImportError:
        return []
    dirs: set[pathlib.Path] = set()
    for root in nvidia.__path__:
        for so in pathlib.Path(root).rglob("lib*.so.*"):
            dirs.add(so.parent)
    return sorted(dirs)


def preload_cuda_libraries() -> bool:
    """Preload the CUDA libraries. Returns True if cuBLAS is available.

    Never raises: a machine without a GPU is a normal case, not an error.
    """
    available = False
    for directory in nvidia_library_dirs():
        for so in sorted(directory.glob("lib*.so.*")):
            try:
                ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue
            if so.name == CUBLAS:
                available = True
    if not available:
        # cuBLAS may also come from the distribution.
        try:
            ctypes.CDLL(CUBLAS, mode=ctypes.RTLD_GLOBAL)
            available = True
        except OSError:
            pass
    return available
