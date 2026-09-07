import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import conteur.app as app_mod
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
    # The internal token stays "title"; the UI itself is in French.
    assert "titre généré" in win.take_text(row)
    assert "title" not in win.take_text(row)


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
            # We record whether the file exists AT SUBMIT TIME: what is tested is
            # the ordering, not just the final result.
            self.submitted.append((path, path.exists()))

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = FakeQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    win.toggle_recording()   # start
    win.toggle_recording()   # stop

    assert len(queue.submitted) == 1
    path, existed_at_submit = queue.submitted[0]
    assert existed_at_submit is True
    assert path.parent == tmp_path
    assert "sans-nom" in path.name


def test_level_bar_starts_at_floor(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None)
    assert win.level_bar.value() == win.level_bar.minimum()


def test_set_level_is_directly_callable_and_moves_the_bar(qapp):
    # The meter must be testable without going through the QTimer: the event
    # loop does not run in the tests (offscreen, no app.exec()), so the real
    # periodic firing of the QTimer is not covered here.
    import numpy as np

    win = MainWindow(find_rx=lambda: None, queue=None)
    win.set_level(np.full(1000, 32767, dtype=np.int16))
    assert win.level_bar.value() == pytest.approx(0, abs=1)

    win.set_level(np.zeros(1000, dtype=np.int16))
    assert win.level_bar.value() == win.level_bar.minimum()


def test_level_timer_runs_only_during_capture(qapp, tmp_path, monkeypatch):
    import threading

    import numpy as np

    import conteur.app as app_mod

    class Dev:
        index = 0
        name = "Wireless PRO RX"

    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            pass

    release = threading.Event()

    def fake_record(pa, device, stop, on_block=None):
        # Block until the test sets `stop`, so the timer can be observed while
        # the capture is genuinely running.
        release.wait(1.0)
        return np.array([1, 2, 3], dtype=np.int16)

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", fake_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    win = MainWindow(find_rx=lambda: Dev(), queue=FakeQueue(), pa=object())

    assert win._level_timer.isActive() is False
    win.toggle_recording()   # start
    assert win._level_timer.isActive() is True
    release.set()
    win.toggle_recording()   # stop
    assert win._level_timer.isActive() is False


def test_write_failure_is_reported_with_path_and_cause(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None)
    win.report_write_error("/does/not/exist/a.wav", PermissionError("refusé"))
    text = win.status_label.text()
    assert "/does/not/exist/a.wav" in text
    assert "refusé" in text


def test_write_failure_during_finish_capture_is_reported_and_not_enqueued(
    qapp, tmp_path, monkeypatch,
):
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
            self.submitted.append(path)

    def failing_write_wav(samples, path):
        raise OSError("disque plein")

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "write_wav", failing_write_wav)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = FakeQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    win.toggle_recording()   # start
    win.toggle_recording()   # stop: the write fails

    assert queue.submitted == []
    assert win._rows == []
    text = win.status_label.text()
    assert "disque plein" in text
    assert "sans-nom" in text


def test_rename_take_renames_the_file_and_updates_the_row(qapp, tmp_path):
    from datetime import datetime

    when = datetime(2026, 9, 6, 14, 32, 8)
    path = tmp_path / "2026-09-06_143208_sans-nom.wav"
    path.touch()

    win = MainWindow(find_rx=lambda: None, queue=None)
    row = win.add_take(path.name)
    win._takes_meta.append((path, when))

    win.rename_take(row, "Le Loup Gris !")

    new_path = tmp_path / "2026-09-06_143208_le-loup-gris.wav"
    assert new_path.exists()
    assert not path.exists()
    assert win._takes_meta[row] == (new_path, when)
    assert "le-loup-gris" in win.take_text(row)
    assert "manuel" in win.take_text(row)


def test_manual_rename_survives_a_late_automatic_result(qapp, tmp_path):
    # Real race: the user renames the take by hand before the automatic naming
    # queue has finished its work on that same row. The queue still holds the
    # original provisional path; since it no longer exists after the manual
    # rename, `name_recording` fails and the result that arrives afterwards is
    # a late "failed". It must not overwrite the name given by hand.
    #
    from datetime import datetime

    when = datetime(2026, 9, 6, 14, 32, 8)
    path = tmp_path / "2026-09-06_143208_sans-nom.wav"
    path.touch()

    win = MainWindow(find_rx=lambda: None, queue=None)
    row = win.add_take(path.name)
    win._takes_meta.append((path, when))

    win.rename_take(row, "Le Loup Gris !")
    assert "le-loup-gris" in win.take_text(row)
    assert "manuel" in win.take_text(row)

    # Late automatic result for the same row, on the provisional name.
    win.take_named.emit(row, path.name, "échec")

    assert "le-loup-gris" in win.take_text(row)
    assert "manuel" in win.take_text(row)
    assert win._takes_meta[row][0].name == "2026-09-06_143208_le-loup-gris.wav"


def test_successful_naming_updates_takes_meta_with_the_final_path(
    qapp, tmp_path, monkeypatch,
):
    import numpy as np

    import conteur.app as app_mod
    from conteur.job import NameResult

    class Dev:
        index = 0
        name = "Wireless PRO RX"

    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            target = path.parent / "2026-09-06_143208_le-loup.wav"
            on_done(path, NameResult(path=target, slug="le-loup", origin="title"))

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    win = MainWindow(find_rx=lambda: Dev(), queue=FakeQueue(), pa=object())
    win.toggle_recording()   # start
    win.toggle_recording()   # stop: the queue (synchronous here) names at once

    row = 0
    final_path, when = win._takes_meta[row]
    assert final_path.name == "2026-09-06_143208_le-loup.wav"
    assert "le-loup" in win.take_text(row)
    assert "titre généré" in win.take_text(row)


def test_rename_take_with_an_empty_slug_leaves_the_file_alone(qapp, tmp_path):
    from datetime import datetime

    when = datetime(2026, 9, 6, 14, 32, 8)
    path = tmp_path / "2026-09-06_143208_garde.wav"
    path.touch()

    win = MainWindow(find_rx=lambda: None, queue=None)
    row = win.add_take(path.name)
    win._takes_meta.append((path, when))

    win.rename_take(row, "!!!")

    assert path.exists()
    assert win._takes_meta[row] == (path, when)
    assert win.take_text(row) == f"{path.name}  ·  manuel"


