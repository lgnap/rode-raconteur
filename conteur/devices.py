"""Seeing the RØDE cards, and telling which transmitter each one belongs to.

The join key is the FAT volume serial, which equals the transmitter's
HID_UNIQ. It is deliberately not the USB serial: through the charging case the
two cards are two LUNs of a single USB device carrying the case's own serial
for both, so the transmitter's serial only exists on the block side when it is
connected directly.
"""

import struct
from dataclasses import dataclass
from pathlib import Path

VENDOR_ID = "19f7"
CASE_STORAGE_PID = "007c"
SYS_BLOCK = Path("/sys/block")
SYS_HIDRAW = Path("/sys/class/hidraw")


@dataclass(frozen=True)
class BlockCandidate:
    node: Path
    serial: str | None
    product_id: str
    size: int


@dataclass(frozen=True)
class StorageDevice:
    serial: str
    block: Path
    hidraw: Path | None
    via_case: bool


def read_volume_serial(node: Path) -> str | None:
    """The FAT volume serial, straight from the boot sector.

    Not from blkid: it has not re-probed immediately after an erase, and the
    device is readable long before udev has settled.
    """
    try:
        with node.open("rb") as fh:
            boot = fh.read(512)
    except OSError:
        return None
    if len(boot) < 512 or boot[82:87] != b"FAT32":
        return None
    value = struct.unpack("<I", boot[67:71])[0]
    return f"{value >> 16:04X}-{value & 0xFFFF:04X}"


def _usb_parent(start: Path) -> Path | None:
    node = start.resolve()
    while node != node.parent:
        if (node / "idVendor").is_file():
            return node
        node = node.parent
    return None


def default_lister() -> list[BlockCandidate]:
    """RØDE block nodes visible in sysfs, whatever state they are in."""
    found: list[BlockCandidate] = []
    if not SYS_BLOCK.is_dir():
        return found
    for entry in sorted(SYS_BLOCK.glob("sd*")):
        usb = _usb_parent(entry / "device")
        if usb is None:
            continue
        try:
            if (usb / "idVendor").read_text().strip() != VENDOR_ID:
                continue
            pid = (usb / "idProduct").read_text().strip()
            sectors = int((entry / "size").read_text().strip())
        except (OSError, ValueError):
            continue
        node = Path("/dev") / entry.name
        size = sectors * 512
        serial = read_volume_serial(node) if size else None
        found.append(BlockCandidate(node, serial, pid, size))
    return found


def default_hidraw_lister() -> dict[str, Path]:
    """HID_UNIQ to node, re-read every time.

    Node numbers are not stable across a replug — two transmitters swapped
    theirs in one session — so a path resolved earlier must never be trusted.
    """
    out: dict[str, Path] = {}
    if not SYS_HIDRAW.is_dir():
        return out
    for entry in sorted(SYS_HIDRAW.iterdir()):
        try:
            uevent = (entry / "device" / "uevent").read_text()
        except OSError:
            continue
        if f"{VENDOR_ID.upper()}" not in uevent.upper():
            continue
        for line in uevent.splitlines():
            if line.startswith("HID_UNIQ=") and line[9:].strip():
                out[line[9:].strip()] = Path("/dev") / entry.name
    return out


def find_storage(lister=default_lister,
                 hidraw_lister=default_hidraw_lister) -> list[StorageDevice]:
    """The cards worth reading, one entry per physical card.

    A LUN of size zero means the transmitter has been taken out of the case —
    normal, not an error. And after an erase the same card shows up twice, once
    through the case and once as the transmitter's own node; the case's is the
    one to use, since the transmitter's carries no filesystem, runs on a slower
    link and arrives root-only.
    """
    hid = hidraw_lister()
    best: dict[str, BlockCandidate] = {}
    for candidate in lister():
        if not candidate.size or candidate.serial is None:
            continue
        current = best.get(candidate.serial)
        if current is None or (candidate.product_id == CASE_STORAGE_PID
                               and current.product_id != CASE_STORAGE_PID):
            best[candidate.serial] = candidate
    return [
        StorageDevice(
            serial=serial,
            block=candidate.node,
            hidraw=hid.get(serial.replace("-", "")),
            via_case=candidate.product_id == CASE_STORAGE_PID,
        )
        for serial, candidate in sorted(best.items())
    ]
