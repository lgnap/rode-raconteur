from conteur.rx_device import find_rx


class FakePyAudio:
    def __init__(self, devices):
        self._devices = devices

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, i):
        return self._devices[i]


def _dev(name, inputs=2):
    return {"name": name, "maxInputChannels": inputs}


def test_finds_the_receiver_among_other_devices():
    pa = FakePyAudio([
        _dev("HDA Intel PCH"),
        _dev("Wireless PRO RX: USB Audio (hw:3,0)"),
    ])
    found = find_rx(pa)
    assert found is not None
    assert found.index == 1


def test_transmitter_is_not_accepted():
    assert find_rx(FakePyAudio([_dev("Wireless PRO TX")])) is None


def test_output_only_device_is_ignored():
    pa = FakePyAudio([_dev("Wireless PRO RX", inputs=0)])
    assert find_rx(pa) is None


def test_absent_receiver_returns_none():
    assert find_rx(FakePyAudio([_dev("HDA Intel PCH")])) is None


def test_matching_is_case_insensitive():
    assert find_rx(FakePyAudio([_dev("wireless pro rx")])) is not None
