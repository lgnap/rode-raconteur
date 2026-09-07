"""Erasing a transmitter's onboard recordings, over the vendor HID channel.

The storage is read-only at device level, so this is the only way. The command
is a plain write to /dev/hidrawN — the HID interface declares no OUT endpoint,
so the kernel turns it into a SET_REPORT on the control pipe. It works; a
failure will surface as a control-transfer error rather than a bulk one.
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
            # Check idle timeout at the top of every iteration, regardless of which
            # path we take. This ensures no path can hang or busy-loop indefinitely.
            if time.monotonic() >= deadline:
                return EraseResult(UNKNOWN, percents)
            try:
                data = node.read()
            except TimeoutError:
                continue
            except OSError as error:
                # Only treat as device disappearance if errno indicates that.
                if error.errno in (errno.ENODEV, errno.ENXIO, errno.EIO):
                    return EraseResult(REENUMERATED if percents else FAILED, percents)
                # Transient errors like EAGAIN or EINTR should be retried, but with
                # a small pause to avoid busy-looping on persistent failures.
                if error.errno in (errno.EAGAIN, errno.EINTR):
                    time.sleep(RETRY_PAUSE_S)
                    continue
                # Unknown error: propagate it rather than silently converting to a verdict.
                raise
            # Reset deadline only when a report actually arrives, so silence is silence
            # whatever produced it.
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
