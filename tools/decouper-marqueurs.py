#!/usr/bin/env python3
"""Découpe un enregistrement RØDE à ses marqueurs — sans perte, sans dépendance.

Les marqueurs posés sur un émetteur RØDE sont écrits dans le chunk `cue ` du
fichier BWF, qui est le format WAV standard. Presque aucun lecteur ne l'affiche,
si bien qu'ils semblent n'exister que dans RODE Central.

Cet outil les lit, découpe le fichier à chaque marqueur et **garde tout** : rien
n'est jeté, donc recoller les morceaux rend l'original à l'octet près. C'est
voulu — un marqueur peut signaler un début, une fin, ou juste un passage à
réécouter, et l'outil n'a aucun moyen de le savoir. Une césure sans objet doit
pouvoir être annulée.

L'audio n'est jamais réencodé : les octets du chunk `data` sont recopiés tels
quels. L'horodatage BWF de chaque morceau est décalé pour rester juste.

    decouper-marqueurs.py FICHIER.WAV [...] [--sortie DOSSIER]
    decouper-marqueurs.py --rejoindre DOSSIER_DE_MORCEAUX

SPDX-License-Identifier: MIT
"""

import argparse
import re
import struct
import sys
from pathlib import Path

COPY_BLOCK = 1 << 20
# Position du champ TimeReference dans la structure bext (256+32+32+10+8).
BEXT_TIME_REFERENCE = 338


