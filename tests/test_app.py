import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from conteur.app import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_window_blocks_recording_without_the_receiver(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None)
    assert win.record_button.isEnabled() is False
    assert "Wireless PRO RX" in win.status_label.text()


def test_window_enables_recording_when_receiver_is_present(qapp):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    assert win.record_button.isEnabled() is True


def test_takes_list_shows_pending_then_final_name(qapp):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    row = win.add_take("2026-09-06_143208_sans-nom.wav")
    assert "…" in win.take_text(row)
    win.update_take(row, "2026-09-06_143208_le-loup.wav", "title")
    assert "le-loup" in win.take_text(row)
    assert "title" in win.take_text(row)
