"""The QBO import log under Server Edition's several worker processes.

docker-entrypoint.sh starts uvicorn with APP_WORKERS processes (2 in
docker-compose.yml, 4 in docker-compose.prod.yml), and the page's poll or
an admin's Start can reach any of them. Each process has its own boot id,
and the log called any active run owned by another boot id interrupted:
the first poll that reached a sibling worker marked a live import
"Interrupted", and the next start on that worker began a second import
of the same company while the first was still writing.

A run's owner now beats a heartbeat into the log while it works; another
process calls the run interrupted only once that heartbeat has stopped.
"""

from pathlib import Path
from threading import Event
import time

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_sqlite_tuning
from app.services import qbo_import, qbo_import_runs as runs, qbo_progress, storage


@pytest.fixture(autouse=True)
def private_log_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "backups_root", lambda: tmp_path / "private")


@pytest.fixture
def run_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'company.db'}")
    enable_sqlite_tuning(engine)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as db:
        yield db
    engine.dispose()


def _sibling(store):
    """Another worker process: its own connection to the same log file."""
    path = Path(store.connection.execute("PRAGMA database_list").fetchone()[2])
    return runs.ImportStore(path)


def test_a_poll_on_another_worker_leaves_a_live_import_running(db_session, monkeypatch):
    monkeypatch.setattr(runs, "_BOOT_ID", "worker-a")
    owner = runs.store_for(db_session)
    state = owner.reserve(["accounts"], "eric")
    owner.publish(state, "query", "Fetching Accounts page 1")

    monkeypatch.setattr(runs, "_BOOT_ID", "worker-b")
    sibling = _sibling(owner)
    snapshot = sibling.latest()
    assert snapshot["run"]["run_id"] == state["run_id"]
    assert snapshot["run"]["status"] in runs.ACTIVE
    assert all(row["code"] != "IMPORT_INTERRUPTED" for row in snapshot["events"])

    # ...and a start there is refused while the first import works.
    with pytest.raises(HTTPException) as refused:
        sibling.reserve(["items"], "michelle")
    assert refused.value.status_code == 409
    assert refused.value.detail["run_id"] == state["run_id"]

    # The owner goes on logging into the same run.
    monkeypatch.setattr(runs, "_BOOT_ID", "worker-a")
    owner.publish(state, "fetch", "Fetched 1 source items")
    events = owner.latest()["events"]
    assert [row["sequence"] for row in events] == list(range(1, len(events) + 1))
    sibling.connection.close()


def test_a_run_whose_worker_stopped_beating_is_interrupted(db_session, monkeypatch):
    monkeypatch.setattr(runs, "_BOOT_ID", "worker-a")
    owner = runs.store_for(db_session)
    state = owner.reserve(["accounts"], "eric")

    monkeypatch.setattr(runs, "_BOOT_ID", "worker-b")
    later = time.time() + runs.STALE_SECONDS + 1
    monkeypatch.setattr(runs, "_clock", lambda: later)
    sibling = _sibling(owner)
    snapshot = sibling.latest()
    assert snapshot["run"]["run_id"] == state["run_id"]
    assert snapshot["run"]["status"] == "interrupted"
    assert snapshot["events"][-1]["code"] == "IMPORT_INTERRUPTED"
    assert sibling.reserve(["items"], "michelle")["run_id"] != state["run_id"]
    sibling.connection.close()


def test_a_start_on_another_worker_cannot_slip_in_between_read_and_write(
    db_session, monkeypatch
):
    """Two workers starting at once: the second's read of "no run is
    active" and its write of a new run must not straddle the first's."""
    monkeypatch.setattr(runs, "_BOOT_ID", "worker-a")
    first = runs.store_for(db_session)
    first.connection.execute("PRAGMA busy_timeout = 50")
    monkeypatch.setattr(runs, "_BOOT_ID", "worker-b")
    second = _sibling(first)
    original_read = second._read
    started = []

    def read_then_let_the_other_worker_start():
        state = original_read()
        if not started:
            started.append(True)
            monkeypatch.setattr(runs, "_BOOT_ID", "worker-a")
            try:
                started.append(first.reserve(["accounts"], "eric"))
            except Exception as exc:  # it must wait for worker-b's write
                started.append(exc)
            monkeypatch.setattr(runs, "_BOOT_ID", "worker-b")
        return state

    second._read = read_then_let_the_other_worker_start
    accepted = second.reserve(["items"], "michelle")
    second._read = original_read
    # worker-a could not start inside worker-b's reservation...
    assert not isinstance(started[1], dict)
    # ...so exactly one import is live, and it is worker-b's.
    assert first.latest()["run"]["run_id"] == accepted["run_id"]
    second.connection.close()


def test_a_running_import_keeps_beating(run_db, monkeypatch):
    monkeypatch.setattr(runs, "HEARTBEAT_SECONDS", 0.02)
    entered, release = Event(), Event()

    @qbo_progress.stage("accounts")
    def importer(db):
        entered.set()
        assert release.wait(3)
        return {"imported": 0, "errors": []}

    monkeypatch.setattr(qbo_import, "import_accounts", importer)
    store = runs.store_for(run_db)
    runs.start_run(run_db, ["accounts"], "eric")

    def beat():
        return store.connection.execute(
            "SELECT heartbeat FROM owners WHERE boot_id = ?", (runs._BOOT_ID,)
        ).fetchone()[0]

    try:
        assert entered.wait(2)
        first = beat()
        deadline = time.monotonic() + 2
        while beat() == first and time.monotonic() < deadline:
            time.sleep(0.01)
        assert beat() > first
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while store.latest()["run"]["status"] in runs.ACTIVE:
        assert time.monotonic() < deadline, "import worker did not finish"
        time.sleep(0.01)
