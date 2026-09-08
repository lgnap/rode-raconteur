# rode-raconteur

RØDE gear on Linux — and a recorder that names its own takes.

This started as an attempt to make **RODE Central** itself work under Wine. It
mostly does, and the parts that do not turned out to be worth writing down. Along
the way the hardware had to be understood well enough that a small native
recorder became the better tool for everyday use. Four things live here:

| | |
|---|---|
| **1. RODE Central under Wine** | How far it gets, what unlocks it, and the one thing that genuinely does not work — [docs/rode-central-wine.md](docs/rode-central-wine.md), with a Wine bug report [still to file](docs/upstream/) |
| **2. How the Wireless PRO works** | Interfaces, HID reports, onboard recordings, markers, throughput — [docs/device-map.md](docs/device-map.md) and [docs/wireless-pro-hid-observations.md](docs/wireless-pro-hid-observations.md) |
| **3. Erasing recordings with krode** | What that binary is, why it has to exist, and what we sent upstream — [docs/krode.md](docs/krode.md) |
| **4. `conteur`, a recorder** | Records from the receiver and names each take from what is said in it; also recovers the transmitters' onboard recordings and erases a card once every take on it is copied and verified — [below](#conteur--a-recorder-that-names-its-own-takes) |

> **Scope, honestly.** Everything here was verified on **one machine** (Fedora 44,
> kernel 7.1.12, KDE 6/Wayland) with **one kit**: a Wireless PRO receiver, two
> transmitters and a Charge Case+, on firmware 1.0.2 through 2.0.8. The HID access
> part should apply to any RØDE device; the audio, device-map and recording
> formats are model-specific. Corrections and additions welcome — see
> [CONTRIBUTING.md](CONTRIBUTING.md), it takes one command.

## Start here

```sh
./tools/rode-doctor.sh
```

It prints what is plugged in, what interfaces each device declares, whether the
HID nodes are reachable, whether ALSA sees a usable capture device, and what
your Wine bottle is configured to expose. Most problems are visible in that
output.

## The two things that block RODE Central

Both must be done. Doing one and not the other produces no visible change,
which makes this frustrating to diagnose one step at a time.

### 1. Let your user reach the HID nodes

```
$ ls -l /dev/hidraw11
crw-------  root root  /dev/hidraw11      ← your user gets EACCES
```

```sh
sudo install -m 0644 udev/70-rode.rules /etc/udev/rules.d/70-rode.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add /sys/class/hidraw/hidraw*
```

Expected afterwards: `crw-rw----+` — the trailing `+` is the ACL, and it is the
part that matters.

**Do not** reach for `udevadm trigger --attr-match=idVendor=19f7`. It matches
nothing for hidraw nodes: `--attr-match` only tests a device's own attributes,
and `idVendor` lives on the parent USB device. The rule itself is fine — it
uses `ATTRS`, which walks the parent chain — but the trigger has to name sysfs
paths. This one wasted a while.

### 2. Expose the devices to Wine

Wine's `winebus.sys` only presents HID devices it has been told to present.
Under Bottles, list them in the bottle's `hidraw_devices`; Bottles turns that
into `PROTON_ENABLE_HIDRAW` for the runner.

```yaml
# bottle.yml — Parameters:
hidraw_devices:
  - 0x19F7/0x0056
  - 0x19F7/0x0058
```

Full walkthrough, including the non-obvious parts: **[docs/rode-central-wine.md](docs/rode-central-wine.md)**

### Verify it actually worked

Do not trust the application's own display. Check that Wine's HID host process
holds the file descriptors:

```sh
readlink /proc/$(pgrep -f winedevice.exe | tail -1)/fd/* | grep hidraw
```

## Using the microphone as a sound card

Short version for the Wireless PRO: **only the receiver can be a sound card, and
only outside the charging case.** The transmitters never can — plugged in
directly they expose HID plus a mass-storage volume holding their onboard
recordings.

There is also a trap worth knowing about: the receiver can enumerate with a USB
**AudioControl** interface but no **AudioStreaming** interface. ALSA then creates
a card with a mixer and *zero* PCM devices — so it shows up in
`/proc/asound/cards` but not in `arecord -l`. Nothing on the Linux side fixes
that; it is a device mode.

Details, and the per-model table: **[docs/audio-capture.md](docs/audio-capture.md)**
and **[docs/device-map.md](docs/device-map.md)**

## The timecode channel