class Dev:
    index = 0
    name = "Wireless PRO RX"


class RecordingQueue:
    """Stubbed queue that records what is submitted to it."""

    def __init__(self):
        self.submitted = []
        self.stopped = False
        self.joined = None

    def start(self):
        pass

    def submit(self, path, when, on_done):
        self.submitted.append((path, when, on_done))

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = timeout


def test_capture_thread_failure_is_reported_and_does_not_crash_the_window(
    qapp, tmp_path, monkeypatch,
):
    # The receiver goes away between detection and `pa.open()`: `record` raises
    # on the capture thread. Without a guard, `_samples` stays None and
    # `write_wav(None)` raises an AttributeError inside a Qt slot.
    import conteur.app as app_mod

    def exploding_record(pa, device, stop, on_block=None):
        raise OSError("device disconnected")

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", exploding_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    win.toggle_recording()   # start
    win.toggle_recording()   # stop: the thread raised

    assert queue.submitted == []
    assert win._rows == []
    assert win._capture is None
    assert win.record_button.text() == "Enregistrer"
    text = win.status_label.text()
    assert "device disconnected" in text


def test_stop_control_stays_enabled_when_the_receiver_vanishes_mid_capture(
    qapp, tmp_path, monkeypatch,
):
    import threading

    import numpy as np

    import conteur.app as app_mod

    release = threading.Event()

    def blocking_record(pa, device, stop, on_block=None):
        release.wait(2.0)
        return np.array([1, 2, 3], dtype=np.int16)

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", blocking_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    found = [Dev()]
    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: found[0], queue=queue, pa=object())

    win.toggle_recording()   # start
    found[0] = None          # the RX vanishes mid-take

    win.refresh_device()

    # The button reads "Arrêter": disabling it would make the take unstoppable
    # and the audio already captured permanently unreachable.
    assert win.record_button.isEnabled() is True
    assert win.record_button.text() == "Arrêter"
    assert app_mod.RX_LOST in win.status_label.text()

    release.set()
    win._capture.join(2.0)
    win.refresh_device()     # the thread returned: the fragment is written

    assert len(win._rows) == 1
    assert app_mod.INCOMPLETE in win.take_text(0)
    assert len(queue.submitted) == 1
    assert queue.submitted[0][0].exists()


def test_disconnect_is_detected_by_the_real_poll_timer(qapp, tmp_path, monkeypatch):
    # The real path goes through the 2 s QTimer: we shorten it and run a real
    # event loop rather than calling the function directly.
    import numpy as np
    from PySide6.QtTest import QTest

    import conteur.app as app_mod

    def blocking_record(pa, device, stop, on_block=None):
        stop.wait(2.0)
        return np.array([1, 2, 3], dtype=np.int16)

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", blocking_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    found = [Dev()]
    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: found[0], queue=queue, pa=object())
    win._poll.setInterval(20)

    win.toggle_recording()
    found[0] = None
    QTest.qWait(400)

    assert win._capture is None
    assert len(win._rows) == 1
    assert app_mod.INCOMPLETE in win.take_text(0)
    assert queue.submitted[0][0].exists()


def test_a_late_automatic_name_does_not_erase_the_incomplete_marker(qapp, tmp_path):
    import conteur.app as app_mod

    win = MainWindow(find_rx=lambda: None, queue=None)
    row = win.add_take("2026-09-06_143208_sans-nom.wav", incomplete=True)
    win._takes_meta.append((tmp_path / "2026-09-06_143208_sans-nom.wav", None))
    assert app_mod.INCOMPLETE in win.take_text(row)

    win.update_take(row, "2026-09-06_143208_le-loup.wav", "title")

    assert app_mod.INCOMPLETE in win.take_text(row)


def test_write_error_survives_the_device_poll(qapp):
    # Polling every two seconds used to rewrite the device name over the error
    # message: it vanished before it could be read.
    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    win.report_write_error("/plein/a.wav", OSError("disque plein"))

    win.refresh_device()
    win.refresh_device()

    assert "disque plein" in win.status_label.text()


def test_a_new_recording_clears_a_stale_error(qapp, tmp_path, monkeypatch):
    import numpy as np

    import conteur.app as app_mod

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    win = MainWindow(find_rx=lambda: Dev(), queue=RecordingQueue(), pa=object())
    win.report_write_error("/plein/a.wav", OSError("disque plein"))

    win.toggle_recording()   # geste de l'utilisateur : l'erreur n'a plus cours
    win.refresh_device()

    assert "disque plein" not in win.status_label.text()
    win.toggle_recording()


class FakePa:
    """Stubbed PortAudio context: its device list is frozen."""

    def __init__(self, names=()):
        self.devices = [{"name": n, "maxInputChannels": 2} for n in names]
        self.terminated = False

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, i):
        return self.devices[i]

    def terminate(self):
        self.terminated = True


def test_rename_on_a_stale_path_is_reported_instead_of_crashing(qapp, tmp_path):
    # The queue has already renamed the file, but `take_named` has not been
    # delivered yet: the row holds a stale provisional path. A double-click
    # levait alors FileNotFoundError dans le fil graphique.
    from datetime import datetime

    import conteur.app as app_mod

    when = datetime(2026, 9, 6, 14, 32, 8)
    stale = tmp_path / "2026-09-06_143208_sans-nom.wav"   # never created

    win = MainWindow(find_rx=lambda: None, queue=None)
    row = win.add_take(stale.name)
    win._takes_meta.append((stale, when))

    win.rename_take(row, "Le Loup Gris !")   # ne doit pas lever

    assert app_mod.MISSING_FILE in win.status_label.text()
    assert str(stale) in win.status_label.text()
    assert win._takes_meta[row] == (stale, when)
    assert row not in win._manually_renamed_rows
    assert "manuel" not in win.take_text(row)


