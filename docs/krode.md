# krode — and what we contributed to it

**[github.com/LinuxRenaissance/krode](https://github.com/LinuxRenaissance/krode)**
· C · BSD-2-Clause

## What it is

A small C program that **deletes the onboard recordings** of a RØDE transmitter
over the vendor HID channel. That is its whole job.

It exists because the storage a RØDE transmitter presents is **read-only at the
device level** — `/sys/block/sdX/ro` reads `1`, on the Interview PRO and on the
Wireless PRO alike. You cannot `rm` a take, you cannot mount the volume
read-write, and reformatting is not an option either. The only way to free a
full card without RODE Central is to send the vendor command the firmware
listens for:

```
01 4A 01 00 00 …          report id 1, 17 bytes, written to /dev/hidrawN
```

krode is that command, wrapped in enough care to be usable.

**Why it matters here.** The mirror-image problem — getting recordings *off* the
device — has an open answer: the volume is readable, so anything can copy from
it. Erasing had none on Linux until krode. Between the two, a transmitter
becomes usable without ever launching the official application.

Credit where it is due: krode's author documented the protocol rather than only
shipping a binary. That is the reason any of the work below was possible.

## What we contributed

krode was written for the **Interview PRO**. We have Wireless PRO hardware, so
we could test whether the same command works on a different model — and it does.

### [Issue #2](https://github.com/LinuxRenaissance/krode/issues/2) — Wireless PRO confirmed

A measurement dump rather than a feature request: USB descriptors for all four
Wireless PRO device IDs, a trace of RODE Central's own HID traffic under Wine,
and then the confirmation that `01 4A 01` erases a Wireless PRO TX — tested on
two transmitters, both connected directly and **through the charging case**.

Two findings in it are useful beyond this one model:

- **Byte [3] of the reply is a percentage, not a status code.** The reply
  `02 4A 41 <n>` counts `00`, `05`, `0A` … `64`. krode documented "status 100 =
  success"; 100 is the device saying *100 % complete*.
- **The HID serial equals the FAT volume UUID** (`HID_UNIQ=800AF63E` ↔ volume
  `800A-F63E`). Two transmitters share PID `0x0056`, so VID/PID cannot tell them
  apart — this can, and it is the only thing that can when they are docked.

The issue also records the conclusion of a long detour: the official application
under Wine is **not** a workable alternative. Its Recordings tab fails on a Wine
stub unrelated to RØDE — see [rode-central-wine.md](rode-central-wine.md).

### [PR #4](https://github.com/LinuxRenaissance/krode/pull/4) — wait for the erase to finish

krode reported success as soon as the device acknowledged the command. But the
device answers `0x41` immediately and *then* erases, streaming progress reports
as it goes. On a full card that takes seconds.

Worse, the transmitter **re-enumerates when it finishes**: the hidraw node
disappears and comes back under a different number. Read code that treats a
vanished device as an error will report failure at the exact moment the erase
succeeded.

The fix reads reports until 100 %, and treats a disconnect *after progress has
been seen* as a normal ending rather than an error.

### [PR #3](https://github.com/LinuxRenaissance/krode/pull/3) — closed by us

Our first attempt bundled Wireless PRO support with the progress fix. We then
found [PR #1](https://github.com/LinuxRenaissance/krode/pull/1), which already
added Wireless PRO support and was open before ours. Duplicating someone else's
open work is not a contribution, so we closed ours and reopened the part that
was genuinely missing — the progress handling — as PR #4.

## Using it with a Wireless PRO

**Copy and verify your recordings first.** The erase is irreversible, there is no
confirmation prompt, and the read-only storage means nothing can be recovered
afterwards.

Both paths work — a transmitter connected directly, or docked in the charging
case. Docked is the connection RØDE designs for, and the one worth using: over
a SuperSpeed link the case reads at **38.6 MB/s**, so the copy you must do
first takes about twelve minutes even for a completely full card.

Check the link before blaming the hardware: on a USB 2 cable the same case
reads at 21.8 MB/s and *declares itself a USB 2.0 device*. See
[device-map.md](device-map.md).

Node numbers are not stable across a replug. Re-read `HID_UNIQ` from sysfs
immediately before writing rather than trusting a path resolved earlier, and
match it against the volume UUID of the card you verified.

After the erase, unplug and replug: the volume comes back without a filesystem
until you do, and waiting does not help.
