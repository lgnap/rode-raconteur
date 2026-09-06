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


def test_stop_writes_the_wav_before_submitting_the_naming_job(qapp, tmp_path, monkeypatch):
    import numpy as np

    import conteur.app as app_mod

    class Dev:
        index = 0
        name = "Wireless PRO RX"

    class FakeQueue:
        def __init__(self):
            self.submitted = []

        def start(self):
            pass

        def submit(self, path, when, on_done):
            # On note l'existence du fichier AU MOMENT de la soumission : c'est
            # l'ordre qui est testé, pas seulement le résultat final.
            self.submitted.append((path, path.exists()))

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record", lambda pa, device, stop: np.array([1, 2, 3], dtype=np.int16)
    )
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = FakeQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    win.toggle_recording()   # démarre
    win.toggle_recording()   # arrête

    assert len(queue.submitted) == 1
    path, existed_at_submit = queue.submitted[0]
    assert existed_at_submit is True
    assert path.parent == tmp_path
    assert "sans-nom" in path.name
