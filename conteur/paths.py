"""Where files live and how they are named. `runner` is injectable for tests."""

import subprocess
from datetime import datetime
from pathlib import Path

# User-visible: this is the folder name on disk, in French like the rest of
# the interface. Renaming it would orphan every take already recorded.
FOLDER = "Enregistrements"


def music_dir(runner=subprocess.run) -> Path:
    """XDG music directory, resolved at run time (never hard-coded)."""
    try:
        out = runner(
            ["xdg-user-dir", "MUSIC"], capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        out = ""
    return Path(out) if out else Path.home() / "Music"


def destination_dir(when: datetime, runner=subprocess.run) -> Path:
    """<Music>/Enregistrements/YYYY-MM, created if needed."""
    directory = music_dir(runner=runner) / FOLDER / when.strftime("%Y-%m")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_name(when: datetime, slug: str) -> str:
    return f"{when.strftime('%Y-%m-%d_%H%M%S')}_{slug}.wav"


def unique_path(directory: Path, filename: str) -> Path:
    """Append -2, -3, … for as long as the name is taken."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    n = 2
    while (directory / f"{stem}-{n}{suffix}").exists():
        n += 1
    return directory / f"{stem}-{n}{suffix}"
