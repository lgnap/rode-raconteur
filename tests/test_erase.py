"""The vendor HID erase, and the four ways it can end."""

from pathlib import Path

from conteur.erase import (
    ERASE_COMMAND, FAILED, REENUMERATED, REFUSED, SUCCESS, UNKNOWN, erase,
)


class FakeNode:
    """Replays what a transmitter answers. Reading None means it vanished."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.written = []

    def write(self, data):
        self.written.append(data)
        return len(data)

    def read(self):
        if not self.replies:
            raise TimeoutError
        reply = self.replies.pop(0)
        if reply is None:
            raise OSError(19, "No such device")
        return reply

    def close(self):
        pass


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
    result = erase(Path("/dev/hidraw6"), opener=lambda _: node)
    assert result.verdict == UNKNOWN


def test_unrelated_reports_are_ignored():
    node = FakeNode([bytes([0x0B]) + bytes(60)] + _progress(*range(0, 101, 5)))
    assert erase(Path("/dev/hidraw6"), opener=lambda _: node).verdict == SUCCESS