def test_hot_plug_is_seen_only_after_the_audio_context_is_recreated(
    qapp, monkeypatch,
):
    # PortAudio enumerates devices at Pa_Initialize() and PyAudio offers no
    # rescan: without rebuilding the context, polling forever re-reads the
    # startup snapshot and the new plug goes unnoticed.
    import conteur.app as app_mod

    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    contexts = [FakePa(["HDA Intel PCH"]),                      # toujours rien
                FakePa(["HDA Intel PCH", "Wireless PRO RX"])]   # RX plugged in
    made = []

    def factory():
        pa = contexts.pop(0)
        made.append(pa)
        return pa

    first = FakePa(["HDA Intel PCH"])
    win = MainWindow(pa=first, pa_factory=factory)

    # The startup context could not see the RX; it has been released.
    assert first.terminated is True
    assert win.record_button.isEnabled() is False

    win.refresh_device()

    assert win.record_button.isEnabled() is True
    assert "Wireless PRO RX" in win.status_label.text()
    assert made[0].terminated is True          # no context is left open
    assert win._pa is made[1]
    assert win._pa.terminated is False


def test_the_audio_context_is_never_recreated_during_a_capture(
    qapp, tmp_path, monkeypatch,
):
    import threading

    import numpy as np

    import conteur.app as app_mod

    release = threading.Event()

    def blocking_record(pa, device, stop, on_block=None):
        release.wait(2.0)
        return np.array([1, 2, 3], dtype=np.int16)

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", blocking_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    calls = []

    def factory():
        calls.append(1)
        return FakePa([])

    found = [Dev()]
    win = MainWindow(find_rx=lambda: found[0], queue=RecordingQueue(),
                     pa=FakePa([]), pa_factory=factory)
    win.toggle_recording()
    found[0] = None

    win.refresh_device()

    # Rebuilding the context during a capture would take the open stream away.
    assert calls == []
    release.set()
    win._capture.join(2.0)
    win.refresh_device()


def test_the_model_is_loaded_at_startup_and_off_the_gui_thread(qapp, monkeypatch):
    import threading

    import conteur.app as app_mod

    loaded_in = []
    model = object()

    def slow_load():
        loaded_in.append(threading.current_thread())
        return model

    monkeypatch.setattr(app_mod, "load_model", slow_load)

    win = MainWindow(find_rx=lambda: Dev(), queue=RecordingQueue(), pa=object())
    win._model_loader.join(5.0)

    assert win._model is model                      # loaded without any capture
    assert loaded_in and loaded_in[0] is not threading.main_thread()


def test_finish_capture_waits_for_the_model_but_never_loads_it_itself(
    qapp, tmp_path, monkeypatch,
):
    import threading

    import numpy as np

    import conteur.app as app_mod

    loaded_in = []

    def slow_load():
        loaded_in.append(threading.current_thread())
        return object()

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "load_model", slow_load)

    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    win.toggle_recording()
    win.toggle_recording()

    assert len(loaded_in) == 1
    assert loaded_in[0] is not threading.main_thread()
    assert len(queue.submitted) == 1


def test_two_takes_stopped_in_the_same_second_do_not_overwrite_each_other(
    qapp, tmp_path, monkeypatch,
):
    import numpy as np

    import conteur.app as app_mod
    from conteur.naming import UNNAMED

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "build_name",
                        lambda when, slug: f"2026-09-06_143208_{slug}.wav")
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    for _ in range(2):
        win.toggle_recording()
        win.toggle_recording()

    first, second = queue.submitted[0][0], queue.submitted[1][0]
    assert first != second
    assert first.exists() and second.exists()
    assert UNNAMED in first.name
    assert second.name == f"2026-09-06_143208_{UNNAMED}-2.wav"


def test_closing_the_window_drains_the_queue_and_releases_portaudio(
    qapp, monkeypatch,
):
    import conteur.app as app_mod

    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    pa = FakePa(["Wireless PRO RX"])
    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=pa)

    win.close()

    # The queue gets its sentinel after the jobs already waiting: those get the
    # allotted time to finish rather than being silently thrown away.
    assert queue.stopped is True
    assert queue.joined == app_mod.SHUTDOWN_TIMEOUT_S
    assert pa.terminated is True
    assert win._poll.isActive() is False


def test_closing_during_a_capture_still_writes_the_take(qapp, tmp_path, monkeypatch):
    import numpy as np

    import conteur.app as app_mod

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
    )
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=FakePa([]))

    win.toggle_recording()
    win.close()

    assert len(queue.submitted) == 1
    assert queue.submitted[0][0].exists()
    assert queue.stopped is True


def test_the_level_meter_survives_a_capture_with_no_block_yet(qapp):
    from PySide6.QtTest import QTest

    win = MainWindow(find_rx=lambda: None, queue=None)
    win._last_block = None

    win._refresh_level()                 # no block: nothing to measure

    assert win.level_bar.value() == win.level_bar.minimum()

    # And through the real timer, which runs from the start of the capture,
    # before the first block has arrived.
    win._level_timer.setInterval(5)
    win._level_timer.start()
    QTest.qWait(60)
    win._level_timer.stop()
    assert win.level_bar.value() == win.level_bar.minimum()


def test_take_named_crosses_the_thread_boundary(qapp, tmp_path):
    # The naming queue lives on another thread: the result only reaches the row
    # through the signal, on a queued connection.
    import threading
    from datetime import datetime

    from PySide6.QtTest import QTest

    win = MainWindow(find_rx=lambda: None, queue=None)
    row = win.add_take("2026-09-06_143208_sans-nom.wav")
    win._takes_meta.append((tmp_path / "2026-09-06_143208_sans-nom.wav",
                            datetime(2026, 9, 6, 14, 32, 8)))

    done = threading.Event()

    def emit():
        win.take_named.emit(row, "2026-09-06_143208_le-loup.wav", "title")
        done.set()

    threading.Thread(target=emit, daemon=True).start()
    assert done.wait(2.0)
    QTest.qWait(50)

    assert "le-loup" in win.take_text(row)
    assert "titre généré" in win.take_text(row)
    assert win._takes_meta[row][0].name == "2026-09-06_143208_le-loup.wav"


# --- surfacing the cause of a naming failure ---


def test_describe_error_translates_the_cublas_failure():
    from conteur.app import describe_error

    reason = describe_error(RuntimeError("Library libcublas.so.12 is not found"))
    assert "CUDA" in reason
    assert "libcublas" not in reason          # the user should not decode that


def test_describe_error_keeps_an_unknown_cause_intact():
    from conteur.app import describe_error

    assert describe_error(ValueError("disque plein")) == "ValueError : disque plein"


def test_naming_failure_reason_is_shown_and_survives_the_device_poll(qapp):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    win.report_naming_error("2026-09-06_162855_sans-nom.wav", "bibliothèques CUDA introuvables")
    assert "2026-09-06_162855_sans-nom.wav" in win.status_label.text()
    assert "CUDA" in win.status_label.text()

    # The device poll must not wipe the cause.
    win.refresh_device()
    assert "CUDA" in win.status_label.text()