If your receiver is in a timecode mode, **the right channel is not audio.** It
carries SMPTE LTC — a full-scale square wave. RØDE documents this for camera
use; the consequence on Linux is that a stereo capture feeds a square wave into
whatever you are recording.

Record the left channel only:

```sh
arecord -D hw:RX,0 -f S16_LE -r 48000 -c 2 - | sox - -c 1 out.wav remix 1
```

`rode-doctor.sh --record` will measure both channels and tell you which is
which.

## Case study

A full write-up of the diagnostic session this repo came out of — the two
locks, the dead ends, the measurements, and the timecode decode — is included
as a self-contained page (in French):

**[docs/case-study-wireless-pro.fr.html](docs/case-study-wireless-pro.fr.html)**

Open it locally, or serve the repo with GitHub Pages to read it in a browser.

## Splitting a recording at its markers

Markers set on a transmitter are stored in the file's standard `cue ` chunk, which
most software silently ignores. To cut a recording at them:

```sh
./tools/decouper-marqueurs.py /path/to/00009_Name.WAV --sortie ~/decoupe
```

Each source gets a folder of numbered parts, named with their time range:

```
00009_Name/
  01_sur_03__00-00.000_a_01-20.799.wav
  02_sur_03__01-20.799_a_07-46.811.wav
  03_sur_03__07-46.811_a_07-57.915.wav
  rejoindre.sh
```

**Nothing is discarded and nothing is re-encoded.** The audio bytes are copied
verbatim and every part of the recording is kept, so `./rejoindre.sh` reconstructs
the audio byte for byte — verified on real files, including one cut at 34 markers
into 35 parts. That matters because a marker may mean a start, an end, or just a
passage worth revisiting, and the tool has no way to tell: a cut that turns out to
be pointless has to be undoable.

**Known limitation.** Rejoining restores the `data` chunk identically but not the
whole header: the `cue ` and `PAD ` chunks are not rebuilt. A rejoined file has
lost its markers and cannot be split again. Keep the original if you may want to
re-cut it.

Each part's BWF timestamp is shifted by its offset, so timecode stays correct.

## `conteur` — a recorder that names its own takes

The reason this exists: recording a two-minute idea should not mean opening
Audacity, picking the right input, arming a track, and then inventing a filename.

```sh
python -m conteur
```

One window. It watches for the Wireless PRO receiver, records when you press the
button, and then **works out what to call the take from what was said in it**.

```
2026-09-06_143208_chaine-hifi-et-baffle.wav      · titre généré
2026-09-06_150411_silence.wav                    · silence
2026-09-06_151002_timecode.wav                   · canal timecode
```

