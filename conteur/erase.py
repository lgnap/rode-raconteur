"""Erasing a transmitter's onboard recordings, over the vendor HID channel.

The storage is read-only at device level, so this is the only way. The command
is a plain write to /dev/hidrawN — the HID interface declares no OUT endpoint,
so the kernel turns it into a SET_REPORT on the control pipe. It works; a
failure will surface as a control-transfer error rather than a bulk one.

The command itself — that a vendor HID channel erases the card at all, its
opcode and the shape of its report — is krode's finding, published rather than
merely shipped as a binary: github.com/LinuxRenaissance/krode, BSD-2-Clause.
No code was taken from it; this is a reimplementation from what its author
documented, plus what we measured on Wireless PRO hardware they do not have and
sent back to them. See docs/krode.md, which is also where the reply byte is
explained: it counts percent complete, and is not a status code — reading a
progress report as an error is the mistake this module exists not to make.
"""

import errno
import os
import select
import time
from dataclasses import dataclass, field
from pathlib import Path

# Report id 1, 17 bytes: [id][opcode][param][padding].
ERASE_COMMAND = bytes([0x01, 0x4A, 0x01]) + bytes(14)
REPLY_ID = 0x02
OPCODE = 0x4A
ACK = 0x41
NACK = 0x4E
COMPLETE = 100
RETRY_PAUSE_S = 0.01  # Sleep on transient errors to avoid busy-looping

SUCCESS = "complete"
REENUMERATED = "reenumerated"
FAILED = "failed"
REFUSED = "refused"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class EraseResult:
    verdict: str
    percents: list[int] = field(default_factory=list)


class _RealNode:
    def __init__(self, path: Path):
        self.fd = os.open(str(path), os.O_RDWR | os.O_NONBLOCK)

    def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    def read(self) -> bytes:
        ready, _, _ = select.select([self.fd], [], [], 0.5)
        if not ready:
            raise TimeoutError
        return os.read(self.fd, 64)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def default_opener(path: Path):
    return _RealNode(path)


def erase(node_path: Path, opener=default_opener,
          idle_timeout_s: float = 10.0) -> EraseResult:
    """Send the erase and follow it to its end.

    Byte [3] of the reply is a percentage, not a status code: the replies run
    00, 05, 0A … 64. Twenty-one of them, and they arrive in a burst after the
    work rather than during it — so a progress bar fed by them will jump
    straight from 0 to 100.

    The transmitter re-enumerates when it finishes, so its node disappearing
    *after* progress has been seen is a normal ending. Disappearing before any
    progress is a failure. Silence is neither: the command may have gone
    through, and the honest answer is to re-inventory the card.
    """
    node = opener(node_path)
    percents: list[int] = []
    deadline = time.monotonic() + idle_timeout_s
    try:
        node.write(ERASE_COMMAND)
        while True:
            # A transmitter that answers nothing at all would otherwise hold
            # this loop for ever, with the window frozen behind it and no way
            # out but killing the application. Silence is not a failure — the
            # command may well have gone through — so it ends the wait and is
            # reported as UNKNOWN, which is what sends the caller back to
            # re-inventory the card.
            if time.monotonic() >= deadline:
                return EraseResult(UNKNOWN, percents)
            try:
                data = node.read()
            except (TimeoutError, OSError) as error:
                if isinstance(error, OSError) and not isinstance(error, TimeoutError):
                    if error.errno in (errno.ENODEV, errno.ENXIO, errno.EIO):
                        return EraseResult(REENUMERATED if percents else FAILED, percents)
                    if error.errno not in (errno.EAGAIN, errno.EINTR):
                        # Anything else is a real error, and turning it into a
                        # retry would hide it behind ten seconds of silence.
                        raise
                # Nothing arrived, so we go round again — but a non-blocking
                # node returns EAGAIN immediately, which measured out at 2.4
                # million reads a second: one core pinned for the whole erase,
                # for nothing. The pause costs the erase nothing (the reports
                # arrive in a burst at the end anyway) and costs the machine
                # everything it was burning
                # (test_persistent_timeouts_do_not_burn_cpu).
                time.sleep(RETRY_PAUSE_S)
                continue
            # The deadline is pushed back only when a report actually arrives.
            # Resetting it on a timeout or a retry would make the wait renew
            # itself for ever on a transmitter that never answers — the hang
            # the check at the top of the loop exists to prevent
            # (test_persistent_transient_errors_do_not_hang).
            deadline = time.monotonic() + idle_timeout_s
            if len(data) < 4 or data[0] != REPLY_ID or data[1] != OPCODE:
                continue
            status, value = data[2], data[3]
            if status == NACK:
                return EraseResult(REFUSED, percents)
            if status != ACK:
                continue
            percents.append(value)
            if value >= COMPLETE:
                return EraseResult(SUCCESS, percents)
    finally:
        node.close()