# --- Ctrl+C et retour visuel du chargement ---


def test_ctrl_c_asks_the_application_to_quit(qapp):
    """Qt runs in C++: without a timer handing control back to the interpreter,
    the Python handler never runs and Ctrl+C has no effect."""
    import signal as sig

    from conteur.app import install_interrupt_handler

    class FakeApp:
        def __init__(self):
            self.quit_called = False

        def quit(self):
            self.quit_called = True

    fake = FakeApp()
    previous = sig.getsignal(sig.SIGINT)
    try:
        timer = install_interrupt_handler(fake, interval_ms=50)
        assert timer.isActive() is True
        sig.raise_signal(sig.SIGINT)
        assert fake.quit_called is True
    finally:
        sig.signal(sig.SIGINT, previous)


def test_status_says_the_model_is_still_loading_without_hiding_the_device(qapp):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    win._model_loader = object()      # chargement en cours
    win._model = None
    win.refresh_device()
    assert "Wireless PRO RX" in win.status_label.text()
    assert "Chargement" in win.status_label.text()


def test_status_reports_a_model_that_failed_to_load(qapp):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    win._model_error = RuntimeError("libcublas absent")
    win.refresh_device()
    assert "Wireless PRO RX" in win.status_label.text()
    assert "indisponible" in win.status_label.text()


def test_status_is_clean_once_the_model_is_ready(qapp):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    win = MainWindow(find_rx=lambda: Dev(), queue=None)
    win._model_loader = object()
    win._model = object()
    win.refresh_device()
    assert win.status_label.text() == "Wireless PRO RX"


# --- rattrapage des prises orphelines ---


def _orphan(directory, stamp="2026-09-06_162542"):
    import numpy as np

    from conteur.recorder import write_wav

    path = directory / f"{stamp}_sans-nom.wav"
    write_wav(np.array([1, 2, 3], dtype=np.int16), path)
    return path


def test_orphans_are_queued_at_startup(qapp, tmp_path):
    class Dev:
        index = 0
        name = "Wireless PRO RX"

    class FakeQueue:
        def __init__(self):
            self.submitted = []

        def start(self):
            pass

        def submit(self, path, when, on_done):
            self.submitted.append((path, when))

    old = _orphan(tmp_path, "2026-08-31_235959")
    recent = _orphan(tmp_path, "2026-09-06_162542")
    queue = FakeQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, orphan_root=tmp_path)

    assert win.recover_orphans() == 2
    # Chronologique : la prise la plus ancienne est reprise en premier.
    assert [p for p, _ in queue.submitted] == [old, recent]
    assert win.take_text(0).startswith(old.name)


def test_already_named_takes_are_left_alone(qapp, tmp_path):
    import numpy as np

    from conteur.recorder import write_wav

    write_wav(np.array([1], dtype=np.int16), tmp_path / "2026-09-06_162542_le-loup.wav")
    win = MainWindow(find_rx=lambda: None, queue=None, orphan_root=tmp_path)
    assert win.recover_orphans() == 0


def test_a_recovered_orphan_can_be_renamed_by_hand(qapp, tmp_path):
    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            pass

    path = _orphan(tmp_path)
    win = MainWindow(find_rx=lambda: None, queue=FakeQueue(), orphan_root=tmp_path)
    win.recover_orphans()
    # _takes_meta must be populated, or the manual rename would raise.
    win.rename_take(0, "Le loup gris")
    assert (tmp_path / "2026-09-06_162542_le-loup-gris.wav").exists()
    assert not path.exists()


def test_a_card_offers_recovery_even_without_a_receiver(qapp):
    """What is plugged in decides the mode: a transmitter never exposes audio,
    a receiver never exposes storage, so nothing has to be selected."""
    from conteur.devices import StorageDevice
    from pathlib import Path

    win = MainWindow(find_rx=lambda: None, queue=None,
                     find_storage=lambda: [StorageDevice("800A-F63E",
                                                         Path("/dev/sdb"),
                                                         Path("/dev/hidraw6"),
                                                         True)])
    win.refresh_device()
    assert win.import_button.isEnabled()


def test_no_card_disables_the_import_button(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win.refresh_device()
    assert not win.import_button.isEnabled()


def test_the_snapshot_is_cumulative_not_the_last_event(qapp):
    """Between two polls the import thread can finish three files. A slot
    holding only the last event would lose two, and the list would be wrong —
    not late, wrong.
    """
    from conteur.intake import Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    for event in [Event("inventory", detail="3"),
                  Event("recorded", take="a.wav"),
                  Event("recorded", take="b.wav"),
                  Event("recorded", take="c.wav")]:
        win._snapshot.absorb(event)
    assert win._snapshot.done == 3
    assert win._snapshot.total == 3


def test_a_failure_is_counted_and_kept(qapp):
    from conteur.intake import Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.absorb(Event("failed", take="a.wav", detail="mismatch"))
    assert win._snapshot.failures == 1
    assert "a.wav" in win._snapshot.lines[-1]


# --- start_import(): the riskiest part of this file, exercised for real
# (a real threading.Thread, fakes throughout, no hardware, no real QTimer --
# _refresh_import is called directly, exactly like _refresh_level already is).

def _wav_bytes(value=1):
    """A minimal, real, mono WAV -- enough for bwf.chunks/split to parse it
    without raising, unlike arbitrary bytes."""
    import io
    import wave

    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.array([value, value, value], dtype="int16").tobytes())
    return buf.getvalue()


# The size a fake take must declare. copy_verified now refuses a stream that
# ends before the announced size, so a fake that lies about it is rejected —
# rightly.
_WAV_SIZE = len(_wav_bytes())


class _FakeBlock:
    """Stands in for StorageDevice.block: a Path whose .open() is read."""

    def __init__(self, data=b"\x00" * 512):
        self._data = data

    def open(self, mode):
        import io

        return io.BytesIO(self._data)

    def __str__(self):
        return "/dev/fake"


class _RecordingFile(io.BytesIO):
    """Like _FakeBlock's file, but tells `order` when it is closed."""

    def __init__(self, data, order, label):
        super().__init__(data)
        self._order = order
        self._label = label

    def __exit__(self, *exc):
        self._order.append(("close", self._label))
        return super().__exit__(*exc)


