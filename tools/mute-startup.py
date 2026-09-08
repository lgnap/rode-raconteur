#!/usr/bin/env python3
"""Silence the power-on transient a transmitter writes at the start of a take.

A transmitter starts recording the instant it leaves the charging case, before
its microphone chain has settled, so the first take of an outing opens with a
block of full-scale square wave. Measured across the library: 90-110 ms, peak
at 1.00 of full scale, crest factor 1.13 every time, where the quietest
ordinary take measures 2.84. The takes that do *not* have it open at speaking
level — a recording rolled over when it reached the file size limit starts
mid-sentence — which is why the length is measured per file rather than
trimmed to a constant.

Only the `data` chunk is written to. `bext` keeps the timestamp, `cue` keeps
the markers, and the file keeps its length, so a take cleaned here can still be
split by split-at-markers.py afterwards.

    mute-startup.py FILES...            report what would change, write nothing
    mute-startup.py --out DIR FILES...  write cleaned copies, originals intact
    mute-startup.py --replace FILES...  patch in place

SPDX-License-Identifier: MIT
"""

import argparse
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conteur.bwf import chunks                                    # noqa: E402
from conteur.card import sha256_file                              # noqa: E402
from conteur.ledger import ledger_root                            # noqa: E402
from conteur.signal import CAPTURE_RATE, startup_burst            # noqa: E402
from conteur.wavread import read_samples                          # noqa: E402

RAMP_MS = 5
WAVE_PCM = 1
WAVE_FLOAT = 3
WAVE_EXTENSIBLE = 0xFFFE

DTYPES = {(WAVE_FLOAT, 32): "<f4", (WAVE_FLOAT, 64): "<f8",
          (WAVE_PCM, 16): "<i2"}


class StillHeld(Exception):
    """The file is a copy a ledger vouches for, and an erase depends on it."""


def _format(fh) -> tuple[str, int, int]:
    """(numpy dtype, channels, sample rate) of the `fmt ` chunk."""
    found = chunks(fh)
    if "fmt " not in found or "data" not in found:
        raise ValueError("missing fmt or data chunk")
    offset, size = found["fmt "]
    fh.seek(offset)
    fmt = fh.read(size)
    tag, channels, rate = struct.unpack("<HHI", fmt[:8])
    bits = struct.unpack("<H", fmt[14:16])[0]
    if tag == WAVE_EXTENSIBLE and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if (tag, bits) not in DTYPES:
        raise ValueError(f"unsupported format: tag {tag}, {bits} bits")
    return DTYPES[(tag, bits)], channels, rate


def held_by_ledger(path: Path) -> bool:
    """True if a ledger still counts this file as a verified copy.

    Read by digest and not by name: a ledger follows a file through renaming,
    which is the whole point of storing the digest.
    """
    import json
    root = ledger_root()
    if not root.is_dir():
        return False
    digest = sha256_file(path)
    for entry in root.glob("*.json"):
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(take.get("digest") == digest for take in raw.get("takes", [])):
            return True
    return False


def mute_startup(path: Path, ramp_ms: int = RAMP_MS, guard: bool = False) -> int:
    """Silence the transient in place. Returns the frames covered, 0 if none.

    `guard` refuses a file a ledger still holds: patching the audio changes the
    digest, and the digest is what authorises erasing the card it came from.
    Done between an import and the erase, this closes that lock for good — the
    copy no longer matches, and nothing can prove the card is safe to wipe.
    """
    path = Path(path)
    with path.open("rb") as fh:
        dtype, channels, rate = _format(fh)
        data_at, data_size = chunks(fh)["data"]

    frames = startup_burst(read_samples(path, max_frames=rate), rate=rate)
    if not frames:
        return 0
    if guard and held_by_ledger(path):
        raise StillHeld(f"{path.name}: a ledger still holds this copy")

    ramp = min(int(rate * ramp_ms / 1000), frames)
    width = np.dtype(dtype).itemsize
    block = width * channels
    silent = frames - ramp

    with path.open("r+b") as fh:
        fh.seek(data_at)
        fh.write(b"\x00" * min(silent * block, data_size))
        # The junction is ramped rather than cut: stepping straight back to
        # full audio is itself a click, which is the defect being removed.
        fh.seek(data_at + silent * block)
        raw = fh.read(min(ramp * block, data_size - silent * block))
        samples = np.frombuffer(raw, dtype=dtype).astype(np.float64)
        factors = np.repeat(np.linspace(0.0, 1.0, ramp), channels)[:samples.size]
        scaled = samples * factors
        if np.dtype(dtype).kind == "i":
            scaled = np.rint(scaled)
        fh.seek(data_at + silent * block)
        fh.write(scaled.astype(dtype).tobytes())
    return frames


def measure(path: Path) -> int:
    """Frames of transient, without writing anything."""
    with path.open("rb") as fh:
        _, _, rate = _format(fh)
    return startup_burst(read_samples(path, max_frames=rate), rate=rate)


def collect(targets: list[Path]) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        if target.is_dir():
            found.extend(sorted(p for p in target.iterdir()
                                if p.suffix.lower() == ".wav"))
        elif target.suffix.lower() == ".wav":
            found.append(target)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="+", type=Path,
                        help="directories or WAV files")
    parser.add_argument("--out", type=Path, metavar="DIR",
                        help="write cleaned copies there, leaving the originals alone")
    parser.add_argument("--replace", action="store_true",
                        help="patch the files in place")
    parser.add_argument("--anyway", action="store_true",
                        help="with --replace, patch even a copy a ledger holds")
    args = parser.parse_args()

    files = collect(args.targets)
    if not files:
        print("no WAV file found")
        return 1
    if args.out and args.replace:
        parser.error("--out and --replace are mutually exclusive")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    touched = clean = 0
    for path in files:
        try:
            frames = measure(path)
        except (OSError, ValueError) as error:
            print(f"  !! {path.name}: {error}")
            continue
        if not frames:
            clean += 1
            continue
        rate = CAPTURE_RATE
        if not args.out and not args.replace:
            print(f"  {frames / rate * 1000:5.0f} ms  {path.name}")
            touched += 1
            continue
        target = path
        if args.out:
            target = args.out / path.name
            shutil.copy2(path, target)
        try:
            mute_startup(target, guard=args.replace and not args.anyway)
        except StillHeld as held:
            print(f"  !! {held} — erase the card first, or pass --anyway")
            continue
        print(f"  {frames / rate * 1000:5.0f} ms  {target.name}")
        touched += 1

    verb = "would be silenced" if not (args.out or args.replace) else "silenced"
    print(f"\n{touched} {verb}, {clean} already clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
