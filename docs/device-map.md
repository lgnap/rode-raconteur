# Device map

USB vendor ID for RØDE Microphones is **`0x19f7`**.

What matters for Linux is the **interface class** each device declares, because
that is what decides whether a driver binds and what you can do with it.

| Class | Meaning | Kernel driver |
|---|---|---|
| `03` | HID — configuration channel used by RODE Central | `usbhid` → `/dev/hidrawN` |
| `01` sub `01` | USB Audio *Control* — mixer only, **no stream** | `snd-usb-audio` |
| `01` sub `02` | USB Audio *Streaming* — this is the one that gives you a PCM | `snd-usb-audio` |
| `08` | Mass storage — onboard recordings | `usb-storage` |

## Wireless PRO

Verified on firmware 1.0.2. `hidraw` access requires the udev rule in this repo
for every row below.

| Device | PID | Connection | Interfaces | Sound card |
|---|---|---|---|---|
| Wireless PRO RX | `0058` | direct, correct mode | `03` + `01/01` + `01/02` + `ff` | **yes** — 24-bit/48 kHz stereo |
| Wireless PRO RX | `0058` | direct, timecode-only mode | `03` + `01/01` | card with **no PCM** |
| Wireless PRO RX | `0058` | in charging case | `03` | no |
| Wireless PRO TX | `0056` | direct | `03` + `08` | no — HID + ~29 GB storage |
| Wireless PRO TX | `0056` | in charging case | `03` | no |
| Charge Case+ | `007a` | — | `03` | no |
| Charge Case+ | `007c` | — | `08` | no |

### Channel layout when the RX is a sound card

| Channel | Content |
|---|---|
| Left | audio |
| Right | SMPTE LTC timecode (in timecode modes) — **not audio** |

### Charging-case topology

Devices sitting in the case reach the host through two cascaded hubs *inside the
case* (`1a86:8091`), and expose HID only:

```
1-1      1a86:8091  USB HUB           ← case
├─ 1-1.1   19f7:007a  Charge Case+    HID
├─ 1-1.2   1a86:8091  USB HUB         ← charging bay
│   ├─ 1-1.2.2  19f7:0058  RX         HID only
│   ├─ 1-1.2.3  19f7:0056  TX         HID only
│   └─ 1-1.2.4  19f7:0056  TX         HID only
└─ 1-1.4   19f7:007c  Charge Case+    Mass Storage
```

`lsusb` showing five RØDE devices is therefore fully compatible with **zero**
usable audio devices.

## Other models

Not covered — no hardware to test. If you have another RØDE device, running
`tools/rode-doctor.sh` and opening an issue with the output is enough to extend
this table. See [CONTRIBUTING.md](../CONTRIBUTING.md).

## Onboard recordings and markers

Transmitters record to their own storage as **Broadcast WAV** (BWF): 48 kHz,
32-bit IEEE float, mono, one numbered file per take, named after the transmitter.

```
fmt   16 B      format
bext  602 B     firmware version, origination date and time     ← not always present
iXML 1110 B     timecode rate and flag, sample timestamp        ← not always present
cue    4 B +    ← markers live here, in the standard WAV chunk
PAD   ~31 kB    pre-allocated, all zeros
data            audio
```

**`bext` and `iXML` are not always written.** Of four takes from one transmitter
on firmware 2.0.8, only one carried them — the one with markers. Whatever
triggers them, a tool that needs the recording's start time cannot assume they
are there.

The chunks before `data` always total exactly **32 768 bytes**, because `PAD `
absorbs the difference: adding `bext`, `iXML` and 34 cue points to a file grew
those chunks by 2 544 bytes and shrank `PAD ` by exactly 2 544. The audio
therefore always begins at offset `0x8008`.

### The BWF timestamp is the start, the filesystem date is the end

```
bext / iXML   09:40:31     recording started
FAT mtime     09:44        file closed
duration      234 s        09:40:31 + 3:54 = 09:44:25   ✓
```

Anything that dates a take by its file timestamp dates it by when it *stopped*.

