# Running RODE Central under Wine

RODE Central is Windows/macOS only. It runs under Wine, but it will show an
empty device list until two independent things are fixed. Neither produces a
visible change on its own.

Verified with: Bottles 67.1 (flatpak), runner `soda-11.0-7`, win64/win10 prefix,
Fedora 44, Wireless PRO firmware 1.0.2.

## Why audio configuration is a dead end

RODE Central does not reach the microphones through USB audio. It uses a
**vendor-specific HID** channel — the `/dev/hidrawN` nodes. Looking at ALSA or
PipeWire when the device list is empty is time spent in the wrong place.

## Lock 1 — kernel permissions

`/dev/hidraw*` nodes are `root:root 0600`. Wine runs as your user, so every
`open()` returns `EACCES`.

```sh
sudo install -m 0644 udev/70-rode.rules /etc/udev/rules.d/70-rode.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add /sys/class/hidraw/hidraw*
sudo udevadm settle
```

Check both the mode and the tag:

```sh
ls -l /dev/hidraw*                                  # want crw-rw----+
udevadm info -q property -p /sys/class/hidraw/hidraw11 | grep CURRENT_TAGS
                                                    # want :seat:uaccess:
```

The `+` on the permission string is the ACL that `uaccess` asked logind to
place. Without it, the rule matched but logind did not act.

### The trigger pitfall

```sh
# Does nothing for hidraw nodes:
udevadm trigger --attr-match=idVendor=19f7

# Correct:
udevadm trigger --action=add /sys/class/hidraw/hidraw*
```

`--attr-match` only tests attributes on the device itself. A hidraw node carries
almost none — `idVendor` belongs to the parent USB device. The rule works
because it uses `ATTRS{...}` (plural), which walks up the parent chain; the
trigger has no such behaviour. Unplugging and replugging the device also works,
and additionally proves the rule will apply at boot.

## Lock 2 — exposing devices to Wine

Even readable, the nodes are not offered to the application. `winebus.sys`
presents in raw-HID mode only what it has been told to present, via the
`PROTON_ENABLE_HIDRAW` environment variable (Proton-derived runners; Bottles
documents this as tested with Soda).

In Bottles, the bottle's `bottle.yml`:

```yaml
Parameters:
  hidraw_devices:
    - 0x19F7/0x0056   # Wireless PRO TX
    - 0x19F7/0x0058   # Wireless PRO RX
    - 0x19F7/0x007A   # Charge Case+
    - 0x19F7/0x007C   # Charge Case+ (storage interface)
```

Format is `0xVVVV/0xPPPP`, uppercase hex. Bottles normalises and turns the list
into:

```
PROTON_ENABLE_HIDRAW=0x19F7/0x0056,0x19F7/0x0058,0x19F7/0x007A,0x19F7/0x007C
```

Edit the file with Bottles closed, or it will be overwritten.

### This is not a sandbox setting

In the Bottles UI the HIDRAW device list sits under the **Sandbox** tab. That
placement is misleading: the code path
(`backend/wine/winecommand.py`, `apply_hidraw_preferences`) reads it
unconditionally, whether or not the sandbox is enabled. It is required even with
`sandbox: false`.

## Verifying, properly

An application that "looks like it works" is not evidence. Check that Wine's HID
host process actually holds the descriptors open:

```sh
readlink /proc/$(pgrep -f winedevice.exe | tail -1)/fd/* | grep hidraw
```

Expected — one line per RØDE device:

```
/dev/hidraw9
/dev/hidraw10
/dev/hidraw11
/dev/hidraw12
```

And confirm the variable actually reached the runner:

```sh
tr '\0' '\n' < /proc/$(pgrep wineserver | head -1)/environ | grep HIDRAW
```

## Known-good result

RODE Central lists the devices with their user-assigned names read from the
hardware, battery level, firmware version, and offers firmware updates.

Firmware updates over Wine are unverified here and carry obvious risk; consider
doing those from a supported OS.

## Not covered

- Whether the same approach works with plain `wine` outside Bottles. It should
  — `PROTON_ENABLE_HIDRAW` is read by Proton-derived runners — but this was not
  tested.
- Behaviour with RØDE interfaces other than the Wireless PRO family.
