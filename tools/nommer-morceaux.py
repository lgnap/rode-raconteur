#!/usr/bin/env python3
"""Ajoute au nom de fichiers WAV ce que la transcription y reconnaît.

Même reconnaissance que l'application — mêmes modèle, VAD, seuil et règles de
slug — parce que la décision vient de `conteur.job.decide_name`, partagée, et
non d'une copie qui finirait par diverger.

À la différence de l'application, le nom existant est **conservé** : le slug est
ajouté à la suite. C'est ce qui permet de nommer des morceaux découpés aux
marqueurs sans perdre l'indication de ce qui allait ensemble, ni casser leur
recollage.

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
from conteur.paths import unique_path        # noqa: E402
from conteur.transcribe import chosen_device, load_model  # noqa: E402
from conteur.wavread import read_samples     # noqa: E402

# Un slug ajouté par cet outil : uniquement minuscules, chiffres et tirets.
# Compter les « __ » ne suffit pas — un fichier d'appareil n'en a aucun avant
# annotation, un morceau découpé en a déjà un (sa plage horaire).
SLUG = re.compile(r"[a-z0-9-]+")


def deja_annote(stem: str) -> bool:
    return "__" in stem and SLUG.fullmatch(stem.rsplit("__", 1)[1]) is not None


def collect(targets: list[Path]) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        if target.is_dir():
            # Insensible à la casse : l'appareil écrit en .WAV, l'application
            # en .wav, et les deux cohabitent dans la même arborescence.
            found.extend(p for p in sorted(target.rglob("*"))
                         if p.is_file() and p.suffix.lower() == ".wav")
        elif target.is_file():
            found.append(target)
    return [p for p in found if not p.name.startswith(".")]


def annotate(path: Path, model) -> tuple[Path, str] | None:
    """Renomme en ajoutant le slug reconnu. Rend None si déjà annoté."""
    if deja_annote(path.stem):
        return None
    slug, origin = decide_name(read_samples(path), model)
    target = unique_path(path.parent, f"{path.stem}__{slug}{path.suffix}")
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