**Markers set on the device are written to the standard `cue ` chunk.** They are
not lost when you copy the files off — almost no player or editor displays cue
points, so they only appear to exist inside RØDE's own software.

The zero-filled `PAD ` chunk is why: the device reserves room so it can write cue
points in place afterwards, without rewriting a multi-megabyte file.

`tools/decouper-marqueurs.py` reads them, and can split a recording at its markers
without re-encoding — see the README.

**Firmware quirk:** `BWF_ORIGINATION_DATE` is written as `0026-08-25` rather than
`2026-08-25`. Anything sorting takes by the BWF date will place them in the first
century. Observed on firmware 1.3.3 and again on 2.0.8, in the `bext` chunk and
in `iXML` alike — so it is the firmware, not a decoding artefact.

## Reading the recordings off

### Throughput, and the trap in measuring it

Read from the Charge Case+, measured with `dd iflag=direct` and confirmed by a
real file copy that hashes the stream as it goes:

| Host connection | Throughput | A full 28.9 GB card |
|---|---|---|
| USB-C, SuperSpeed link | **38.6 MB/s** | **12.5 min** |
| through a USB 2 path | 21.8 MB/s | 23 min |

**The cable and the port matter, and the descriptor will not tell you.** On a
USB 2 path the case declares `bcdUSB 2.10` and negotiates 480M, so it looks
like a USB 2.0 device. On a SuperSpeed path the very same case declares
`bcdUSB 3.20` and negotiates 5000M, and the kernel raises `max_sectors_kb`
from 120 to 1024. A device descriptor describes the connection that was
negotiated, not what the hardware can do — do not conclude a ceiling from it,
as we first did.

```sh
cat /sys/bus/usb/devices/<dev>/speed          # 480 or 5000
cat /sys/block/sdX/queue/max_sectors_kb       # 120 or 1024
```

A transmitter connected directly gave 5.2 MB/s, but that was measured on a
USB 2 path and has not been re-measured since; treat it as not comparable. The
charging case is the connection RØDE designs for, and the one these figures
describe.

### Reading both cards at once is slower than one after the other

The two LUNs share one link, and competing for it costs:

| | Throughput |
|---|---|
| one card at a time | 38.6 MB/s |
| both in parallel | 18.2 + 18.3 = 36.5 MB/s |

Worse on a USB 2 path, where the same test lost 25 %. Copy one card, then the
other.

### The case exposes two LUNs under one serial — and that breaks the obvious join

Docked transmitters do not present their own storage. The case's `0x007c`
interface presents **both cards as two LUNs of a single USB device**:

```
/dev/sdb  scsi 2:0:0:0  LUN 0  ┐                                   volume 800A-F63E
                               ├─ 19f7:007c  serial 00000000V332
/dev/sdc  scsi 2:0:0:1  LUN 1  ┘                                   volume 800A-92D6
```

Both LUNs report the **case's** USB serial. A transmitter connected directly
does carry its own serial in the USB descriptor — but that only works in the
direct case, so it is not a join key you can rely on.

**The FAT volume UUID is the only per-transmitter identifier that works in both
situations**, and it equals that transmitter's `HID_UNIQ`. That is the mapping
to use when deciding which device to send an erase to.

### There is always a take in progress

A transmitter **starts recording the instant it leaves the charging case**. So a
transmitter you plug in always shows one extra file, with a size of **0 bytes**:

```
00004_Ambiance-Personne.WAV         0    9:28   ← recording, not empty
00005_Ambiance-Personne.WAV  44988872   9:44
```

Zero is what the directory entry says while the file is still open; the size is
written when the take is finalised. Re-docking the transmitter finalises it, and
the file then shows its real size. A 14-minute take looked like an empty card
until it went back in the case.

Any tool reading the card should **skip the trailing zero-byte entry** — it is
the normal state of a device you just connected, not a corrupted file.

### Both are read-only

`/sys/block/sdX/ro` reads `1` and the kernel logs `Write Protect is on`, whether
the card comes from the case or from a transmitter connected directly. Deleting
a take through the filesystem is impossible by design — see
[krode.md](krode.md).
