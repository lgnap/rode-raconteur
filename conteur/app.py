"""Single window. All the logic lives elsewhere; this module is only the UI."""

import signal as stdlib_signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from conteur.card import Card
from conteur.devices import find_storage as default_find_storage
from conteur.intake import intake
from conteur.job import name_recording
from conteur.job import rename_take as rename_take_file
from conteur.ledger import Ledger
from conteur.naming import ORIGIN_FAILED, ORIGIN_MANUAL, UNNAMED, origin_label
from conteur.orphans import find_orphans, parse_timestamp
from conteur.paths import FOLDER, build_name, destination_dir, music_dir, unique_path
from conteur.recorder import record, write_wav
from conteur.rx_device import find_rx as default_find_rx
from conteur.signal import DBFS_FLOOR, rms_dbfs
from conteur.transcribe import chosen_device, load_model
from conteur.worker import NamingQueue

NO_DEVICE = "Branche le Wireless PRO RX pour enregistrer."
RX_LOST = "Récepteur débranché — la prise en cours est incomplète."
CAPTURE_FAILED = "Capture impossible"
MODEL_LOADING = "Chargement du modèle de transcription…"
MODEL_FAILED = "Modèle de transcription indisponible"
AUDIO_UNAVAILABLE = "Sous-système audio indisponible"
MISSING_FILE = "Fichier introuvable"
RENAME_FAILED = "Renommage impossible"
IMPORT_RUNNING = "Récupération en cours…"
POLL_MS = 2000
IMPORT_POLL_MS = 200
SHUTDOWN_TIMEOUT_S = 30.0
INCOMPLETE = "incomplet"
PENDING = "transcription…"


