# Capturing audio from a RØDE wireless system

Verified on Fedora 44 with a Wireless PRO (RX + 2 TX + Charge Case), firmware
1.0.2. See [device-map.md](device-map.md) for which device does what.

## Only the receiver is a sound card

Plug the **RX** in on its own, out of the charging case, directly into the
computer. It then declares a USB Audio Class interface and needs no driver:

```
$ arecord -l
card 3: RX [Wireless PRO RX], device 0: USB Audio
  → hw:RX,0   S24_3LE   48000 Hz   2 channels
```

PipeWire picks it up automatically and will usually select it as the default
source:

```
$ wpctl status
Sources:  * 85. Wireless PRO RX Analog Stereo   [vol: 1.00]
```

The **transmitters cannot** do this. Connected directly they expose HID plus a
mass-storage volume (~29 GB, FAT, labelled `RODE Wireless PRO`) holding their
onboard recordings. There is no audio interface in their descriptors, in any
observed mode.

## Trap: a sound card with no PCM

The receiver can enumerate with a USB **AudioControl** interface but *no*
**AudioStreaming** interface:

```
$ lsusb -v -d 19f7:0058 | grep -E 'bInterfaceClass|bInterfaceSubClass'
      bInterfaceClass         3 Human Interface Device
      bInterfaceClass         1 Audio
      bInterfaceSubClass      1 Control Device      ← control only, no streaming
```

ALSA then creates a card with a `usbmixer` and **zero PCM devices**. The
contradictory symptom:

```
$ cat /proc/asound/cards        # the card is there
 3 [RX ]: USB-Audio - Wireless PRO RX
$ arecord -l | grep RX          # but nothing to open
$
```

Nothing on the Linux side fixes this. It is a device mode — change it on the
receiver or in RODE Central. Once the AudioStreaming interface appears, a PCM
capture device shows up immediately.

Quick check:

```sh
ls /proc/asound/card*/pcm*      # no pcm*c entry → no capture stream
```

## Trap: the right channel is not audio

In timecode modes the receiver puts **audio on the left channel and SMPTE LTC
timecode on the right**. RØDE documents this for feeding cameras; on Linux the
consequence is that an ordinary stereo capture records a full-scale square wave
alongside your voice.

Measured on a 6-second capture:

| | Left (audio) | Right (timecode) |
|---|---|---|
| Crest factor | 6.55 | **1.03** |
| Peak | −22.6 dBFS | −1.0 dBFS |
| Samples above 0.8 × peak | 0.0 % | **93.7 %** |
| Distinct sample values | many | **5** |

A crest factor of 1.03 is a square wave; speech is above 4. The stream decodes
as valid SMPTE LTC at 24 fps, locked to time of day.

### Record the left channel only

```sh
arecord -D hw:RX,0 -f S16_LE -r 48000 -c 2 - | sox - -c 1 out.wav remix 1
```

Or in PipeWire, use a channel-remap so applications only ever see the left
channel.

`tools/rode-doctor.sh --record` measures both channels and reports which is
which, so you do not have to guess after changing a device mode.

## No gain control from Linux

The card exposes no volume or mute control — the only element present is
`Capture Channel Map`:

```
$ amixer -c RX controls
numid=1,iface=PCM,name='Capture Channel Map'
```

Set levels on the device itself or in RODE Central. Software gain after the fact
is your only other option.

## Avoid hub chains

USB audio is isochronous and tolerates cascaded hubs poorly. If capture is
glitchy, plug the receiver into a port on the machine rather than through a dock
— a laptop dock can easily put three hubs between the device and the controller.