class _RecordingBlock:
    """Stands in for StorageDevice.block; records when it is opened/closed,
    so a test can tell whether two cards were read one after another or
    overlapped."""

    def __init__(self, order, label, data=b"\x00" * 512):
        self._order = order
        self._label = label
        self._data = data

    def open(self, mode):
        self._order.append(("open", self._label))
        return _RecordingFile(self._data, self._order, self._label)

    def __str__(self):
        return f"/dev/{self._label}"


def _patch_intake_plumbing(monkeypatch, tmp_path, card_cls):
    """Point app.py's Card/Ledger/destination_dir at fakes and a scratch
    ledger, exactly what a real import needs, none of it touching hardware."""
    from conteur.ledger import Ledger

    monkeypatch.setattr(app_mod, "Card", card_cls)
    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path / "dest")
    monkeypatch.setattr(
        app_mod, "Ledger",
        lambda serial: Ledger(serial, root=tmp_path / "ledger"),
    )


def test_take_submission_always_happens_on_the_gui_thread(qapp, tmp_path, monkeypatch):
    """queue_submit only records a path; handing it to the naming queue --
    add_take mutates a QListWidget -- must happen on the GUI thread, from
    _refresh_import, never inside the import thread itself."""
    import threading
    from datetime import datetime

    import conteur.app as app_mod
    from conteur.card import Take
    from conteur.devices import StorageDevice

    when = datetime(2026, 9, 7, 11, 14, 32)

    class FakeCard:
        serial = "800A-F63E"

        def __init__(self, fh):
            pass

        def takes(self):
            return [Take("00001_A.WAV", _WAV_SIZE, 3, when), Take("00002_A.WAV", _WAV_SIZE, 3, when)]

        def stream(self, take, chunk=1 << 20):
            yield _wav_bytes(1 if take.name.startswith("00001") else 2)

    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            pass

    _patch_intake_plumbing(monkeypatch, tmp_path, FakeCard)

    win = MainWindow(find_rx=lambda: None, queue=FakeQueue(), find_storage=list)
    win._cards = [StorageDevice("800A-F63E", _FakeBlock(), None, True)]

    main_thread = threading.current_thread()
    threads_seen = []
    original = win._submit_imported

    def spy(path, _orig=original):
        threads_seen.append(threading.current_thread())
        return _orig(path)

    win._submit_imported = spy

    win.start_import()
    win._import_thread.join(5)
    win._refresh_import()

    assert win._snapshot.is_finished()
    assert win._snapshot.done == 2
    assert len(threads_seen) == 2
    assert all(t is main_thread for t in threads_seen)


def test_two_cards_are_cumulative_and_finished_only_after_the_second(
    qapp, tmp_path, monkeypatch,
):
    """Pins the two bugs found in review: a second card's inventory must add
    to the first's `total`, and `finished` must not flip after the first
    card's own "done" event while a second card is still queued.

    Observed mid-run, not just after both cards join: a version that flips
    `finished` inside `absorb`'s "done" branch (deviation 3's original bug)
    would still pass an end-of-run-only assertion, since by the time the
    thread is joined both cards are done regardless of when the flag moved.
    Card 2 is deliberately held open on a threading.Event so the test can
    look at the snapshot -- and at the button and timer, the user-visible
    consequence -- while card 1 is finished but card 2 demonstrably is not.
    """
    import threading
    from datetime import datetime

    from conteur.card import Take
    from conteur.devices import StorageDevice

    when = datetime(2026, 9, 7, 11, 14, 32)
    started2 = threading.Event()
    hold2 = threading.Event()

    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            pass

    class Card1:
        serial = "AAAA-0001"

        def __init__(self, fh):
            pass

        def takes(self):
            return [Take("00001_A.WAV", _WAV_SIZE, 3, when)]

        def stream(self, take, chunk=1 << 20):
            yield _wav_bytes(1)

    class Card2:
        serial = "BBBB-0002"

        def __init__(self, fh):
            pass

        def takes(self):
            return [Take("00001_B.WAV", _WAV_SIZE, 3, when), Take("00002_B.WAV", _WAV_SIZE, 3, when)]

        def stream(self, take, chunk=1 << 20):
            # Card 1 must be entirely finished before this ever runs, since
            # cards are read one after another -- so parking here proves the
            # run is genuinely mid-second-card, not merely "not yet started".
            started2.set()
            hold2.wait(5)
            yield _wav_bytes(2 if take.name.startswith("00001") else 3)

    made = iter([Card1, Card2])
    monkeypatch.setattr(app_mod, "Card", lambda fh: next(made)(fh))
    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path / "dest")
    from conteur.ledger import Ledger
    monkeypatch.setattr(
        app_mod, "Ledger", lambda serial: Ledger(serial, root=tmp_path / "ledger"),
    )

    devices = [
        StorageDevice("AAAA-0001", _FakeBlock(), None, True),
        StorageDevice("BBBB-0002", _FakeBlock(), None, True),
    ]
    # Cards stay "plugged in" for the whole test, including after the import
    # finishes -- refresh_device() re-scans via find_storage, and a version
    # returning nothing would hide whether the button legitimately re-enables.
    win = MainWindow(find_rx=lambda: None, queue=FakeQueue(),
                     find_storage=lambda: list(devices))
    win._cards = list(devices)

    win.start_import()

    assert started2.wait(2), "card 2 was never reached"
    # Card 1 is done; card 2's own first copy is deliberately still blocked.
    # The run -- and therefore the button and the timer -- must still read
    # as in progress.
    assert win._snapshot.is_finished() is False
    win._refresh_import()
    assert win._import_timer.isActive()
    assert not win.import_button.isEnabled()

    hold2.set()
    win._import_thread.join(5)
    win._refresh_import()

    assert win._snapshot.total == 3   # 1 + 2, not replaced by the second card
    assert win._snapshot.done == 3    # the run did not stop after card 1's "done"
    assert win._snapshot.is_finished() is True
    assert not win._import_timer.isActive()
    assert win.import_button.isEnabled()


