"""Serialised work queue. One job at a time (VRAM contention)."""

import queue
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

_STOP = object()


class NamingQueue:
    """Runs naming jobs one by one on a dedicated thread."""

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
            error: Exception | None = None
            try:
                result = self._runner(wav_path, when)
            except Exception as exc:
                # A failure does not kill the queue, but its cause must not be
                # lost: without it the UI can only ever display "failed".
                result, error = None, exc
            try:
                on_done(wav_path, result, error)
            except Exception:
                pass
