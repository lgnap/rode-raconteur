"""Fenêtre unique. Toute la logique vit ailleurs ; ce module ne fait que l'UI."""

import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from conteur.job import name_recording
from conteur.job import rename_take as rename_take_file
from conteur.naming import ORIGIN_FAILED, ORIGIN_MANUAL, UNNAMED, origin_label
from conteur.paths import build_name, destination_dir, unique_path
from conteur.recorder import record, write_wav
from conteur.rx_device import find_rx as default_find_rx
from conteur.signal import DBFS_FLOOR, rms_dbfs
from conteur.transcribe import load_model
from conteur.worker import NamingQueue

NO_DEVICE = "Branche le Wireless PRO RX pour enregistrer."
RX_LOST = "Récepteur débranché — la prise en cours est incomplète."
CAPTURE_FAILED = "Capture impossible"
MODEL_FAILED = "Modèle de transcription indisponible"
AUDIO_UNAVAILABLE = "Sous-système audio indisponible"
MISSING_FILE = "Fichier introuvable"
RENAME_FAILED = "Renommage impossible"
POLL_MS = 2000
SHUTDOWN_TIMEOUT_S = 30.0
INCOMPLETE = "incomplet"
PENDING = "transcription…"


def describe_error(error: BaseException) -> str:
    """Rend une cause lisible, en traduisant les échecs connus les plus opaques."""
    text = str(error).strip() or error.__class__.__name__
    if "cublas" in text.lower() or "cudnn" in text.lower():
        return (
            "bibliothèques CUDA introuvables, transcription impossible "
            "(voir docs/known-issues.md)"
        )
    return f"{error.__class__.__name__} : {text}"


