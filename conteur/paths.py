"""Emplacement et nommage des fichiers. `runner` est injectable pour les tests."""

import subprocess
from datetime import datetime
from pathlib import Path

FOLDER = "Enregistrements"


def music_dir(runner=subprocess.run) -> Path:
    """Dossier Musique XDG, résolu à l'exécution (jamais codé en dur)."""
    try:
        out = runner(
            ["xdg-user-dir", "MUSIC"], capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        out = ""
    return Path(out) if out else Path.home() / "Music"


def destination_dir(when: datetime, runner=subprocess.run) -> Path:
    """<Musique>/Enregistrements/AAAA-MM, créé au besoin."""
    directory = music_dir(runner=runner) / FOLDER / when.strftime("%Y-%m")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_name(when: datetime, slug: str) -> str:
    return f"{when.strftime('%Y-%m-%d_%H%M%S')}_{slug}.wav"


def unique_path(directory: Path, filename: str) -> Path:
    """Ajoute -2, -3, … tant que le nom est pris."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    n = 2
    while (directory / f"{stem}-{n}{suffix}").exists():
        n += 1
    return directory / f"{stem}-{n}{suffix}"