class Recording:
    """Ce qu'il faut connaître d'un BWF pour le découper."""

    def __init__(self, path: Path):
        self.path = path
        self.fmt = self.bext = self.ixml = None
        self.cues: list[int] = []
        self.data_offset = self.data_size = 0
        self._parse()

    def _parse(self) -> None:
        with self.path.open("rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                raise ValueError(f"{self.path.name} n'est pas un fichier WAVE")
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                cid, size = struct.unpack("<4sI", header)
                if cid == b"data":
                    self.data_offset, self.data_size = f.tell(), size
                    f.seek(size + (size & 1), 1)
                    continue
                payload = f.read(size)
                f.seek(size & 1, 1)
                if cid == b"fmt ":
                    self.fmt = payload
                elif cid == b"bext":
                    self.bext = payload
                elif cid == b"iXML":
                    self.ixml = payload
                elif cid == b"cue ":
                    count = struct.unpack("<I", payload[:4])[0]
                    for i in range(count):
                        base = 4 + i * 24
                        self.cues.append(
                            struct.unpack("<I", payload[base + 20:base + 24])[0]
                        )
        if self.fmt is None:
            raise ValueError(f"{self.path.name} n'a pas de chunk fmt")
        self.channels, self.rate = struct.unpack("<HI", self.fmt[2:8])
        self.block_align, self.bits = struct.unpack("<HH", self.fmt[12:16])

    @property
    def frames(self) -> int:
        return self.data_size // self.block_align

    def boundaries(self) -> list[tuple[int, int]]:
        """Bornes des morceaux, en trames. Tout l'enregistrement est couvert."""
        marks = sorted({c for c in self.cues if 0 < c < self.frames})
        edges = [0, *marks, self.frames]
        return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def clock(frames: int, rate: int) -> str:
    """`MM-SS.mmm`, lisible et triable dans un nom de fichier."""
    seconds = frames / rate
    return f"{int(seconds // 60):02d}-{seconds % 60:06.3f}"


def _shift_bext(bext: bytes, frames: int) -> bytes:
    """Décale l'horodatage BWF, sinon chaque morceau prétend commencer à l'origine."""
    if bext is None or len(bext) < BEXT_TIME_REFERENCE + 8:
        return bext
    low, high = struct.unpack("<II", bext[BEXT_TIME_REFERENCE:BEXT_TIME_REFERENCE + 8])
    value = (high << 32 | low) + frames
    patched = struct.pack("<II", value & 0xFFFFFFFF, value >> 32)
    return bext[:BEXT_TIME_REFERENCE] + patched + bext[BEXT_TIME_REFERENCE + 8:]


def _shift_ixml(ixml: bytes, frames: int) -> bytes:
    if ixml is None:
        return ixml
    text = ixml.decode("utf-8", "replace")

    def bump(match: re.Match) -> str:
        return f"{match.group(1)}{int(match.group(2)) + frames}{match.group(3)}"

    for tag in ("BWF_TIME_REFERENCE_LOW", "TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO"):
        text = re.sub(rf"(<{tag}>)(\d+)(</{tag}>)", bump, text)
    return text.encode("utf-8")


def _chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def write_segment(rec: Recording, start: int, end: int, target: Path) -> None:
    """Écrit un morceau en recopiant les octets du `data`, sans réencodage."""
    head = _chunk(b"fmt ", rec.fmt)
    if rec.bext is not None:
        head += _chunk(b"bext", _shift_bext(rec.bext, start))
    if rec.ixml is not None:
        head += _chunk(b"iXML", _shift_ixml(rec.ixml, start))
    size = (end - start) * rec.block_align

    with rec.path.open("rb") as src, target.open("wb") as out:
        out.write(b"RIFF" + struct.pack("<I", 4 + len(head) + 8 + size + (size & 1)) + b"WAVE")
        out.write(head)
        out.write(b"data" + struct.pack("<I", size))
        src.seek(rec.data_offset + start * rec.block_align)
        remaining = size
        while remaining:
            block = src.read(min(COPY_BLOCK, remaining))
            if not block:
                raise IOError(f"{rec.path.name} : lecture incomplète")
            out.write(block)
            remaining -= len(block)
        if size & 1:
            out.write(b"\0")


REJOIN = """#!/bin/sh
# Recolle les morceaux dans l'ordre et rend l'original à l'octet près.
# Une césure qui n'avait pas lieu d'être s'annule donc sans perte.
exec python3 "{tool}" --rejoindre "$(dirname "$0")"
"""


def split(path: Path, out_root: Path) -> int:
    rec = Recording(path)
    parts = rec.boundaries()
    if len(parts) < 2:
        print(f"  {path.name} : aucun marqueur, ignoré")
        return 0

    folder = out_root / path.stem
    folder.mkdir(parents=True, exist_ok=True)
    total = len(parts)
    for index, (start, end) in enumerate(parts, 1):
        name = (f"{index:02d}_sur_{total:02d}__"
                f"{clock(start, rec.rate)}_a_{clock(end, rec.rate)}.wav")
        write_segment(rec, start, end, folder / name)
        print(f"  {folder.name}/{name}")

    script = folder / "rejoindre.sh"
    script.write_text(REJOIN.format(tool=Path(__file__).resolve()), encoding="utf-8")
    script.chmod(0o755)
    (folder / "source.txt").write_text(f"{path}\n", encoding="utf-8")
    return total


def rejoin(folder: Path) -> Path:
    """Reconstruit l'original en concaténant les `data` dans l'ordre des noms."""
    parts = sorted(p for p in folder.glob("*.wav") if re.match(r"\d{2}_sur_\d{2}__", p.name))
    if not parts:
        raise SystemExit(f"{folder} ne contient aucun morceau à recoller")

    first = Recording(parts[0])
    sizes = [Recording(p).data_size for p in parts]
    total = sum(sizes)
    target = folder / f"{folder.name}_recolle.wav"

    head = _chunk(b"fmt ", first.fmt)
    if first.bext is not None:
        head += _chunk(b"bext", first.bext)
    if first.ixml is not None:
        head += _chunk(b"iXML", first.ixml)

    with target.open("wb") as out:
        out.write(b"RIFF" + struct.pack("<I", 4 + len(head) + 8 + total + (total & 1)) + b"WAVE")
        out.write(head)
        out.write(b"data" + struct.pack("<I", total))
        for part in parts:
            rec = Recording(part)
            with part.open("rb") as src:
                src.seek(rec.data_offset)
                remaining = rec.data_size
                while remaining:
                    block = src.read(min(COPY_BLOCK, remaining))
                    out.write(block)
                    remaining -= len(block)
        if total & 1:
            out.write(b"\0")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fichiers", nargs="*", type=Path)
    parser.add_argument("--sortie", type=Path, default=Path("."),
                        help="dossier où déposer les morceaux (défaut : dossier courant)")
    parser.add_argument("--rejoindre", type=Path, metavar="DOSSIER",
                        help="recoller les morceaux d'un dossier")
    args = parser.parse_args()

    if args.rejoindre:
        target = rejoin(args.rejoindre)
        print(f"recollé : {target}")
        return 0
    if not args.fichiers:
        parser.error("indiquer au moins un fichier, ou --rejoindre")

    cut = 0
    for path in args.fichiers:
        cut += split(path, args.sortie)
    print(f"\n{cut} morceaux écrits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
