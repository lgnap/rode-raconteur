# RØDE on Linux

Getting RØDE gear working on Linux — including **RODE Central itself**, running
under Wine.

Most of what breaks is not audio. RØDE devices are configured over a
vendor-specific **HID** channel, and that channel is closed to your user by
default. Fix that and a lot of things start working at once.

> **Scope, honestly.** Everything here was verified on **one machine** (Fedora 44,
> kernel 7.1.12, KDE 6/Wayland) with **one device** (Wireless PRO, firmware
> 1.0.2). The HID access part should apply to any RØDE device; the audio and
> device-map parts are model-specific. Corrections and additions welcome — see
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

## Prior art

Device-specific tools that do things this guide does not:

- [rode-wireless-go-ctl](https://github.com/christianmeurer/rode-wireless-go-ctl) — CLI configuration for the Wireless GO II
- [krode](https://github.com/LinuxRenaissance/krode) — delete onboard recordings on the Interview PRO
- [rodecaster-control](https://github.com/Jordan-Milner/rodecaster-control) — open alternative to RODE Central for the RODECaster Pro II
- [Rode_WirelessGoII_UGG2wav](https://github.com/fukidzon/Rode_WirelessGoII_UGG2wav) — convert Wireless GO II `.UGG` recordings to WAV

## License

MIT — see [LICENSE](LICENSE). Copy freely, including into distribution packages
and other wikis.
