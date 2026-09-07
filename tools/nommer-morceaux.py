#!/usr/bin/env python3
"""Add to a WAV file name what transcription recognises inside it.

Same recognition as the application — same model, VAD, threshold and slug
rules — because the decision comes from the shared `conteur.job.decide_name`,
not from a copy that would eventually drift.

Unlike the application, the existing name is **kept**: the slug is attached to
it. That is what lets you name parts cut at markers without losing the
indication of what belonged together, or breaking their rejoining. A
provisional `__sans-nom` tail is replaced rather than kept, so a file never
carries the placeholder and a real name at once.

    nommer-morceaux.py DOSSIER_OU_FICHIERS...

SPDX-License-Identifier: MIT
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conteur.job import decide_name          # noqa: E402
from conteur.naming import origin_label      # noqa: E402
from conteur.orphans import is_placeholder   # noqa: E402
from conteur.paths import unique_path        # noqa: E402
from conteur.transcribe import chosen_device, load_model  # noqa: E402
from conteur.wavread import read_samples     # noqa: E402

# A slug appended by this tool: lowercase, digits and hyphens only. Counting
# "__" is not enough — a file straight off the device has none before
# annotation, while a part cut at markers already has one (its time range).
SLUG = re.compile(r"[a-z0-9-]+")


def already_annotated(stem: str) -> bool:
    if "__" not in stem:
        return False
    tail = stem.rsplit("__", 1)[1]
    return SLUG.fullmatch(tail) is not None and not is_placeholder(tail)


def collect(targets: list[Path]) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        if target.is_dir():
            # Case-insensitive: the device writes .WAV, the application
            # writes .wav, and both live in the same tree.
            found.extend(p for p in sorted(target.rglob("*"))
                         if p.is_file() and p.suffix.lower() == ".wav")
        elif target.is_file():
            found.append(target)
    return [p for p in found if not p.name.startswith(".")]


def named(stem: str, slug: str) -> str:
    """The stem with the slug attached, replacing a placeholder if there is one.

    Appending unconditionally kept `__sans-nom` in the middle of the name for
    good — `..._00002_Source-Baffle__sans-nom__la-licorne` — and the
    application produces a different shape from the same file, so whichever ran
    first decided which wrong name you got. `is_placeholder` is the one
    authority on what a provisional tail looks like, shared with orphans.py so
    the two cannot drift.
    """
    if "__" in stem:
        head, tail = stem.rsplit("__", 1)
        if is_placeholder(tail):
            return f"{head}__{slug}"
    return f"{stem}__{slug}"


def annotate(path: Path, model) -> tuple[Path, str] | None:
    """Rename with the recognised slug. Returns None if already done."""
    if already_annotated(path.stem):
        return None
    slug, origin = decide_name(read_samples(path), model)
    target = unique_path(path.parent, f"{named(path.stem, slug)}{path.suffix}")
    path.rename(target)
    return target, origin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cibles", nargs="+", type=Path,
                        help="dossiers ou fichiers WAV")
    args = parser.parse_args()

    files = collect(args.cibles)
    if not files:
        print("aucun fichier WAV trouvé")
        return 1

    model = load_model()
    print(f"modèle sur {chosen_device()}, {len(files)} fichier(s)\n")
    done = skipped = 0
    for path in files:
        try:
            result = annotate(path, model)
        except Exception as error:            # noqa: BLE001
            print(f"  !! {path.name} : {type(error).__name__} : {error}")
            continue
        if result is None:
            print(f"  =  {path.name}  (déjà annoté)")
            skipped += 1
            continue
        target, origin = result
        print(f"  -> {target.name}   [{origin_label(origin)}]")
        done += 1
    print(f"\n{done} renommé(s), {skipped} ignoré(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
