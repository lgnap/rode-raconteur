"""Fenêtre unique. Toute la logique vit ailleurs ; ce module ne fait que l'UI."""

import sys
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPushButton, QVBoxLayout, QWidget,
)

from conteur.rx_device import find_rx as default_find_rx

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


def main() -> int:
    import pyaudio

    app = QApplication(sys.argv)
    pa = pyaudio.PyAudio()
    window = MainWindow(pa=pa)
    window.resize(560, 420)
    window.show()
    return app.exec()
