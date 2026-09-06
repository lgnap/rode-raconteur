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
        app_mod, "record",
        lambda pa, device, stop, on_block=None: np.array([1, 2, 3], dtype=np.int16),
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


def test_level_bar_starts_at_floor(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None)
    assert win.level_bar.value() == win.level_bar.minimum()


def test_set_level_is_directly_callable_and_moves_the_bar(qapp):
    # Le mètre doit être testable sans passer par le QTimer : la boucle
    # d'évènements ne tourne pas dans les tests (offscreen, pas d'app.exec()),
    # donc le déclenchement périodique réel du QTimer n'est pas couvert ici.
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
        # Bloque jusqu'à ce que le test lève `stop`, pour observer le minuteur
        # pendant que la capture est réellement en cours.
        release.wait(1.0)
        return np.array([1, 2, 3], dtype=np.int16)

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", fake_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    win = MainWindow(find_rx=lambda: Dev(), queue=FakeQueue(), pa=object())

    assert win._level_timer.isActive() is False
    win.toggle_recording()   # démarre
    assert win._level_timer.isActive() is True
    release.set()
    win.toggle_recording()   # arrête
    assert win._level_timer.isActive() is False


def test_write_failure_is_reported_with_path_and_cause(qapp):
    win = MainWindow(find_rx=lambda: None, queue=None)
    win.report_write_error("/n/existe/pas/a.wav", PermissionError("refusé"))
    text = win.status_label.text()
    assert "/n/existe/pas/a.wav" in text
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

    win.toggle_recording()   # démarre
    win.toggle_recording()   # arrête : l'écriture échoue

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
    # Course réelle : l'utilisateur renomme la prise à la main avant que la
    # file de nommage automatique n'ait fini son travail sur cette même
    # ligne. La file tient encore le chemin provisoire d'origine ; comme il
    # n'existe plus après le renommage manuel, `name_recording` échoue et le
    # résultat qui arrive ensuite est un "échec" tardif. Il ne doit pas
    # écraser le nom donné à la main.
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

    # Résultat automatique tardif pour la même ligne, sur le nom provisoire.
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
    win.toggle_recording()   # démarre
    win.toggle_recording()   # arrête : la file (synchrone ici) nomme aussitôt

    row = 0
    final_path, when = win._takes_meta[row]
    assert final_path.name == "2026-09-06_143208_le-loup.wav"
    assert "le-loup" in win.take_text(row)
    assert "title" in win.take_text(row)


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
    """File bouchonnée qui note ce qu'on lui soumet."""

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
    # Le récepteur part entre la détection et `pa.open()` : `record` lève dans
    # le fil de capture. Sans garde, `_samples` reste None et `write_wav(None)`
    # fait lever un AttributeError au cœur d'un slot Qt.
    import conteur.app as app_mod

    def exploding_record(pa, device, stop, on_block=None):
        raise OSError("device disconnected")

    monkeypatch.setattr(app_mod, "destination_dir", lambda when: tmp_path)
    monkeypatch.setattr(app_mod, "record", exploding_record)
    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    queue = RecordingQueue()
    win = MainWindow(find_rx=lambda: Dev(), queue=queue, pa=object())

    win.toggle_recording()   # démarre
    win.toggle_recording()   # arrête : le fil a levé

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

    win.toggle_recording()   # démarre
    found[0] = None          # le RX disparaît pendant la prise

    win.refresh_device()

    # Le bouton porte « Arrêter » : le désactiver rendrait la prise
    # inarrêtable et l'audio déjà capté définitivement inatteignable.
    assert win.record_button.isEnabled() is True
    assert win.record_button.text() == "Arrêter"
    assert app_mod.RX_LOST in win.status_label.text()

    release.set()
    win._capture.join(2.0)
    win.refresh_device()     # le fil a rendu la main : le fragment est écrit

    assert len(win._rows) == 1
    assert app_mod.INCOMPLETE in win.take_text(0)
    assert len(queue.submitted) == 1
    assert queue.submitted[0][0].exists()


def test_disconnect_is_detected_by_the_real_poll_timer(qapp, tmp_path, monkeypatch):
    # Le chemin réel passe par le QTimer de 2 s : on le raccourcit et on fait
    # tourner une vraie boucle d'évènements plutôt que d'appeler la fonction.
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
    # Le sondage toutes les deux secondes réécrivait le nom du périphérique
    # par-dessus le message d'erreur : il disparaissait avant d'être lu.
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