def test_cards_are_read_one_after_another_not_overlapped(qapp, tmp_path, monkeypatch):
    from datetime import datetime

    import conteur.app as app_mod
    from conteur.card import Take
    from conteur.devices import StorageDevice

    when = datetime(2026, 9, 7, 11, 14, 32)
    order: list = []

    class FakeCard:
        def __init__(self, fh):
            self.serial = "shared"

        def takes(self):
            return [Take("00001_X.WAV", _WAV_SIZE, 3, when)]

        def stream(self, take, chunk=1 << 20):
            yield _wav_bytes(1)

    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            pass

    monkeypatch.setattr(app_mod, "Card", FakeCard)
    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path / "dest")
    from conteur.ledger import Ledger
    counter = iter(["one", "two"])
    monkeypatch.setattr(
        app_mod, "Ledger",
        lambda serial: Ledger(next(counter), root=tmp_path / "ledger"),
    )

    win = MainWindow(find_rx=lambda: None, queue=FakeQueue(), find_storage=list)
    win._cards = [
        StorageDevice("one", _RecordingBlock(order, "one"), None, True),
        StorageDevice("two", _RecordingBlock(order, "two"), None, True),
    ]

    win.start_import()
    win._import_thread.join(5)
    win._refresh_import()

    assert order == [("open", "one"), ("close", "one"), ("open", "two"), ("close", "two")]


def test_shutdown_stops_an_import_in_flight_and_does_not_outlive_the_window(
    qapp, tmp_path, monkeypatch,
):
    import threading
    from datetime import datetime

    import conteur.app as app_mod
    from conteur.card import Take
    from conteur.devices import StorageDevice

    when = datetime(2026, 9, 7, 11, 14, 32)
    started = threading.Event()
    hold = threading.Event()

    class HoldingCard:
        serial = "800A-F63E"

        def __init__(self, fh):
            pass

        def takes(self):
            return [Take("00001_A.WAV", _WAV_SIZE, 3, when)]

        def stream(self, take, chunk=1 << 20):
            started.set()
            hold.wait(5)  # released by the test itself, below
            yield _wav_bytes(1)

    class FakeQueue:
        def start(self):
            pass

        def stop(self):
            pass

        def join(self, timeout_s):
            pass

        def submit(self, path, when, on_done):
            pass

    _patch_intake_plumbing(monkeypatch, tmp_path, HoldingCard)

    win = MainWindow(find_rx=lambda: None, queue=FakeQueue(), find_storage=list)
    win._cards = [StorageDevice("800A-F63E", _FakeBlock(), None, True)]
    win.start_import()

    assert started.wait(2), "the import thread never reached the copy"
    thread = win._import_thread

    # A short timeout: shutdown() must not hang waiting for a copy that is
    # deliberately still blocked.
    win.shutdown(timeout_s=0.2)
    assert win._import_stop.is_set()
    assert win._import_thread is None

    # Release the blocked copy and confirm the thread genuinely terminates --
    # it must not be left running past the window's own shutdown.
    hold.set()
    thread.join(5)
    assert not thread.is_alive()


def test_a_card_that_fails_to_open_does_not_stop_the_next_one(
    qapp, tmp_path, monkeypatch,
):
    """Anything escaping intake() -- not just OSError/ValueError -- must be
    caught, reported, and must not stop the next card or leave `finished`
    unset (the Important-1 review finding)."""
    from datetime import datetime

    import conteur.app as app_mod
    from conteur.card import Take
    from conteur.devices import StorageDevice

    when = datetime(2026, 9, 7, 11, 14, 32)

    class FailingBlock:
        def open(self, mode):
            raise RuntimeError("boom")  # deliberately not OSError/ValueError

        def __str__(self):
            return "/dev/broken"

    class GoodCard:
        serial = "800A-F63E"

        def __init__(self, fh):
            pass

        def takes(self):
            return [Take("00001_A.WAV", _WAV_SIZE, 3, when)]

        def stream(self, take, chunk=1 << 20):
            yield _wav_bytes(1)

    class FakeQueue:
        def start(self):
            pass

        def submit(self, path, when, on_done):
            pass

    _patch_intake_plumbing(monkeypatch, tmp_path, GoodCard)

    win = MainWindow(find_rx=lambda: None, queue=FakeQueue(), find_storage=list)
    win._cards = [
        StorageDevice("broken", FailingBlock(), None, True),
        StorageDevice("800A-F63E", _FakeBlock(), None, True),
    ]

    win.start_import()
    win._import_thread.join(5)
    win._refresh_import()

    assert win._snapshot.is_finished()
    assert any("boom" in line for line in win._snapshot.lines)
    assert win._snapshot.failures == 1
    assert win._snapshot.done == 1     # the second, good card was still imported


def test_the_import_outcome_survives_the_device_poll(qapp):
    """The device poll fires every two seconds and used to wipe the summary,
    so the outcome of a minutes-long import was never read by anyone."""
    from conteur.intake import Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    for event in [Event("inventory", detail="2"), Event("recorded", take="a.wav"),
                  Event("skipped", take="b.wav")]:
        win._snapshot.absorb(event)
    win._snapshot.mark_finished()
    win._refresh_import()
    assert "Récupération terminée" in win.status_label.text(), \
        win.status_label.text()
    win.refresh_device()
    assert "Récupération terminée" in win.status_label.text(), \
        "the device poll wiped the outcome"


def test_the_summary_tells_new_from_already_imported(qapp):
    """"0/8" reads like a failure. Nothing new is not the same as nothing done."""
    from conteur.intake import Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    for event in [Event("inventory", detail="8")] + [
            Event("skipped", take=f"{i}.wav") for i in range(8)]:
        win._snapshot.absorb(event)
    win._snapshot.mark_finished()
    text = win._snapshot.summary()
    assert "8" in text and "rien de nouveau" in text.lower(), text


# --- erasable() / erase_serials(): one row per card, one action per card,
# and the device list re-resolved between erases (a completed erase makes the
# transmitter re-enumerate). No test here ever opens a real hidraw node or
# calls the real eraser: erase_card's own eraser and every StorageDevice come
# from fakes, and Ledger only ever writes under tmp_path.

def test_a_fully_held_card_is_offered_for_erasing(qapp, tmp_path):
    from conteur.intake import CardVerdict, Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.absorb(Event("verdict", verdict=CardVerdict(
        serial="800A-92D6", digests=frozenset({"aa" * 32}),
        inventory=(), complete=True)))
    assert [s for s, allowed, _ in win.erasable() if allowed] == ["800A-92D6"]


