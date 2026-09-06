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
    # Le jeton interne reste "title" ; l'interface, elle, est en français.
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


class FakePa:
    """Contexte PortAudio bouchonné : sa liste de périphériques est figée."""

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
    # La file a déjà renommé le fichier, mais `take_named` n'a pas encore été
    # délivré : la ligne tient un chemin provisoire périmé. Un double-clic
    # levait alors FileNotFoundError dans le fil graphique.
    from datetime import datetime

    import conteur.app as app_mod

    when = datetime(2026, 9, 6, 14, 32, 8)
    stale = tmp_path / "2026-09-06_143208_sans-nom.wav"   # jamais créé

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
    # PortAudio énumère les périphériques à Pa_Initialize() et PyAudio n'offre
    # pas de rebalayage : sans recréer le contexte, le sondage relit
    # indéfiniment l'instantané du démarrage et le branchement passe inaperçu.
    import conteur.app as app_mod

    monkeypatch.setattr(app_mod, "load_model", lambda: object())

    contexts = [FakePa(["HDA Intel PCH"]),                      # toujours rien
                FakePa(["HDA Intel PCH", "Wireless PRO RX"])]   # RX branché
    made = []

    def factory():
        pa = contexts.pop(0)
        made.append(pa)
        return pa

    first = FakePa(["HDA Intel PCH"])
    win = MainWindow(pa=first, pa_factory=factory)

    # Le contexte du démarrage ne voyait pas le RX ; il a été rendu.
    assert first.terminated is True
    assert win.record_button.isEnabled() is False

    win.refresh_device()

    assert win.record_button.isEnabled() is True
    assert "Wireless PRO RX" in win.status_label.text()
    assert made[0].terminated is True          # aucun contexte n'est laissé ouvert
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

    # Recréer le contexte pendant la capture emporterait le flux ouvert.
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

    assert win._model is model                      # chargé sans aucune capture
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

    # La file reçoit sa sentinelle après les travaux déjà en attente : ceux-ci
    # ont le temps imparti pour aboutir plutôt que d'être jetés en silence.
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

    win._refresh_level()                 # aucun bloc : rien à mesurer

    assert win.level_bar.value() == win.level_bar.minimum()

    # Et par le vrai minuteur, qui tourne dès le début de la capture, avant
    # que le premier bloc ne soit arrivé.
    win._level_timer.setInterval(5)
    win._level_timer.start()
    QTest.qWait(60)
    win._level_timer.stop()
    assert win.level_bar.value() == win.level_bar.minimum()


def test_take_named_crosses_the_thread_boundary(qapp, tmp_path):
    # La file de nommage vit dans un autre fil : le résultat n'atteint la
    # ligne qu'au travers du signal, en connexion différée.
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


# --- remontée de la cause d'un échec de nommage ---


def test_describe_error_translates_the_cublas_failure():
    from conteur.app import describe_error

    reason = describe_error(RuntimeError("Library libcublas.so.12 is not found"))
    assert "CUDA" in reason
    assert "libcublas" not in reason          # l'utilisateur n'a pas à décoder ça


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

    # Le sondage périphérique ne doit pas effacer la cause.
    win.refresh_device()
    assert "CUDA" in win.status_label.text()


# --- Ctrl+C et retour visuel du chargement ---


def test_ctrl_c_asks_the_application_to_quit(qapp):
    """Qt tourne en C++ : sans minuteur rendant la main à l'interpréteur, le
    gestionnaire Python ne s'exécute jamais et Ctrl+C reste sans effet."""
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
