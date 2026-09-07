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

## The Recordings tab does not work — and it is not your setup

With both locks in place, the Devices tab works fully. The **Recordings** tab
still fails, with:

> Something went wrong during the device connection.
> Please unplug and plug the device again.

Unplugging does not help. Neither does anything else on the Linux side: this is
a Wine bug, not a permission, mount, or drive-letter problem.

### What the trace shows

Captured with `WINEDEBUG=+hid,+hidp,+usb` set in the bottle's
`Environment_Variables` (~12 000 HID lines over one session, one Wireless PRO TX
connected):

| Observation | Count |
|---|---|
| `write output report` (app → device) | 1489 |
| `process_hid_report` (device → app) | 1489 |
| NACK responses (`0x4e`) | **0** |
| HID errors of any kind | **0** |
| Volume / mass-storage enumeration | **0** |

The vendor HID exchange is bidirectional, one-for-one, and entirely successful.
The device acknowledges (`0x41`) every single command. It never refuses
anything, and the app never touches a filesystem — so `D:`, `removable`,
udev rules on `block` devices and mount points are all irrelevant to this
failure.

Responses even decode correctly. Report `0x0f` returns the device serial:

```
→ 0f 00 01 ...                              (report id 15, 64 bytes)
← 10 00 41 00 d6 92 0a 80 00 00 ...         (report id 16, 64 bytes)
            ^^^^^^^^^^^ little-endian 0x800A92D6
```

`0x800A92D6` is exactly the `HID_UNIQ` of that transmitter's hidraw node. The
protocol is working.

### Where it actually breaks

The same trace contains, from the same thread, throughout the whole session:

```
0180:fixme:setupapi:CM_Get_Child_Ex child 0000000006FBF960, node 0x7, flags 0, machine 0000000000000000 stub!
```

**973 times. Always the same devnode (`0x7`), always the same stack slot.** The
cadence matches the app's device-refresh loop: each cycle polls the device over
HID (succeeds), then tries to walk down the device tree to the child node
(fails). An application that got its answer does not ask the same question 973
times.

`CM_Get_Child_Ex` is how a Windows application descends from a composite USB
devnode to its child interfaces — the standard way to find the storage endpoint
belonging to the same physical device as the HID interface. In Wine it is a
stub, and the stub is worse than a plain failure
([`dlls/cfgmgr32/cfgmgr32.c`](https://github.com/wine-mirror/wine/blob/master/dlls/cfgmgr32/cfgmgr32.c)):

```c
CONFIGRET WINAPI CM_Get_Child_Ex( DEVINST *child, DEVINST node, ULONG flags, HMACHINE machine )
{
    FIXME( "child %p, node %#lx, flags %#lx, machine %p stub!\n", child, node, flags, machine );
    return CR_SUCCESS;
}
```

It returns `CR_SUCCESS` **without ever writing `*child`**. The caller believes
the call succeeded and reads an uninitialised `DEVINST` off its own stack.

### Why it is intermittent

This also explains the behaviour that looks like flakiness. The 972 calls all
target the same stack slot, so whatever garbage sits there is stable *within* a
run — but differs *between* launches, depending on what the preceding call left
behind. The Recordings tab therefore appears to work sometimes and not others,
with no change to the configuration. Observed directly: the same bottle, the
same devices, working in one launch and failing in the next.

### Consequences

- No amount of udev, mount, drive-letter, `removable` type, or DXVK
  configuration will fix this. All of it was tried; none of it changes anything.
- Fixing it means implementing `CM_Get_Child_Ex` in Wine. Making the stub return
  `CR_NO_SUCH_DEVNODE` instead — as was done for `CM_Get_Parent` in 2018 —
  would at least turn undefined behaviour into an honest failure, but is
  unlikely to make the tab work.
- **To manage recordings on Linux, use a native tool instead.** See
  [`docs/wireless-pro-hid-observations.md`](wireless-pro-hid-observations.md)
  for the vendor protocol, including the erase command, which works reliably.

## Reporting this upstream

A finished Wine bug report for the `CM_Get_Child_Ex` stub is kept in
[upstream/winehq-cm-get-child-ex-stub.md](upstream/winehq-cm-get-child-ex-stub.md).
**It has not been filed** — it needs a Bugzilla account. If you have one, posting
it as-is would help; see [upstream/README.md](upstream/README.md).

## Not covered

- Whether the same approach works with plain `wine` outside Bottles. It should
  — `PROTON_ENABLE_HIDRAW` is read by Proton-derived runners — but this was not
  tested.
- Behaviour with RØDE interfaces other than the Wireless PRO family.