def install_interrupt_handler(app, interval_ms: int = 200):
    """Make Ctrl+C a clean shutdown, like closing the window.

    Qt runs its loop in C++; a Python signal handler only runs between two
    bytecodes of the main thread, and is therefore never reached while the loop
    is running. The idle timer periodically hands control back to the
    interpreter, which lets the handler run.
    """
    stdlib_signal.signal(stdlib_signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(interval_ms)
    return timer


def describe_error(error: BaseException) -> str:
    """Return a readable cause, translating the most opaque known failures."""
    text = str(error).strip() or error.__class__.__name__
    if "cublas" in text.lower() or "cudnn" in text.lower():
        return (
            "bibliothèques CUDA introuvables, transcription impossible "
            "(voir docs/known-issues.md)"
        )
    return f"{error.__class__.__name__} : {text}"


class ImportSnapshot:
    """What the import thread has done so far, read by the GUI thread.

    Cumulative rather than "the last event": between two polls the thread can
    finish three files, and a single-slot state would lose two of them. The
    list on screen would then be wrong, not merely late. It stays cumulative
    across cards too — several cards can be imported in one run, one after
    another, and `total`/`done` describe the whole run, not just the card
    currently being read.

    Nothing here emits a Qt signal, and nothing here touches a Qt widget: both
    a signal delivered into an already-destroyed window and a QListWidget
    mutated outside the GUI thread crash or corrupt the process. Handing a
    freshly-imported take to the naming queue means calling `add_take`, which
    is a widget mutation — so a path is only queued here (`queue_submit`) and
    handed to `MainWindow._submit_imported` later, from the GUI thread, by
    `_refresh_import`. That is exactly the pattern already used for the level
    meter, and the one that resolved this project's model-loading segfault.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.total = 0
        self.done = 0
        self.skipped = 0
        self.failures = 0
        self.current: str | None = None
        self.finished = False
        self.lines: list[str] = []
        self._pending: list = []

    def queue_submit(self, path) -> None:
        """Record a take to hand to the naming queue — never do it here."""
        with self._lock:
            self._pending.append(path)

    def mark_finished(self) -> None:
        """The run is over. Through the lock, like every other mutation here.

        The GUI thread reads this every hundred milliseconds to decide whether
        to stop polling; the import thread is what sets it. One unlocked
        assignment is all it takes to make the pattern a matter of memory
        rather than of construction.
        """
        with self._lock:
            self.finished = True

    def is_finished(self) -> bool:
        with self._lock:
            return self.finished

    def record_failure(self, line: str) -> None:
        """A whole-card failure: counted and kept, like a single take's.

        Routed through the lock like every other mutation of `.lines`, and
        counted in `.failures` so it also reaches the finished summary — the
        only place a failure surfaces on the status line.
        """
        with self._lock:
            self.failures += 1
            self.lines.append(line)

    def drain_pending(self) -> list:
        """Take ownership of the paths queued since the last drain."""
        with self._lock:
            pending, self._pending = self._pending, []
        return pending

    def absorb(self, event) -> None:
        with self._lock:
            if event.kind == "inventory":
                # += rather than =: a second card's inventory must add to the
                # first's, not erase it.
                self.total += int(event.detail or 0)
            elif event.kind == "copy":
                self.current = event.take
            elif event.kind == "recorded":
                self.done += 1
                self.lines.append(f"{event.take} → importé")
            elif event.kind == "skipped":
                self.skipped += 1
                self.lines.append(f"{event.take} → déjà importé")
            elif event.kind == "failed":
                self.failures += 1
                self.lines.append(f"{event.take} → échec : {event.detail}")
            elif event.kind == "done":
                # Marks the end of one card's intake, not of the whole run:
                # with several cards, the run loop keeps going. `finished` is
                # set once, by the thread itself, after every card is done.
                self.current = None

    def summary(self) -> str:
        with self._lock:
            if not self.finished:
                where = f" — {self.current}" if self.current else ""
                return f"{IMPORT_RUNNING} {self.done}/{self.total}{where}"
            # "0/8" reads like a failure. Finding nothing new is the ordinary
            # outcome of a second run — the card has to be re-read every time,
            # since a name is not an identity — and it deserves to be said as
            # such rather than as a count that looks like a loss.
            parts = []
            if self.done:
                parts.append(f"{self.done} importée(s)")
            elif self.skipped:
                parts.append("rien de nouveau")
            if self.skipped:
                parts.append(f"{self.skipped} déjà présente(s)")
            if self.failures:
                parts.append(f"{self.failures} échec(s)")
            return "Récupération terminée : " + (", ".join(parts) or "rien à faire")


class MainWindow(QMainWindow):
    take_named = Signal(int, str, str)
    naming_failed = Signal(str, str)

    def __init__(self, find_rx=None, queue=None, pa=None, pa_factory=None,
                 orphan_root=None, find_storage=None):
        super().__init__()
        self._pa = pa
        # PortAudio freezes the device list at Pa_Initialize() and PyAudio
        # offers no rescan: without a factory to rebuild the context, polling
        # would forever re-read the snapshot taken at startup.
        self._pa_factory = pa_factory
        self._orphan_root = orphan_root
        self._find_rx = find_rx or self._find_rx_via_pa
        self._find_storage = find_storage or default_find_storage
        self._queue = queue
        self._stop = threading.Event()
        # A separate flag from `_stop`: that one belongs to audio capture
        # (cleared by _start_capture, set by _finish_capture). Sharing it
        # would mean stopping a recording also stops an import in progress,
        # and starting a recording would clear an import's stop request.
        self._import_stop = threading.Event()
        self._cards: list = []
        self._snapshot = ImportSnapshot()
        self._import_thread: threading.Thread | None = None
        self._rows: list[QListWidgetItem] = []
        self._takes_meta: list[tuple[Path, datetime]] = []
        # Rows renamed by hand: a late automatic result (often a "failed" due
        # to the race between the manual rename and the queue, which still
        # holds the original provisional path) must no longer touch them.
        self._manually_renamed_rows: set[int] = set()
        # Takes captured while the receiver had vanished: the fragment is
        # kept, but the row must say that it is truncated.
        self._incomplete_rows: set[int] = set()
        self._rx_lost = False
        # An error message must not be wiped by the next device poll, two
        # seconds later.
        self._sticky_status = False
        self._shutdown_done = False

        self.setWindowTitle("Conteur")
        self.status_label = QLabel()
        self.record_button = QPushButton("Enregistrer")
        self.import_button = QPushButton("Récupérer")
        self.import_button.setEnabled(False)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(int(DBFS_FLOOR), 0)
        self.level_bar.setValue(int(DBFS_FLOOR))
        self.level_bar.setTextVisible(False)
        self.takes = QListWidget()

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.record_button)
        layout.addWidget(self.import_button)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.takes)
        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)

        self.record_button.clicked.connect(self.toggle_recording)
        self.import_button.clicked.connect(self.start_import)
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

        self._import_timer = QTimer(self)
        self._import_timer.timeout.connect(self._refresh_import)

        self.take_named.connect(self._apply_name)
        self.naming_failed.connect(self.report_naming_error)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh_device)
        self._poll.start(POLL_MS)
        self.refresh_device()
        # The model is loaded once at startup and stays resident. Loading it
        # off the UI thread avoids freezing the window at the first stop of a
        # capture, at the worst possible moment.
        if self._pa is not None:
            self._start_model_loading()

    def _find_rx_via_pa(self):
        if self._pa is None:
            return None
        return default_find_rx(self._pa)

    def _rescan_devices(self):
        """Return a fresh PortAudio context, the only way to see a new plug.

        Never called during a capture: the context owns the open stream.
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
        except Exception as error:      # noqa: BLE001 - the window must survive
            self._set_status(f"{AUDIO_UNAVAILABLE} : {error}", sticky=True)
            return None
        return self._find_rx()

    def _ensure_queue(self, runner) -> None:
        if self._queue is None:
            self._queue = NamingQueue(runner)
            self._queue.start()

    def _naming_runner(self, path: Path, at: datetime):
        return name_recording(path, at, self._await_model())

    def recover_orphans(self, root=None) -> int:
        """Re-queue takes left unnamed by an earlier session.

        Without this recovery, an interrupted naming — model unavailable,
        application killed — would leave the file `sans-nom` forever.
        Returns the number of takes picked up.
        """
        root = Path(root if root is not None else self._orphan_root or "")
        taken = 0
        for path, when in find_orphans(root):
            row = self.add_take(path.name)
            self._takes_meta.append((path, when))

            def on_done(_src, result, error=None, row=row, path=path):
                if result is None:
                    self.take_named.emit(row, path.name, ORIGIN_FAILED)
                    if error is not None:
                        self.naming_failed.emit(path.name, describe_error(error))
                else:
                    self.take_named.emit(row, result.path.name, result.origin)

            self._ensure_queue(self._naming_runner)
            self._queue.submit(path, when, on_done)
            taken += 1
        return taken

    def start_import(self) -> None:
        """Drain the intake generator on a thread. Five lines, on purpose."""
        if self._import_thread is not None or not self._cards:
            return
        self._clear_status()
        self._snapshot = ImportSnapshot()
        self._import_stop.clear()
        cards = list(self._cards)
        stop = self._import_stop
        snapshot = self._snapshot

        def run():
            try:
                for device in cards:              # one card at a time: reading
                    try:                          # two at once is slower
                        with device.block.open("rb") as fh:
                            card = Card(fh)
                            ledger = Ledger(card.serial)
                            # queue_submit only records the path; it must not
                            # call into Qt, since this closure runs on the
                            # import thread. _refresh_import hands each one to
                            # _submit_imported from the GUI thread instead.
                            for event in intake(card, ledger, destination_dir,
                                                snapshot.queue_submit, stop=stop):
                                snapshot.absorb(event)
                    except BaseException as error:      # noqa: BLE001 - never a silent dead thread
                        # A card that vanishes mid-import, one whose boot
                        # sector no longer parses as FAT32, or any other
                        # failure inside intake() (a malformed provisional
                        # file breaking _started()'s struct.unpack, say) is
                        # reported, and the next card is still attempted.
                        snapshot.record_failure(f"{device.block} : {error}")
            finally:
                # Whatever happens above, the GUI thread must be told to stop
                # polling and re-enable the button. Without this, an
                # unanticipated failure leaves the status stuck on
                # "Récupération en cours…" forever, with nobody watching.
                snapshot.mark_finished()

        self._import_thread = threading.Thread(target=run, daemon=True)
        self._import_thread.start()
        self.import_button.setEnabled(False)
        self._import_timer.start(IMPORT_POLL_MS)

    def _submit_imported(self, path: Path) -> None:
        when = parse_timestamp(path.name) or datetime.now()
        row = self.add_take(path.name)
        self._takes_meta.append((path, when))

        def on_done(_src, result, error=None, row=row, path=path):
            if result is None:
                self.take_named.emit(row, path.name, ORIGIN_FAILED)
                if error is not None:
                    self.naming_failed.emit(path.name, describe_error(error))
            else:
                self.take_named.emit(row, result.path.name, result.origin)

        self._ensure_queue(self._naming_runner)
        self._queue.submit(path, when, on_done)

    def _refresh_import(self) -> None:
        # Handing a take to the naming queue means mutating the take list —
        # a Qt widget — so it happens here, on the GUI thread, never inside
        # the import thread that merely queued the path.
        for path in self._snapshot.drain_pending():
            self._submit_imported(path)
        if not self._snapshot.is_finished():
            self._set_status(self._snapshot.summary())
            return
        self._import_timer.stop()
        self._import_thread = None
        # refresh_device() first, then the outcome — and stickily. It rewrites
        # the status every two seconds, so the result of an import that took
        # minutes used to vanish before anyone read it.
        self.refresh_device()
        self._set_status(self._snapshot.summary(), sticky=True)

    def _start_model_loading(self) -> None:
        if self._model_loader is not None:
            return

        def load():
            try:
                self._model = load_model()
            except BaseException as error:      # noqa: BLE001 - never a silent dead thread
                self._model_error = error

        self._model_loader = threading.Thread(target=load, daemon=True)
        self._model_loader.start()

    def _await_model(self):
        """Wait for the load started at startup. Does not trigger it."""
        self._start_model_loading()
        self._model_loader.join()
        return self._model

    def refresh_device(self) -> None:
        device = self._find_rx()
        if self._capture is not None:
            # Capture in progress: the button reads "Arrêter". Disabling it
            # would make the take unstoppable and the audio already captured
            # unreachable.
            self.record_button.setEnabled(True)
            if device is None:
                self._rx_lost = True
                self._stop.set()
                self._set_status(RX_LOST)
            if self._rx_lost and not self._capture.is_alive():
                # The capture thread has returned: the fragment is written
                # without waiting for the user to act.
                self._finish_capture()
            return
        if device is None:
            device = self._rescan_devices()
        self.record_button.setEnabled(device is not None)
        # What is plugged in decides the mode: a transmitter never exposes
        # audio, a receiver never exposes storage. Nothing to select.
        try:
            self._cards = self._find_storage()
        except OSError:
            self._cards = []
        self.import_button.setEnabled(
            bool(self._cards) and self._import_thread is None)
        self._set_status((device.name if device else NO_DEVICE) + self._model_suffix())

    def _model_suffix(self) -> str:
        """Say where the model stands, without hiding the device state.

        Computed here rather than emitted from the loading thread: a signal
        crossing the thread boundary into an already-destroyed window crashes
        the process.
        """
        if self._model_error is not None:
            return f" — {MODEL_FAILED}"
        if self._model_loader is not None and self._model is None:
            return f" — {MODEL_LOADING}"
        return ""

    def _set_status(self, text: str, sticky: bool = False) -> None:
        """Display a state. A sticky message survives the following polls."""
        if self._sticky_status and not sticky:
            return
        self.status_label.setText(text)
        self._sticky_status = sticky

    def _clear_status(self) -> None:
        """A user action clears the stickiness of the last message."""
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
        # The origin travels as an internal token; only its label is shown.
        self._rows[row].setText(self._row_text(row, filename, origin_label(origin)))

    def take_text(self, row: int) -> str:
        return self._rows[row].text()

    def _apply_name(self, row: int, filename: str, origin: str) -> None:
        if row in self._manually_renamed_rows:
            # The manual rename won; a late automatic result (typically a
            # "failed" caused by the race on the provisional path) must not
            # wipe it.
            return
        old_path, when = self._takes_meta[row]
        self._takes_meta[row] = (old_path.parent / filename, when)
        self.update_take(row, filename, origin)

    def set_level(self, samples) -> None:
        self.level_bar.setValue(int(rms_dbfs(samples)))

    def _refresh_level(self) -> None:
        # Nothing to display before the first block arrives.
        if self._last_block is not None:
            self.set_level(self._last_block)

    def report_naming_error(self, filename: str, reason: str) -> None:
        """Say why naming failed, not merely that it failed."""
        self._set_status(f"Nommage impossible pour {filename} : {reason}", sticky=True)

    def report_write_error(self, path, error) -> None:
        self._set_status(f"Écriture impossible dans {path} : {error}", sticky=True)

    def rename_take(self, row: int, new_text: str) -> None:
        path, when = self._takes_meta[row]
        if not path.exists():
            # Known race: the queue has already renamed the file, but the
            # result has not reached the UI yet, which therefore holds a stale
            # path. Say so rather than raise on the GUI thread.
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
            # Everything the capture raises (typically `pa.open` on a
            # receiver gone between detection and opening) stays inside the
            # thread: without this, `_samples` would stay None and writing the
            # WAV would crash the window.
            try:
                self._samples = record(self._pa, device, self._stop, on_block=on_block)
            except BaseException as error:      # noqa: BLE001 - never a silent dead thread
                self._capture_error = error

        self._capture = threading.Thread(target=run, daemon=True)
        self._capture.start()
        self._level_timer.start()
        self.record_button.setText("Arrêter")
        # The take that starts clears the message from the previous one.
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
            # The capture failed before returning anything: nothing to write,
            # but the user must be told.
            detail = f" : {error}" if error is not None else ""
            self._set_status(f"{CAPTURE_FAILED}{detail}", sticky=True)
            return

        when = datetime.now()
        target_dir = destination_dir(when)
        # Like every other path written: two takes stopped within the same
        # second must not overwrite each other.
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
            # The file is written and the row is there: nothing is lost, but
            # there is nobody to name it.
            self._set_status(f"{MODEL_FAILED} : {self._model_error}", sticky=True)
            return

        def runner(path: Path, at: datetime):
            return name_recording(path, at, self._model)

        def on_done(_src, result, error=None):
            if result is None:
                self.take_named.emit(row, provisional.name, ORIGIN_FAILED)
                if error is not None:
                    # Without this the user sees "failed" and can never learn
                    # why: the whole chain has to be replayed by hand.
                    self.naming_failed.emit(provisional.name, describe_error(error))
            else:
                self.take_named.emit(row, result.path.name, result.origin)

        self._ensure_queue(runner)
        self._queue.submit(provisional, when, on_done)

    def shutdown(self, timeout_s: float = SHUTDOWN_TIMEOUT_S) -> None:
        """Clean shutdown: nothing pending is thrown away without its chance.

        A capture in progress is written, jobs already queued get the allotted
        time to finish, then the PortAudio context is released. An import in
        progress is asked to stop between takes — the source is read-only, so
        there is nothing to flush — and given the same grace period as the
        naming queue to actually do so.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._poll.stop()
        if self._capture is not None:
            self._finish_capture()
        self._stop.set()
        self._level_timer.stop()
        self._import_timer.stop()
        self._import_stop.set()
        thread, self._import_thread = self._import_thread, None
        if thread is not None:
            thread.join(timeout_s)
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
    window = MainWindow(
        pa=pyaudio.PyAudio(),
        pa_factory=pyaudio.PyAudio,
        orphan_root=music_dir() / FOLDER,
    )
    window.resize(560, 420)
    window.show()
    interrupt_timer = install_interrupt_handler(app)  # noqa: F841 - keeps the reference
    window.recover_orphans()
    try:
        return app.exec()
    finally:
        window.shutdown()
