"""Fenêtre unique. Toute la logique vit ailleurs ; ce module ne fait que l'UI."""

import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPushButton, QVBoxLayout, QWidget,
)

from conteur.job import name_recording
from conteur.paths import build_name, destination_dir
from conteur.recorder import record, write_wav
from conteur.rx_device import find_rx as default_find_rx
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

        self.setWindowTitle("Conteur")
        self.status_label = QLabel()
        self.record_button = QPushButton("Enregistrer")
        self.takes = QListWidget()

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.record_button)
        layout.addWidget(self.takes)
        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)

        self.record_button.clicked.connect(self.toggle_recording)
        self._capture: threading.Thread | None = None
        self._samples = None
        self._model = None

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
        self.update_take(row, filename, origin)

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

        def run():
            self._samples = record(self._pa, device, self._stop)

        self._capture = threading.Thread(target=run, daemon=True)
        self._capture.start()
        self.record_button.setText("Arrêter")

    def _finish_capture(self) -> None:
        self._stop.set()
        self._capture.join()
        self._capture = None
        self.record_button.setText("Enregistrer")

        when = datetime.now()
        target_dir = destination_dir(when)
        provisional = target_dir / build_name(when, "sans-nom")
        write_wav(self._samples, provisional)
        row = self.add_take(provisional.name)

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
