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
cue    4 B +    ← markers live here, in the standard WAV chunk
PAD   ~31 kB    pre-allocated, all zeros
bext  ~600 B    firmware version, origination date and time
iXML   ~1 kB    timecode rate and flag, sample timestamp
data            audio
```

**Markers set on the device are written to the standard `cue ` chunk.** They are
not lost when you copy the files off — almost no player or editor displays cue
points, so they only appear to exist inside RØDE's own software.

The zero-filled `PAD ` chunk is why: the device reserves room so it can write cue
points in place afterwards, without rewriting a multi-megabyte file.

`tools/decouper-marqueurs.py` reads them, and can split a recording at its markers
without re-encoding — see the README.

**Firmware quirk:** `BWF_ORIGINATION_DATE` is written as `0026-08-25` rather than
`2026-08-25`. Anything sorting takes by the BWF date will place them in the first
century. Observed on firmware 1.3.3.