def test_a_card_with_an_uncopied_take_is_not_offered(qapp):
    from conteur.intake import CardVerdict, Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.absorb(Event("verdict", verdict=CardVerdict(
        serial="800A-F63E", digests=frozenset(), inventory=(), complete=False)))
    assert [s for s, allowed, _ in win.erasable() if allowed] == []
    # Still listed, so the user learns why it is locked rather than wondering
    # where the row went.
    serial, allowed, reason = win.erasable()[0]
    assert serial == "800A-F63E" and not allowed and reason


def test_two_cards_are_erased_one_at_a_time_with_the_list_re_resolved(qapp):
    """An erase makes the transmitter re-enumerate, so the device list taken
    before the first erase is stale by the second.

    Correction to the original brief: that version stubbed `_erase_one` to
    take a bare serial and asserted `find_storage` was called at least twice
    -- but `_erase_one` was the only thing calling it, and stubbing it is
    exactly what removes that call. `erase_serials` now resolves the device
    itself, once per card inside the loop, and hands it to
    `_erase_one(device, verdict)` -- which is what this test stubs and
    exercises instead.
    """
    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict
    from pathlib import Path

    resolved = []

    def find_storage():
        resolved.append(len(resolved))
        return [StorageDevice("800A-92D6", Path("/dev/sdc"), Path("/dev/hidraw4"), True),
                StorageDevice("800A-F63E", Path("/dev/sdb"), Path("/dev/hidraw6"), True)]

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=find_storage)
    for serial in ("800A-92D6", "800A-F63E"):
        win._snapshot.verdicts[serial] = CardVerdict(
            serial=serial, digests=frozenset(), inventory=(), complete=True)
    # __init__ ends with refresh_device(), which already resolves once on its
    # own; a threshold of ">= 2" would then pass even if erase_serials hoisted
    # the call out of its loop and resolved only once itself. Clearing here
    # and asserting the exact count is what actually pins "one resolution per
    # card, inside the loop".
    resolved.clear()

    order = []
    win._erase_one = lambda device, verdict: order.append(verdict.serial)
    win.erase_serials(["800A-92D6", "800A-F63E"])
    assert order == ["800A-92D6", "800A-F63E"]
    assert len(resolved) == 2, "one resolution per card, inside the loop"


