# Wireless PRO HID observations

Sent upstream as https://github.com/LinuxRenaissance/krode/issues/2 —
krode deletes onboard recordings on the Interview PRO over vendor HID, and
the Wireless PRO turns out to use the same command shape.

Kept here because these are measurements, not a conversation: they stay
useful whatever becomes of the issue.

Thanks for krode — and especially for documenting the protocol rather than
just shipping a binary. This is a request for **Wireless PRO** support, but
mostly it is a data dump, since I have the hardware and you may not.

I have not sent any command to my devices. Everything below is passive
observation: USB descriptors, and a trace of RØDE Central's own HID traffic.
I did not try `0x4A` on a Wireless PRO, deliberately — see the last section.

## Devices

All under VID `0x19F7`:

| PID | Product | Interfaces |
|---|---|---|
| `0x0056` | Wireless PRO TX | HID `03/00` + Mass Storage `08/06` when connected directly |
| `0x0058` | Wireless PRO RX | HID `03/00`, plus Audio `01/01` + `01/02` in some modes |
| `0x007A` | Charge Case+ | HID `03/00` |
| `0x007C` | Charge Case+ | Mass Storage `08/06` |

Docked in the charging case, the TX and RX expose **HID only** — the case
itself presents the aggregated storage through two internal `1a86:8091` hubs.
A TX connected directly presents its own storage.

## Your read-only observation holds here too

Your README notes the Interview PRO's mass storage is read-only. Same on the
Wireless PRO, and it is the block device itself that says so:

```
/sys/block/sdb/ro = 1
```

Both when the storage comes from the charging case and when a TX is connected
directly. So deletion cannot go through the filesystem on this model either —
which is what led me here.

## The interesting part: same command shape

I traced RØDE Central's HID traffic (it runs under Wine/Bottles, and the
device list, battery levels and firmware version all work — only the
Recordings tab fails). Wine's `WINEDEBUG=+hid` gives the raw reports.

Three report families are used:

```
id 1,  17 bytes   01 36 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
id 1,  17 bytes   01 77 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
id 9,  37 bytes   09 00 01 00 ...   (1478 occurrences — polling)
id 9,  37 bytes   09 01 01 00 ...
id 15, 64 bytes   (2 occurrences)
```

**`id 1` / 17 bytes is exactly the shape your delete command uses**
(`01 4A 01 00 …`). Different opcodes — I observed `0x36` and `0x77`, you
document `0x4A` — but the same report id, the same length, and the same
`[id][opcode][param][padding]` layout.

That makes it plausible the Wireless PRO shares the command family, with
`0x4A` possibly meaning the same thing. Plausible is not verified.

## What I did not do, and why

I did not send `01 4A 01` to a Wireless PRO. An unknown vendor opcode against
firmware on hardware I use for fieldwork is not a risk worth taking blind, and
a negative result would be indistinguishable from a bricked setting.

If you think it is worth testing, I am willing to run it and report back — my
recordings are already copied off and verified, so the only thing at stake is
the device state. Tell me what you would want captured (the input report on
`0x81`, the status byte, anything else) and I will do it carefully.

## Environment

Fedora 44, kernel 7.1.12, `hidraw` access via a udev rule tagging
`ATTRS{idVendor}=="19f7"` with `uaccess` across the `hidraw`, `usb` and
`block` subsystems. Wireless PRO firmware 1.3.3 (from the BWF `bext` chunk),
RØDE Central reports 1.0.2 for the case.

Happy to run further captures if any of this is useful.


---

# Confirmed: deleting onboard recordings

`01 4A 01` on report id 1, 17 bytes, deletes every onboard recording on a
Wireless PRO TX. Tested on both of mine, one connected directly and one
**through the charging case** — the docked path works, so there is no need to
undock the transmitters.

```
01 4A 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00   ->  /dev/hidrawN
02 4A 41 00     ACK 0x41,   0 %
02 4A 41 05                 5 %
   …
02 4A 41 64               100 %
```

Byte [3] is a **percentage**, not a status code: krode's documented "status 100
= success" is the device reporting 100 % complete. 6.1 GB erased in 1.8 s.

## Identifying which transmitter you are about to erase

Both transmitters share PID `0x0056`, so VID/PID is not enough. The HID serial
is the FAT volume UUID of that transmitter's storage, which makes the mapping
unambiguous:

```
HID_UNIQ=800A92D6   ↔   volume 800A-92D6
HID_UNIQ=800AF63E   ↔   volume 800A-F63E
```

Mount the volume, read a filename, and you know which physical microphone a
given serial belongs to.

**Node numbers are not stable.** A replug swapped `hidraw10` and `hidraw12`
between the two transmitters. Re-read `HID_UNIQ` from sysfs immediately before
writing, rather than trusting a path resolved earlier.

## Afterwards, replug

The transmitter re-enumerates and its volume returns **without a filesystem** —
`I/O error` on the block device, plus a stale extra node. Unplugging and
replugging the case brings both volumes back as empty FAT32. Waiting does not
help; the replug is part of the procedure.

## Safety

The storage is read-only at the device level (`/sys/block/*/ro = 1`), so this
is the only way to erase. Copy and verify your recordings first — this is
irreversible and there is no confirmation prompt.
