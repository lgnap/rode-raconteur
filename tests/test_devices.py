"""Seeing the cards: which nodes are real, and which transmitter each belongs to."""

from pathlib import Path

from conteur.devices import BlockCandidate, StorageDevice, find_storage


def _hidraw():
    return {"800AF63E": Path("/dev/hidraw6"), "800A92D6": Path("/dev/hidraw5")}


def test_a_card_is_joined_to_its_transmitter_by_the_volume_serial(tmp_path):
    """The FAT volume serial equals the transmitter's HID_UNIQ. It is the only
    identifier that works both docked and connected directly."""
    found = find_storage(
        lister=lambda: [BlockCandidate(Path("/dev/sdb"), "800A-F63E", "007c", 31_037_849_600)],
        hidraw_lister=_hidraw,
    )
    assert found == [StorageDevice(serial="800A-F63E", block=Path("/dev/sdb"),
                                   hidraw=Path("/dev/hidraw6"), via_case=True)]


def test_a_lun_without_medium_is_not_an_error(tmp_path):
    """Size zero means the transmitter has been taken out of the case."""
    found = find_storage(
        lister=lambda: [BlockCandidate(Path("/dev/sdc"), None, "007c", 0)],
        hidraw_lister=_hidraw,
    )
    assert found == []


def test_the_same_card_seen_twice_is_deduplicated(tmp_path):
    """After an erase the transmitter re-enumerates and presents its own
    storage as well as the case's. The case's node is the usable one: the
    transmitter's carries no filesystem, is slower, and arrives root-only."""
    found = find_storage(
        lister=lambda: [
            BlockCandidate(Path("/dev/sdd"), "800A-F63E", "0056", 31_037_849_600),
            BlockCandidate(Path("/dev/sdb"), "800A-F63E", "007c", 31_037_849_600),
        ],
        hidraw_lister=_hidraw,
    )
    assert [d.block for d in found] == [Path("/dev/sdb")]
    assert found[0].via_case is True


def test_a_transmitter_connected_directly_is_used_when_it_is_the_only_node():
    found = find_storage(
        lister=lambda: [BlockCandidate(Path("/dev/sdd"), "800A-F63E", "0056", 31_037_849_600)],
        hidraw_lister=_hidraw,
    )
    assert [d.block for d in found] == [Path("/dev/sdd")]
    assert found[0].via_case is False


def test_a_card_without_a_readable_serial_is_skipped():
    found = find_storage(
        lister=lambda: [BlockCandidate(Path("/dev/sdd"), None, "0056", 31_037_849_600)],
        hidraw_lister=_hidraw,
    )
    assert found == []


def test_a_card_with_no_matching_hidraw_is_still_listed():
    """It can be imported; only erasing needs the HID node."""
    found = find_storage(
        lister=lambda: [BlockCandidate(Path("/dev/sdb"), "800A-F63E", "007c", 31_037_849_600)],
        hidraw_lister=dict,
    )
    assert found[0].hidraw is None
