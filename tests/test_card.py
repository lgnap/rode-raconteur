"""Reading a RØDE card: identity, inventory, and bytes."""

import hashlib

import pytest

from conteur.card import Card
from tests.fat_image import build


def test_the_volume_identifies_the_card(tmp_path):
    """The FAT volume serial is the join key with the transmitter's HID_UNIQ.

    Read from the boot sector rather than from blkid, which has not re-probed
    immediately after an erase.
    """
    img = tmp_path / "card.img"
    build(img, {"00001_Source.WAV": b"x" * 100})
    with img.open("rb") as fh:
        card = Card(fh)
    assert card.serial == "800A-F63E"
    assert card.label == "WirelessPRO"
    assert card.oem == "RODE"


def test_takes_are_listed_with_their_long_names(tmp_path):
    img = tmp_path / "card.img"
    build(img, {"00001_Source-Baffle.WAV": b"a" * 700,
                "00002_Source-Baffle.WAV": b"b" * 300})
    with img.open("rb") as fh:
        takes = Card(fh).takes()
    assert [t.name for t in takes] == ["00001_Source-Baffle.WAV",
                                       "00002_Source-Baffle.WAV"]
    assert [t.size for t in takes] == [700, 300]


def test_a_zero_byte_entry_is_a_take_in_progress_and_is_skipped(tmp_path):
    """A transmitter starts recording the instant it leaves the case, so a
    device you have just connected always shows one open, zero-byte take.

    It must never be imported, and above all never recorded — the erase lock
    would open on an empty proof.
    """
    img = tmp_path / "card.img"
    build(img, {"00001_Source.WAV": b"a" * 500, "00002_Source.WAV": b""})
    with img.open("rb") as fh:
        takes = Card(fh).takes()
    assert [t.name for t in takes] == ["00001_Source.WAV"]


def test_streaming_returns_the_file_intact(tmp_path):
    img = tmp_path / "card.img"
    content = bytes(range(256)) * 40        # 10 240 bytes, 20 clusters
    build(img, {"00001_Source.WAV": content})
    with img.open("rb") as fh:
        card = Card(fh)
        take = card.takes()[0]
        got = b"".join(card.stream(take))
    assert got == content
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(content).hexdigest()


def test_streaming_survives_a_fragmented_file(tmp_path):
    """A reader that assumes contiguous clusters passes on a freshly written
    card and fails on a used one."""
    img = tmp_path / "card.img"
    a = bytes(range(256)) * 20
    b = bytes(reversed(range(256))) * 20
    build(img, {"A.WAV": a, "B.WAV": b}, fragment=True)
    with img.open("rb") as fh:
        card = Card(fh)
        by_name = {t.name: t for t in card.takes()}
        assert b"".join(card.stream(by_name["A.WAV"])) == a
        assert b"".join(card.stream(by_name["B.WAV"])) == b


def test_a_non_fat32_device_is_refused(tmp_path):
    img = tmp_path / "empty.img"
    img.write_bytes(bytes(4096))
    with img.open("rb") as fh:
        with pytest.raises(ValueError, match="not FAT32"):
            Card(fh)


# Task 4: Verified copy

from conteur.card import PART_SUFFIX, VerificationError, copy_verified


def test_the_copy_is_hashed_while_it_is_written(tmp_path):
    """One read of the device yields both the file and its proof. Reading it
    twice would double the only expensive part of the operation."""
    img = tmp_path / "card.img"
    content = bytes(range(256)) * 40
    build(img, {"00001_Source.WAV": content})
    dest = tmp_path / "out.wav"
    with img.open("rb") as fh:
        card = Card(fh)
        digest = copy_verified(card, card.takes()[0], dest)
    assert dest.read_bytes() == content
    assert digest == hashlib.sha256(content).hexdigest()


def test_a_failed_verification_leaves_a_part_file_and_raises(tmp_path):
    """The .part is kept, not deleted: we do not destroy data on our own
    initiative, even bad data, and the source is intact anyway."""
    img = tmp_path / "card.img"
    build(img, {"00001_Source.WAV": b"z" * 600})
    dest = tmp_path / "out.wav"
    with img.open("rb") as fh:
        card = Card(fh)
        with pytest.raises(VerificationError):
            copy_verified(card, card.takes()[0], dest, attempts=1,
                          verifier=lambda path: "0" * 64)
    assert dest.with_name(dest.name + PART_SUFFIX).exists()
    assert not dest.exists()


def test_a_transient_failure_is_retried_once(tmp_path):
    """A passing USB error is plausible; two in a row are not."""
    img = tmp_path / "card.img"
    content = b"y" * 600
    build(img, {"00001_Source.WAV": content})
    dest = tmp_path / "out.wav"
    calls = []

    def flaky(path):
        calls.append(path)
        if len(calls) == 1:
            return "0" * 64
        return hashlib.sha256(path.read_bytes()).hexdigest()

    with img.open("rb") as fh:
        card = Card(fh)
        digest = copy_verified(card, card.takes()[0], dest, verifier=flaky)
    assert len(calls) == 2
    assert digest == hashlib.sha256(content).hexdigest()
    assert dest.exists()