class MainWindow(QMainWindow):
    take_named = Signal(int, str, str)
    naming_failed = Signal(str, str)

    def __init__(self, find_rx=None, queue=None, pa=None, pa_factory=None):
        super().__init__()
        self._pa = pa
        # PortAudio fige la liste des périphériques à Pa_Initialize() et
        # PyAudio n'offre aucun rebalayage : sans fabrique pour recréer le
        # contexte, le sondage relirait indéfiniment l'instantané du démarrage.
        self._pa_factory = pa_factory
        self._find_rx = find_rx or self._find_rx_via_pa
        self._queue = queue
        self._stop = threading.Event()
        self._rows: list[QListWidgetItem] = []
        self._takes_meta: list[tuple[Path, datetime]] = []
        # Lignes renommées à la main : un résultat automatique tardif (souvent
        # un "échec" dû à la course entre le renommage manuel et la file, qui
        # tient encore le chemin provisoire d'origine) ne doit plus les toucher.
        self._manually_renamed_rows: set[int] = set()
        # Prises captées alors que le récepteur avait disparu : le fragment est
        # conservé, mais la ligne doit dire qu'il est tronqué.
        self._incomplete_rows: set[int] = set()
        self._rx_lost = False
        # Un message d'erreur ne doit pas être effacé par le prochain sondage
        # du périphérique, deux secondes plus tard.
        self._sticky_status = False
        self._shutdown_done = False

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
        self._capture_error: BaseException | None = None
        self._model = None
        self._model_error: BaseException | None = None
        self._model_loader: threading.Thread | None = None
        self._last_block = None

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(100)
        self._level_timer.timeout.connect(self._refresh_level)

        self.take_named.connect(self._apply_name)
        self.naming_failed.connect(self.report_naming_error)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh_device)
        self._poll.start(POLL_MS)
        self.refresh_device()
        # Le modèle est chargé une fois au démarrage et reste résident. Le
        # charger hors du fil d'interface évite de figer la fenêtre au premier
        # arrêt de capture, au pire moment.
        if self._pa is not None:
            self._start_model_loading()

    def _find_rx_via_pa(self):
        if self._pa is None:
            return None
        return default_find_rx(self._pa)

    def _rescan_devices(self):
        """Rend un contexte PortAudio neuf, seule façon de voir un branchement.

        Jamais appelé pendant une capture : le contexte porte le flux ouvert.
        """
        if self._pa_factory is None:
            return None
        old, self._pa = self._pa, None
        if old is not None:
            try:
                old.terminate()
            except Exception:
                pass
        try:
            self._pa = self._pa_factory()
        except Exception as error:      # noqa: BLE001 - la fenêtre doit survivre
            self._set_status(f"{AUDIO_UNAVAILABLE} : {error}", sticky=True)
            return None
        return self._find_rx()

    def _start_model_loading(self) -> None:
        if self._model_loader is not None:
            return

        def load():
            try:
                self._model = load_model()
            except BaseException as error:      # noqa: BLE001
                self._model_error = error

        self._model_loader = threading.Thread(target=load, daemon=True)
        self._model_loader.start()

    def _await_model(self):
        """Attend le chargement lancé au démarrage. Ne le déclenche pas."""
        self._start_model_loading()
        self._model_loader.join()
        return self._model

    def refresh_device(self) -> None:
        device = self._find_rx()
        if self._capture is not None:
            # Capture en cours : le bouton porte « Arrêter ». Le désactiver
            # rendrait la prise inarrêtable et l'audio déjà capté inatteignable.
            self.record_button.setEnabled(True)
            if device is None:
                self._rx_lost = True
                self._stop.set()
                self._set_status(RX_LOST)
            if self._rx_lost and not self._capture.is_alive():
                # Le fil de capture a rendu la main : le fragment est écrit
                # sans attendre un geste de l'utilisateur.
                self._finish_capture()
            return
        if device is None:
            device = self._rescan_devices()
        self.record_button.setEnabled(device is not None)
        self._set_status(device.name if device else NO_DEVICE)

    def _set_status(self, text: str, sticky: bool = False) -> None:
        """Affiche un état. Un message collant survit aux sondages suivants."""
        if self._sticky_status and not sticky:
            return
        self.status_label.setText(text)
        self._sticky_status = sticky

    def _clear_status(self) -> None:
        """Un geste de l'utilisateur lève la rémanence du dernier message."""
        self._sticky_status = False

    def add_take(self, filename: str, incomplete: bool = False) -> int:
        item = QListWidgetItem()
        self.takes.addItem(item)
        self._rows.append(item)
        row = len(self._rows) - 1
        if incomplete:
            self._incomplete_rows.add(row)
        item.setText(self._row_text(row, filename, PENDING))
        return row

    def _row_text(self, row: int, filename: str, tail: str) -> str:
        text = f"{filename}  ·  {tail}"
        if row in self._incomplete_rows:
            text += f"  ·  {INCOMPLETE}"
        return text

    def update_take(self, row: int, filename: str, origin: str) -> None:
        # L'origine circule en jeton interne ; seule sa traduction s'affiche.
        self._rows[row].setText(self._row_text(row, filename, origin_label(origin)))

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

    def report_naming_error(self, filename: str, reason: str) -> None:
        """Affiche pourquoi le nommage a échoué, pas seulement qu'il a échoué."""
        self._set_status(f"Nommage impossible pour {filename} : {reason}", sticky=True)

    def report_write_error(self, path, error) -> None:
        self._set_status(f"Écriture impossible dans {path} : {error}", sticky=True)

    def rename_take(self, row: int, new_text: str) -> None:
        path, when = self._takes_meta[row]
        if not path.exists():
            # Course connue : la file a déjà renommé le fichier, mais le
            # résultat n'a pas encore atteint l'interface, qui tient donc un
            # chemin périmé. Le dire plutôt que de lever dans le fil graphique.
            self._set_status(f"{MISSING_FILE} : {path}", sticky=True)
            return
        try:
            target = rename_take_file(path, new_text, when)
        except OSError as error:
            self._set_status(f"{RENAME_FAILED} {path} : {error}", sticky=True)
            return
        self._takes_meta[row] = (target, when)
        self._manually_renamed_rows.add(row)
        self.update_take(row, target.name, ORIGIN_MANUAL)

    def _ask_rename(self, item) -> None:
        self._clear_status()
        row = self._rows.index(item)
        text, ok = QInputDialog.getText(self, "Renommer", "Nouveau nom :")
        if ok and text.strip():
            self.rename_take(row, text)

    def toggle_recording(self) -> None:
        self._clear_status()
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
        self._capture_error = None
        self._rx_lost = False
        self._last_block = None

        def on_block(block):
            self._last_block = block

        def run():
            # Tout ce que lève la capture (typiquement `pa.open` sur un
            # récepteur parti entre la détection et l'ouverture) reste dans le
            # fil : sans cela, `_samples` resterait None et l'écriture du WAV
            # planterait la fenêtre.
            try:
                self._samples = record(self._pa, device, self._stop, on_block=on_block)
            except BaseException as error:      # noqa: BLE001 - jamais de fil mort muet
                self._capture_error = error

        self._capture = threading.Thread(target=run, daemon=True)
        self._capture.start()
        self._level_timer.start()
        self.record_button.setText("Arrêter")
        # La prise qui démarre chasse le message de la prise précédente.
        self._set_status(device.name)

    def _finish_capture(self) -> None:
        self._stop.set()
        self._capture.join()
        self._capture = None
        self._level_timer.stop()
        self.level_bar.setValue(int(DBFS_FLOOR))
        self.record_button.setText("Enregistrer")

        samples, error = self._samples, self._capture_error
        incomplete, self._rx_lost = self._rx_lost, False
        if samples is None:
            # La capture a échoué avant d'avoir rendu quoi que ce soit : rien
            # à écrire, mais l'utilisateur doit l'apprendre.
            detail = f" : {error}" if error is not None else ""
            self._set_status(f"{CAPTURE_FAILED}{detail}", sticky=True)
            return

        when = datetime.now()
        target_dir = destination_dir(when)
        # Comme tous les autres chemins écrits : deux prises arrêtées dans la
        # même seconde ne doivent pas s'écraser l'une l'autre.
        provisional = unique_path(target_dir, build_name(when, UNNAMED))
        try:
            write_wav(samples, provisional)
        except OSError as error:
            self.report_write_error(provisional, error)
            return
        row = self.add_take(provisional.name, incomplete=incomplete)
        self._takes_meta.append((provisional, when))

        model = self._await_model()
        if model is None:
            # Le fichier est écrit et la ligne est là : rien n'est perdu, mais
            # il n'y a personne pour le nommer.
            self._set_status(f"{MODEL_FAILED} : {self._model_error}", sticky=True)
            return

        def runner(path: Path, at: datetime):
            return name_recording(path, at, self._model)

        def on_done(_src, result, error=None):
            if result is None:
                self.take_named.emit(row, provisional.name, ORIGIN_FAILED)
                if error is not None:
                    # Sans ceci l'utilisateur voit "échec" sans jamais pouvoir
                    # en connaître la cause : il faut rejouer la chaîne à la main.
                    self.naming_failed.emit(provisional.name, describe_error(error))
            else:
                self.take_named.emit(row, result.path.name, result.origin)

        if self._queue is None:
            self._queue = NamingQueue(runner)
            self._queue.start()
        self._queue.submit(provisional, when, on_done)

    def shutdown(self, timeout_s: float = SHUTDOWN_TIMEOUT_S) -> None:
        """Arrêt propre : rien en attente n'est jeté sans lui laisser sa chance.

        Une capture en cours est écrite, les travaux déjà en file disposent du
        délai imparti pour aboutir, puis le contexte PortAudio est rendu.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._poll.stop()
        if self._capture is not None:
            self._finish_capture()
        self._level_timer.stop()
        queue, self._queue = self._queue, None
        if queue is not None:
            queue.stop()
            queue.join(timeout_s)
        pa, self._pa = self._pa, None
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)


def main() -> int:
    import pyaudio

    app = QApplication(sys.argv)
    window = MainWindow(pa=pyaudio.PyAudio(), pa_factory=pyaudio.PyAudio)
    window.resize(560, 420)
    window.show()
    try:
        return app.exec()
    finally:
        window.shutdown()