def test_erase_serials_skips_a_serial_the_snapshot_never_verdicted(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    calls = []
    win._erase_one = lambda device, verdict: calls.append(verdict.serial)
    win.erase_serials(["800A-92D6"])
    assert calls == []


def test_erase_serials_reports_and_skips_an_absent_device(qapp):
    from conteur.intake import CardVerdict

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.verdicts["800A-92D6"] = CardVerdict(
        serial="800A-92D6", digests=frozenset(), inventory=(), complete=True)
    calls = []
    win._erase_one = lambda device, verdict: calls.append(verdict.serial)

    win.erase_serials(["800A-92D6"])

    assert calls == []
    assert "appareil absent" in win.status_label.text()


# --- _erase_one(): routes erase_card's events to the status line without
# re-implementing any of its checks. erase_card itself is faked here, the
# same way test_erase_card.py fakes it, so these tests never touch card.py's
# real FAT parsing, the real eraser, or a real hidraw node.

def test_a_successful_erase_drops_the_verdict_and_says_so(qapp, tmp_path, monkeypatch):
    from pathlib import Path

    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict, Event

    block = tmp_path / "sdc"
    block.write_bytes(b"\x00")

    class FakeCard:
        serial = "800A-92D6"

    monkeypatch.setattr(app_mod, "Card", lambda fh: FakeCard())
    monkeypatch.setattr(
        app_mod, "erase_card",
        lambda card, ledger, verdict, node: iter([
            Event("erasing", detail=verdict.serial),
            Event("erased", detail=verdict.serial),
        ]))

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    verdict = CardVerdict(serial="800A-92D6", digests=frozenset({"aa" * 32}),
                          inventory=(), complete=True)
    win._snapshot.verdicts[verdict.serial] = verdict
    device = StorageDevice("800A-92D6", block, Path("/dev/hidraw4"), True)

    win._erase_one(device, verdict)

    assert "800A-92D6" not in win._snapshot.verdicts
    assert "carte effacée" in win.status_label.text()


def test_a_refused_erase_keeps_the_verdict_and_reports_the_reason(
    qapp, tmp_path, monkeypatch,
):
    from pathlib import Path

    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict, Event

    block = tmp_path / "sdc"
    block.write_bytes(b"\x00")

    class FakeCard:
        serial = "800A-92D6"

    monkeypatch.setattr(app_mod, "Card", lambda fh: FakeCard())
    monkeypatch.setattr(
        app_mod, "erase_card",
        lambda card, ledger, verdict, node: iter([
            Event("refused", detail="la carte a changé depuis la récupération"),
        ]))

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    verdict = CardVerdict(serial="800A-92D6", digests=frozenset(),
                          inventory=(), complete=True)
    win._snapshot.verdicts[verdict.serial] = verdict
    device = StorageDevice("800A-92D6", block, Path("/dev/hidraw4"), True)

    win._erase_one(device, verdict)

    assert verdict.serial in win._snapshot.verdicts
    assert "la carte a changé depuis la récupération" in win.status_label.text()


def test_an_unknown_outcome_is_shown_distinctly_from_a_failure(
    qapp, tmp_path, monkeypatch,
):
    """Silence from the transmitter is not a failure: the command may well
    have gone through, and the honest answer is to re-read the card."""
    from pathlib import Path

    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict, Event

    block = tmp_path / "sdc"
    block.write_bytes(b"\x00")

    class FakeCard:
        serial = "800A-92D6"

    monkeypatch.setattr(app_mod, "Card", lambda fh: FakeCard())
    monkeypatch.setattr(
        app_mod, "erase_card",
        lambda card, ledger, verdict, node: iter([
            Event("unknown",
                 detail=f"{verdict.serial} : état indéterminé, il faut relire la carte"),
        ]))

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    verdict = CardVerdict(serial="800A-92D6", digests=frozenset(),
                          inventory=(), complete=True)
    win._snapshot.verdicts[verdict.serial] = verdict
    device = StorageDevice("800A-92D6", block, Path("/dev/hidraw4"), True)

    win._erase_one(device, verdict)

    assert "état indéterminé" in win.status_label.text()
    assert "échoué" not in win.status_label.text()
    # Undetermined, not erased: the card must be re-read, so the verdict is
    # not dropped as it would be on a confirmed success.
    assert verdict.serial in win._snapshot.verdicts


def test_a_failed_erase_is_reported_and_keeps_the_verdict(qapp, tmp_path, monkeypatch):
    from pathlib import Path

    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict, Event

    block = tmp_path / "sdc"
    block.write_bytes(b"\x00")

    class FakeCard:
        serial = "800A-92D6"

    monkeypatch.setattr(app_mod, "Card", lambda fh: FakeCard())
    monkeypatch.setattr(
        app_mod, "erase_card",
        lambda card, ledger, verdict, node: iter([
            Event("failed", detail=f"{verdict.serial} : l'effacement a échoué"),
        ]))

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    verdict = CardVerdict(serial="800A-92D6", digests=frozenset(),
                          inventory=(), complete=True)
    win._snapshot.verdicts[verdict.serial] = verdict
    device = StorageDevice("800A-92D6", block, Path("/dev/hidraw4"), True)

    win._erase_one(device, verdict)

    assert "l'effacement a échoué" in win.status_label.text()
    assert verdict.serial in win._snapshot.verdicts


def test_a_vanished_card_is_reported_not_crashed(qapp, tmp_path):
    """The card can vanish between resolution and open -- unplugged, or a
    transmitter mid-reenumeration from a previous erase in the same batch.
    The window must survive the click that triggered it."""
    from pathlib import Path

    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    verdict = CardVerdict(serial="800A-92D6", digests=frozenset(),
                          inventory=(), complete=True)
    win._snapshot.verdicts[verdict.serial] = verdict
    device = StorageDevice("800A-92D6", tmp_path / "gone", Path("/dev/hidraw4"), True)

    win._erase_one(device, verdict)  # must not raise

    assert "800A-92D6" in win.status_label.text()


# --- the per-card erase control: no widget could reach erasable()/
# erase_serials() before this, so the feature, even correctly implemented,
# was unreachable from the window. One button per card, never a control
# that erases more than one -- that is the property decision 1 (no global
# erase) rests on, so it is asserted explicitly below.

def test_a_complete_verdict_gets_one_enabled_button(qapp):
    from conteur.intake import CardVerdict, Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.absorb(Event("verdict", verdict=CardVerdict(
        serial="800A-92D6", digests=frozenset({"aa" * 32, "bb" * 32}),
        inventory=(), complete=True)))
    win._rebuild_erase_controls()

    assert len(win._erase_buttons) == 1
    button = win._erase_buttons[0]
    assert button.isEnabled()
    assert "800A-92D6" in button.text()
    assert "2 prise(s) vérifiée(s)" in button.text()


def test_an_incomplete_verdict_gets_a_disabled_row_with_the_reason(qapp):
    from conteur.intake import CardVerdict, Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.absorb(Event("verdict", verdict=CardVerdict(
        serial="800A-F63E", digests=frozenset(), inventory=(), complete=False)))
    win._rebuild_erase_controls()

    assert len(win._erase_buttons) == 1
    button = win._erase_buttons[0]
    assert not button.isEnabled()
    assert "800A-F63E" in button.text()
    assert "des prises n'ont pas été copiées" in button.text()


def test_clicking_the_enabled_button_erases_only_that_one_card(qapp):
    from conteur.intake import CardVerdict, Event

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    for serial in ("800A-92D6", "800A-F63E"):
        win._snapshot.absorb(Event("verdict", verdict=CardVerdict(
            serial=serial, digests=frozenset({"aa" * 32}),
            inventory=(), complete=True)))
    win._rebuild_erase_controls()
    assert len(win._erase_buttons) == 2

    calls = []
    win.erase_serials = lambda serials: calls.append(list(serials))

    by_serial = {b.text().split()[1]: b for b in win._erase_buttons}
    by_serial["800A-92D6"].click()

    assert calls == [["800A-92D6"]]

    # No control erases more than one card at a time: clicking the other
    # button is a second, independent call, never one that folds both in.
    by_serial["800A-F63E"].click()
    assert calls == [["800A-92D6"], ["800A-F63E"]]
    assert all(len(call) == 1 for call in calls), \
        "a single click must never erase more than one card"


def test_starting_an_import_clears_the_erase_controls(qapp, tmp_path, monkeypatch):
    """A verdict in flight means nothing yet -- the previous run's cards must
    not stay offered while a new one is reading them."""
    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict, Event

    class FakeCard:
        serial = "800A-F63E"

        def __init__(self, fh):
            pass

        def takes(self):
            return []

    _patch_intake_plumbing(monkeypatch, tmp_path, FakeCard)

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    win._snapshot.absorb(Event("verdict", verdict=CardVerdict(
        serial="800A-92D6", digests=frozenset({"aa" * 32}),
        inventory=(), complete=True)))
    win._rebuild_erase_controls()
    assert len(win._erase_buttons) == 1

    win._cards = [StorageDevice("800A-F63E", _FakeBlock(), None, True)]
    win.start_import()

    # Cleared synchronously, before the import thread has even run: a
    # verdict in flight means nothing yet.
    assert win._erase_buttons == []

    win._import_thread.join(5)


def test_a_successful_erase_rebuilds_the_controls_without_the_erased_card(
    qapp, tmp_path, monkeypatch,
):
    from pathlib import Path

    from conteur.devices import StorageDevice
    from conteur.intake import CardVerdict, Event

    block = tmp_path / "sdc"
    block.write_bytes(b"\x00")

    class FakeCard:
        serial = "800A-92D6"

    monkeypatch.setattr(app_mod, "Card", lambda fh: FakeCard())
    monkeypatch.setattr(
        app_mod, "erase_card",
        lambda card, ledger, verdict, node: iter([Event("erased", detail=verdict.serial)]))

    win = MainWindow(find_rx=lambda: None, queue=None, find_storage=list)
    verdict = CardVerdict(serial="800A-92D6", digests=frozenset({"aa" * 32}),
                          inventory=(), complete=True)
    win._snapshot.verdicts[verdict.serial] = verdict
    win._rebuild_erase_controls()
    assert len(win._erase_buttons) == 1

    win._erase_one(StorageDevice("800A-92D6", block, Path("/dev/hidraw4"), True), verdict)

    assert win._erase_buttons == []
