"""The vendor HID erase, and the four ways it can end."""

import errno
import time
import pytest
from pathlib import Path

from conteur.erase import (
    ERASE_COMMAND, FAILED, REENUMERATED, REFUSED, SUCCESS, UNKNOWN, erase,
)


class FakeNode:
    """Replays what a transmitter answers. Reading None means it vanished.

    Replies can be bytes, None, or an exception instance to raise.
    """

    def __init__(self, replies, write_raises=None):
        self.replies = list(replies)
        self.written = []
        self.write_raises = write_raises
        self.closed = False
        self.reads = 0

    def write(self, data):
        self.written.append(data)
        if self.write_raises:
            raise self.write_raises
        return len(data)

    def read(self):
        self.reads += 1
        if not self.replies:
            raise TimeoutError
        reply = self.replies.pop(0)
        if reply is None:
            raise OSError(errno.ENODEV, "No such device")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


def _progress(*percents):
    return [bytes([0x02, 0x4A, 0x41, p]) + bytes(13) for p in percents]


def test_the_command_is_the_documented_seventeen_bytes():
    assert ERASE_COMMAND[:3] == bytes([0x01, 0x4A, 0x01])
    assert len(ERASE_COMMAND) == 17


def test_a_full_run_of_progress_reports_is_a_success():
    node = FakeNode(_progress(*range(0, 101, 5)))
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == SUCCESS
    assert result.percents[0] == 0 and result.percents[-1] == 100
    assert len(result.percents) == 21
    assert node.written == [ERASE_COMMAND]


def test_the_node_vanishing_after_progress_is_the_normal_ending():
    """The transmitter re-enumerates when it finishes. Code that treats a
    vanished device as an error reports failure at the exact moment the erase
    succeeded."""
    node = FakeNode(_progress(0, 5, 10) + [None])
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == REENUMERATED
    assert result.percents == [0, 5, 10]


def test_the_node_vanishing_without_progress_is_a_failure():
    node = FakeNode([None])
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == FAILED


def test_a_nack_is_a_refusal():
    node = FakeNode([bytes([0x02, 0x4A, 0x4E, 0x00]) + bytes(13)])
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == REFUSED


def test_silence_is_unknown_not_failure():
    """The command may well have gone through. Declaring failure would be a
    guess; the card has to be re-inventoried instead."""
    node = FakeNode([])
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node, idle_timeout_s=0.05)
    assert result.verdict == UNKNOWN


def test_unrelated_reports_are_ignored():
    node = FakeNode([bytes([0x0B]) + bytes(60)] + _progress(*range(0, 101, 5)))
    assert erase(Path("/dev/hidraw6"), opener=lambda _: node).verdict == SUCCESS


def test_transient_read_error_is_retried_not_reported_as_success():
    """BlockingIOError (EAGAIN) is a transient error, not device disappearance.
    The node still works after the error, so erase should continue and succeed."""
    transient = BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    node = FakeNode(
        [_progress(0, 5, 10)[0], transient] + _progress(*range(15, 101, 5))
    )
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == SUCCESS
    assert result.percents[0] == 0 and result.percents[-1] == 100


def test_device_disappearance_is_distinguished_from_transient_error():
    """OSError with ENODEV errno is a real disappearance, not a transient error."""
    node = FakeNode(_progress(0, 5, 10) + [OSError(errno.ENODEV, "No such device")])
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == REENUMERATED
    assert result.percents == [0, 5, 10]


def test_unclassified_read_error_is_propagated():
    """An OSError we don't recognize as device disappearance should propagate,
    not be silently converted into a verdict."""
    unknown_error = OSError(errno.EACCES, "Permission denied")
    node = FakeNode(_progress(0, 5) + [unknown_error])
    with pytest.raises(OSError) as exc_info:
        erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert exc_info.value.errno == errno.EACCES
    assert node.closed


def test_write_error_propagates_and_closes():
    """An error from write() should propagate and the node must still be closed."""
    write_error = IOError("Write failed")
    node = FakeNode([], write_raises=write_error)
    with pytest.raises(IOError) as exc_info:
        erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert str(exc_info.value) == "Write failed"
    assert node.closed


def test_persistent_transient_errors_do_not_hang():
    """A node that always raises BlockingIOError(EAGAIN) must return UNKNOWN within
    its timeout budget, not hang forever. Regression test for the critical defect
    where transient-error retry lacked a timeout check."""
    class AlwaysEAGAIN:
        def __init__(self):
            self.closed = False
            self.reads = 0

        def write(self, data):
            return len(data)

        def read(self):
            self.reads += 1
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")

        def close(self):
            self.closed = True

    node = AlwaysEAGAIN()
    timeout_s = 0.1
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node, idle_timeout_s=timeout_s)

    assert result.verdict == UNKNOWN
    assert node.closed
    # With a 10 ms sleep per retry and 0.1 s timeout, expect ~10 read() calls.
    # Allow generous margin to catch any significant regression (e.g., if sleep were removed).
    assert node.reads < 100, f"Expected ~10 calls, got {node.reads} — busy-loop regression?"


def test_persistent_timeouts_do_not_burn_cpu():
    """A node that always raises TimeoutError must return UNKNOWN within its timeout,
    and must not busy-loop. Regression test for the busy-loop defect where the
    loop checked the deadline only on one path."""
    node = FakeNode([])  # Empty replies = all TimeoutError
    timeout_s = 0.1
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node, idle_timeout_s=timeout_s)

    assert result.verdict == UNKNOWN
    # With a 10 ms sleep per retry and 0.1 s timeout, expect ~10 read() calls.
    # Allow generous margin to catch any significant regression (e.g., if sleep were removed).
    assert node.reads < 100, f"Expected ~10 calls, got {node.reads} — busy-loop regression?"
