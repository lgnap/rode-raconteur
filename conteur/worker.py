"""File d'attente sérialisée. Un seul travail à la fois (contention VRAM)."""

import queue
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_STOP = object()


class NamingQueue:
    """Exécute les travaux de nommage un par un dans un fil dédié."""

    def __init__(self, runner: Callable[[Path, datetime], object]):
        self._runner = runner
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, wav_path: Path, when: datetime, on_done: Callable) -> None:
        self._queue.put((wav_path, when, on_done))

    def stop(self) -> None:
        self._queue.put(_STOP)

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            wav_path, when, on_done = item
            try:
                result = self._runner(wav_path, when)
            except Exception:
                result = None          # l'échec ne tue pas la file
            try:
                on_done(wav_path, result)
            except Exception:
                pass
