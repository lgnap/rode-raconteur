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
