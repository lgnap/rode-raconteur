"""Chargement des bibliothèques CUDA livrées par les paquets pip `nvidia-*`.

CTranslate2 ouvre `libcublas.so.12` par `dlopen` au premier encodage, pas au
chargement du modèle — un GPU visible ne garantit donc rien. Sur une machine où
CUDA vient des paquets pip plutôt que de la distribution, ces bibliothèques ne
sont pas sur le chemin de l'éditeur de liens, et `LD_LIBRARY_PATH` est lu au
démarrage du processus, donc trop tard pour être corrigé depuis Python.

La seule voie qui fonctionne en cours d'exécution est de les précharger en
RTLD_GLOBAL : le `dlopen` ultérieur les trouve déjà en mémoire.
"""

import ctypes
import pathlib

CUBLAS = "libcublas.so.12"


def nvidia_library_dirs() -> list[pathlib.Path]:
    """Répertoires de bibliothèques des paquets pip `nvidia-*`, s'ils existent."""
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
    """Précharge les bibliothèques CUDA. Rend True si cuBLAS est disponible.

    Ne lève jamais : une machine sans GPU est un cas normal, pas une erreur.
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
        # cuBLAS peut aussi venir de la distribution.
        try:
            ctypes.CDLL(CUBLAS, mode=ctypes.RTLD_GLOBAL)
            available = True
        except OSError:
            pass
    return available
