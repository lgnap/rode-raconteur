# Upstream — what we owe other projects

Findings from this repo that belong somewhere else, and where each one stands.

Nothing here is a draft kept for its own sake. Each file is a report that is
**finished and ready to send**; what is missing is the sending. Keeping them
versioned means the work does not evaporate if nobody gets round to it, and
anyone who does can post it without redoing the analysis.

| Report | Where it goes | Status |
|---|---|---|
| [`winehq-cm-get-child-ex-stub.md`](winehq-cm-get-child-ex-stub.md) | Wine Bugzilla, component `setupapi` | **not filed** — needs a Bugzilla account |

## Already sent

For the record, so nobody re-reports these:

- **[krode issue #2](https://github.com/LinuxRenaissance/krode/issues/2)** —
  Wireless PRO erase confirmed, the percentage-not-status-code finding, and the
  `HID_UNIQ` ↔ volume UUID mapping.
- **[krode PR #4](https://github.com/LinuxRenaissance/krode/pull/4)** — wait for
  the erase to finish, and treat the re-enumeration as a normal ending.

See [../krode.md](../krode.md).

## `winehq-cm-get-child-ex-stub.md`

**What it says.** Wine's `CM_Get_Child_Ex` returns `CR_SUCCESS` without writing
the caller's output `DEVINST`. A correctly-written caller therefore proceeds on
an uninitialised stack value. This is worse than an honest failure, and it is
non-deterministic across runs — which is exactly what made it so confusing to
diagnose from the outside.

**Why we care.** It is the reason RODE Central's Recordings tab fails under Wine,
after every other explanation had been ruled out — permissions, mounts, drive
letters, removable-device type, DXVK. The vendor HID exchange itself is
completely healthy: 1489 reports out, 1489 in, zero refusals, and the device
serial decodes correctly. The application then calls `CM_Get_Child_Ex` 973 times
on the same devnode and never gets anywhere. Full analysis in
[../rode-central-wine.md](../rode-central-wine.md).

**Why it is not filed.** The Wine Bugzilla requires an account, and creating one
was not something we wanted to do on the spot. The report is complete: it names
the component, quotes the current source, proposes two separable fixes — return
`CR_NO_SUCH_DEVNODE` instead of lying, which mirrors what was done for
`CM_Get_Parent` in 2018, or implement it properly — and it does not need a test
case attached.

**If you file it:** the HID trace it refers to is not in this repo (996 kB
uncompressed). It can be regenerated with `WINEDEBUG=+hid,+hidp,+usb` set in the
bottle's `Environment_Variables`, with the transmitters connected.
