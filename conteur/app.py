"""Fenêtre unique. Toute la logique vit ailleurs ; ce module ne fait que l'UI."""

import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from conteur.job import name_recording
from conteur.job import rename_take as rename_take_file
from conteur.paths import build_name, destination_dir
from conteur.recorder import record, write_wav
from conteur.rx_device import find_rx as default_find_rx
from conteur.signal import DBFS_FLOOR, rms_dbfs
from conteur.transcribe import load_model
from conteur.worker import NamingQueue

NO_DEVICE = "Branche le Wireless PRO RX pour enregistrer."


class MainWindow(QMainWindow):
    take_named = Signal(int, str, str)

    def __init__(self, find_rx=None, queue=None, pa=None):
        super().__init__()
        self._find_rx = find_rx or (lambda: default_find_rx(pa))
        self._queue = queue
        self._pa = pa
        self._stop = threading.Event()
        self._rows: list[QListWidgetItem] = []
        self._takes_meta: list[tuple[Path, datetime]] = []
        # Lignes renommées à la main : un résultat automatique tardif (souvent
        # un "échec" dû à la course entre le renommage manuel et la file, qui
        # tient encore le chemin provisoire d'origine) ne doit plus les toucher.
        self._manually_renamed_rows: set[int] = set()

        self.setWindowTitle("Conteur")
        self.status_label = QLabel()
        self.record_button = QPushButton("Enregistrer")
        self.level_bar = QProgressBar()
        self.level_bar.setRange(int(DBFS_FLOOR), 0)
        self.level_bar.setValue(int(DBFS_FLOOR))
        self.level_bar.setTextVisible(False)
        self.takes = QListWidget()

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.record_button)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.takes)
        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)

        self.record_button.clicked.connect(self.toggle_recording)
        self.takes.itemDoubleClicked.connect(self._ask_rename)
        self._capture: threading.Thread | None = None
        self._samples = None
        self._model = None
        self._last_block = None

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(100)
        self._level_timer.timeout.connect(self._refresh_level)

        self.take_named.connect(self._apply_name)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh_device)
        self._poll.start(2000)
        self.refresh_device()

    def refresh_device(self) -> None:
        device = self._find_rx()
        self.record_button.setEnabled(device is not None)
        self.status_label.setText(device.name if device else NO_DEVICE)

    def add_take(self, filename: str) -> int:
        item = QListWidgetItem(f"{filename}  ·  transcription…")
        self.takes.addItem(item)
        self._rows.append(item)
        return len(self._rows) - 1

    def update_take(self, row: int, filename: str, origin: str) -> None:
        self._rows[row].setText(f"{filename}  ·  {origin}")

    def take_text(self, row: int) -> str:
        return self._rows[row].text()

    def _apply_name(self, row: int, filename: str, origin: str) -> None:
        if row in self._manually_renamed_rows:
            # Le renommage manuel a gagné ; un résultat automatique tardif
            # (typiquement un "échec" dû à la course sur le chemin provisoire)
            # ne doit pas l'effacer.
            return
        old_path, when = self._takes_meta[row]
        self._takes_meta[row] = (old_path.parent / filename, when)
        self.update_take(row, filename, origin)

    def set_level(self, samples) -> None:
        self.level_bar.setValue(int(rms_dbfs(samples)))

    def _refresh_level(self) -> None:
        # Rien à afficher avant l'arrivée du premier bloc.
        if self._last_block is not None:
            self.set_level(self._last_block)

    def report_write_error(self, path, error) -> None:
        self.status_label.setText(
            f"Écriture impossible dans {path} : {error}"
        )

    def rename_take(self, row: int, new_text: str) -> None:
        path, when = self._takes_meta[row]
        target = rename_take_file(path, new_text, when)
        self._takes_meta[row] = (target, when)
        self._manually_renamed_rows.add(row)
        self.update_take(row, target.name, "manuel")

    def _ask_rename(self, item) -> None:
        row = self._rows.index(item)
        text, ok = QInputDialog.getText(self, "Renommer", "Nouveau nom :")
        if ok and text.strip():
            self.rename_take(row, text)

    def toggle_recording(self) -> None:
        if self._capture is None:
            self._start_capture()
        else:
            self._finish_capture()

    def _start_capture(self) -> None:
        device = self._find_rx()
        if device is None:
            self.refresh_device()
            return
        self._stop.clear()
        self._samples = None
        self._last_block = None

        def on_block(block):
            self._last_block = block

        def run():
            self._samples = record(self._pa, device, self._stop, on_block=on_block)

        self._capture = threading.Thread(target=run, daemon=True)
        self._capture.start()
        self._level_timer.start()
        self.record_button.setText("Arrêter")

    def _finish_capture(self) -> None:
        self._stop.set()
        self._capture.join()
        self._capture = None
        self._level_timer.stop()
        self.level_bar.setValue(int(DBFS_FLOOR))
        self.record_button.setText("Enregistrer")

        when = datetime.now()
        target_dir = destination_dir(when)
        provisional = target_dir / build_name(when, "sans-nom")
        try:
            write_wav(self._samples, provisional)
        except OSError as error:
            self.report_write_error(provisional, error)
            return
        row = self.add_take(provisional.name)
        self._takes_meta.append((provisional, when))

        if self._model is None:
            self._model = load_model()

        def runner(path: Path, at: datetime):
            return name_recording(path, at, self._model)

        def on_done(_src, result):
            if result is None:
                self.take_named.emit(row, provisional.name, "échec")
            else:
                self.take_named.emit(row, result.path.name, result.origin)

        if self._queue is None:
            self._queue = NamingQueue(runner)
            self._queue.start()
        self._queue.submit(provisional, when, on_done)


def main() -> int:
    import pyaudio

    app = QApplication(sys.argv)
    pa = pyaudio.PyAudio()
    window = MainWindow(pa=pa)
    window.resize(560, 420)
    window.show()
    return app.exec()