**How the naming works.** The take is transcribed locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`large-v3`), and a
local [Ollama](https://ollama.com) model (`qwen3:8b`) turns the transcript into a
short title. Nothing leaves the machine. When there is too little speech to
title, the name falls back to keywords; when there is none, the file is labelled
`silence`, `sans-parole` or `timecode` rather than given a made-up name. Every
row shows *where* its name came from, so a wrong one is obvious at a glance.

Files land in `<Music>/Enregistrements/<YYYY-MM>/`. Double-click a row to rename
it yourself.

**What it takes care of, because these all happened:**

- **The receiver is only sometimes there.** The device list is re-scanned while
  the window is open, so plugging the receiver in after launch works.
- **Unplugging mid-take does not lose the take.** The fragment is written and
  marked `incomplet`.
- **A take is never lost to a failed naming.** The WAV is written before
  transcription is attempted, under a provisional name, and takes left unnamed by
  a crash are picked up again at the next launch.
- **One job at a time.** Whisper and the titling model do not fit in 8 GB of VRAM
  together, so naming is serialised through a single-slot queue.
- **Ctrl+C actually quits**, and a take in progress is written on the way out.

Requires Python 3.12+, PySide6, PyAudio, faster-whisper, and a running Ollama.
Tests: `python -m pytest` (337 tests, no hardware needed).

### Naming files you already have

`conteur` names what it records. For files that already exist — takes copied off
a transmitter, or parts cut at markers — the same recognition is available from
the command line:

```sh
./tools/nommer-morceaux.py ~/decoupe/00009_Name/
```

It **appends** the slug rather than replacing the name, so cut parts keep the
numbering that says what belonged together. The decision comes from the same
shared code as the application, not a copy of it.

### Recovering a transmitter's card, and erasing it

**Récupérer** copies what the transmitters recorded onto the machine. Nothing is
mounted: the card is read from `/dev/sdX` as a FAT32 volume, which is what makes
the volume UUID available — it is the only per-transmitter identifier that holds
both through the charging case and on a transmitter connected directly, and it
equals that transmitter's `HID_UNIQ`. Files land beside the recorded ones, in
`<Music>/Enregistrements/<YYYY-MM>/`, and are named the same way.

Every copy is verified by digest, and what was verified is written to a ledger,
one JSON file per card in `~/.local/share/conteur/imports/<volume-uuid>.json`.
Importing the same card twice copies nothing twice. A take still being recorded —
the zero-byte entry a transmitter always shows, because it starts recording the
instant it leaves the case — is skipped rather than copied empty.

**The ledger is what unlocks erasing.** A card is offered an erase button only
when it holds nothing that is not already copied and verified, and there is one
button per card, never one that erases everything. Pressing it re-inventories the
card and re-hashes every local copy before sending anything, which is the slow
part: around 80 s for 30 GB, against 1.2–1.8 s for the command itself. It refuses,
rather than erasing, when

- the card changed since the import — a transmitter that left the case for a
  second has already recorded something nobody has copied;
- a recording is in progress on it;
- one of the local copies is missing or no longer matches its digest;
- the HID node stopped matching that card, which a re-dock during the hashing
  is enough to cause.

The command is the vendor HID one described below, which is [krode](https://github.com/LinuxRenaissance/krode)'s finding before it is anything of ours — its author documented the protocol instead of only shipping a binary, and none of this would exist otherwise. After it, the transmitter
re-enumerates, so the card is **not** readable again straight away — the erase is
recorded in the ledger with the number of takes it covered and, normally, no
count of what is left, because nothing could be read back. Erasing two cards in a
row works: the device list is resolved again between them, as the first erase
invalidates it. The take counter on the card restarts at `00001`.

Verified on 2026-09-08 with two transmitters: five erases, including two in a
row; the first and third refusals above; the counter restarting; and a re-import
that copied the new takes rather than reporting them as already held. The other
two refusals are exercised by the test suite only — a card that starts recording
between the import and the click, and a re-dock that moves the HID node during
the hashing, are both hard to produce on purpose.

## Deleting onboard recordings

The storage a transmitter exposes is **read-only at the device level**, so a full
card cannot be emptied through the filesystem. The vendor HID command that does
it is implemented by [krode](https://github.com/LinuxRenaissance/krode).

What that binary is, why it is necessary, the percentage-not-status-code finding
and the erase-completion fix we sent upstream: **[docs/krode.md](docs/krode.md)**

`conteur` sends that same command itself, from those findings, but only for a
card whose every take it has copied and verified — see
[above](#recovering-a-transmitters-card-and-erasing-it). Reach for the binary
when you want to erase a card without importing it first.

Copy and verify your recordings before erasing. It is irreversible and there is
no confirmation prompt. Read the cards through the charging case over a
SuperSpeed link — **38.6 MB/s**, about twelve minutes for a completely full
card; the same case on a USB 2 cable manages 21.8 MB/s and will tell you it is
a USB 2.0 device.

## Prior art

Device-specific tools that do things this guide does not:

- [rode-wireless-go-ctl](https://github.com/christianmeurer/rode-wireless-go-ctl) — CLI configuration for the Wireless GO II
- [krode](https://github.com/LinuxRenaissance/krode) — delete onboard recordings on the Interview PRO
- [rodecaster-control](https://github.com/Jordan-Milner/rodecaster-control) — open alternative to RODE Central for the RODECaster Pro II
- [Rode_WirelessGoII_UGG2wav](https://github.com/fukidzon/Rode_WirelessGoII_UGG2wav) — convert Wireless GO II `.UGG` recordings to WAV

## How this was written

Most of the code and prose in this repository was written by Claude (Anthropic's
model) over a long working session, directed and reviewed throughout by a human.
Saying so seems more useful than leaving you to guess.

What is **not** generated is the evidence. Every measurement here was taken from
real hardware on the machine described at the top: the USB and HID report
descriptors were read from sysfs, the throughput figures came from `dd` and from
actual file copies, the erase protocol was traced and then run on two
transmitters, and the WAV chunk layouts were parsed from files the devices wrote.
Where something is inferred rather than measured, the text says so.

Corrections are welcome on any of it — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE). Copy freely, including into distribution packages
and other wikis.
