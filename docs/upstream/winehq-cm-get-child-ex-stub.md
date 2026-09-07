<!--
  NOT FILED. This is a finished report waiting to be posted to the Wine
  Bugzilla (component setupapi). It needs an account we do not have.
  Status and context: see README.md in this folder.
  Paste everything below the line as-is.
-->

> **Status: not filed.** Ready to post to <https://bugs.winehq.org>, component
> `setupapi`. See [README.md](README.md) for why, and for what to do if you
> file it.

---

Summary:
  cfgmgr32: CM_Get_Child_Ex stub returns CR_SUCCESS without setting *child,
  causing callers to use an uninitialised DEVINST

Product:    Wine
Component:  setupapi   (implementation lives in dlls/cfgmgr32/cfgmgr32.c)
Version:    11.0
Severity:   normal
Keywords:   download, testcase-not-needed

--------------------------------------------------------------------------

DESCRIPTION

CM_Get_Child_Ex is a stub that returns CR_SUCCESS while leaving the caller's
output DEVINST untouched:

    dlls/cfgmgr32/cfgmgr32.c

    CONFIGRET WINAPI CM_Get_Child_Ex( DEVINST *child, DEVINST node,
                                      ULONG flags, HMACHINE machine )
    {
        FIXME( "child %p, node %#lx, flags %#lx, machine %p stub!\n",
               child, node, flags, machine );
        return CR_SUCCESS;
    }

    CONFIGRET WINAPI CM_Get_Child( DEVINST *child, DEVINST node, ULONG flags )
    {
        return CM_Get_Child_Ex( child, node, flags, NULL );
    }

Returning CR_SUCCESS tells the application the call succeeded and that *child
now holds a valid device instance handle. Because the stub never writes it,
the application proceeds to use whatever value happened to be in that stack
slot.

This is worse than an honest failure. A caller that checks the return value
correctly still ends up operating on garbage, and the resulting behaviour is
non-deterministic across runs. CM_Get_Parent had the same problem and was
changed to return CR_NO_SUCH_DEVNODE in 2018:

    https://www.winehq.org/pipermail/wine-devel/2018-April/125211.html

CM_Get_Child_Ex, CM_Get_Child and CM_Create_DevNodeW appear to share this
pattern.

--------------------------------------------------------------------------

OBSERVED IMPACT

Application: RODE Central (RODE Microphones), used to manage RODE Wireless PRO
audio recorders. Free download, no account required:
https://rode.com/en/software/rode-central

The application talks to the hardware over a vendor-specific HID interface
(usage page 0xff00) and this works correctly under Wine. Its "Recordings" tab,
which lists and manages the recordings stored on the device, always fails with:

    "Something went wrong during the device connection.
     Please unplug and plug the device again."

A full session traced with WINEDEBUG=+hid,+hidp,+usb shows the HID side is
entirely healthy:

    write output report  (app -> device)      1489
    process_hid_report   (device -> app)      1489
    NACK responses (0x4e)                        0
    HID errors of any kind                       0
    volume / mass-storage enumeration            0

The exchange is bidirectional and one-for-one; the device acknowledges every
command and refuses nothing. Replies decode correctly - report 0x0f returns the
device serial number, which matches the HID_UNIQ of the corresponding hidraw
node exactly.

The same trace contains, from the same thread, for the whole session:

    0180:fixme:setupapi:CM_Get_Child_Ex child 0000000006FBF960, node 0x7, \
        flags 0, machine 0000000000000000 stub!

973 calls, always the same devnode (0x7), 972 of them into the same stack slot
(0x6FBF960), at the cadence of the application's device refresh loop. Each
cycle polls the device over HID (succeeds), then attempts to descend the device
tree to the child node (fails). The application retries indefinitely and never
reaches the storage interface.

Because every call targets the same stack slot, the stale value there is stable
within a single run but differs between launches. The Recordings tab therefore
appears to work on some launches and fail on others with no configuration
change - which is what makes this bug easy to misdiagnose as a permissions or
device-access problem on the host.

--------------------------------------------------------------------------

STEPS TO REPRODUCE

1. Connect a RODE Wireless PRO transmitter or receiver (USB 19f7:0056 /
   19f7:0058) that has onboard recordings.
2. Give the user access to its hidraw nodes, e.g.

       KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="19f7", \
           MODE="0660", TAG+="uaccess"

3. Expose the device to Wine with PROTON_ENABLE_HIDRAW=0x19F7/0x0056
   (Proton-derived runner; under Bottles this is the "hidraw_devices" setting).
4. Run RODE Central. The Devices tab works: names, battery level and firmware
   version are read from the hardware correctly.
5. Open the Recordings tab.

Expected: the recordings stored on the device are listed.
Actual:   "Something went wrong during the device connection." The device list
          never populates, and the log fills with CM_Get_Child_Ex stub FIXMEs.

--------------------------------------------------------------------------

SUGGESTED FIX

Two separate steps, the first of which is trivial and strictly an improvement:

1. Make the stub fail honestly - return CR_NO_SUCH_DEVNODE (mirroring the 2018
   CM_Get_Parent change) rather than claiming success without writing *child.
   This will not make the application work, but it removes undefined behaviour
   and makes the failure diagnosable.

2. Implement CM_Get_Child_Ex against the device tree that Wine already
   maintains, so applications can walk from a composite USB devnode to its
   child interfaces.

--------------------------------------------------------------------------

ENVIRONMENT

Wine:     wine-experimental.bleeding.edge.11.0.424754.20260831 (TkG Plain),
          shipped as the "soda-11.0-7" runner in Bottles (flatpak
          com.usebottles.bottles)
OS:       Fedora, kernel 7.1.12-200.fc44.x86_64
Desktop:  KDE 6 / Wayland
GPU:      NVIDIA RTX 2080 Super, driver 610.57.04

Stub confirmed still present in wine-mirror/wine master as of 2026-09-06:
https://github.com/wine-mirror/wine/blob/master/dlls/cfgmgr32/cfgmgr32.c

The full HID trace (about 12000 lines) can be attached on request.
