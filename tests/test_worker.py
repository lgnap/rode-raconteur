import threading
from datetime import datetime
from pathlib import Path

from conteur.job import NameResult
from conteur.worker import NamingQueue

WHEN = datetime(2026, 9, 6, 14, 32, 8)


def test_jobs_run_one_at_a_time():
    concurrent = []
    active = 0
    lock = threading.Lock()

    def runner(path, when):
        nonlocal active
        with lock:
            active += 1
            concurrent.append(active)
        with lock:
            active -= 1
        return NameResult(path=path, slug=path.stem, origin="transcript")

    q = NamingQueue(runner)
    q.start()
    done = threading.Event()
    seen = []

    def on_done(src, result):
        seen.append((src, result))
        if len(seen) == 5:
            done.set()

    for i in range(5):
        q.submit(Path(f"/tmp/{i}.wav"), WHEN, on_done)
    assert done.wait(5.0)
    q.stop()
    q.join(5.0)
    assert max(concurrent) == 1
    assert len(seen) == 5


def test_a_failing_job_does_not_kill_the_queue():
    results = []
    done = threading.Event()

    def runner(path, when):
        if path.name == "bad.wav":
            raise RuntimeError("boom")
        return NameResult(path=path, slug="ok", origin="transcript")

    def on_done(src, result):
        results.append((src.name, result))
        if len(results) == 2:
            done.set()

    q = NamingQueue(runner)
    q.start()
    q.submit(Path("/tmp/bad.wav"), WHEN, on_done)
    q.submit(Path("/tmp/good.wav"), WHEN, on_done)
    assert done.wait(5.0)
    q.stop()
    q.join(5.0)
    assert results[0][1] is None            # échec signalé par None
    assert results[1][1].slug == "ok"
