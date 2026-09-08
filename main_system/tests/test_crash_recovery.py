"""What the registry says after the process that was running a pipeline dies.

PROMPT 20. During acceptance an AOI poll started three `auto_run` pipelines on
top of one already executing; the metocean HDF5 readers are not thread-safe
and the backend died with a segmentation fault. Two things were then wrong,
and both are the kind of wrong that reads as fine:

* the three runs kept the status `running` forever -- the UI showed work in
  progress that did not exist;
* nothing had stopped four pipelines starting at once in the first place.

The fixes are small and these tests pin them: at boot every in-flight row is
dead by definition and is marked `failed` with the reason, and pipeline
execution is serialised through one gate so a fan-out queues instead of
crashing the process.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "runs").mkdir(parents=True)
    monkeypatch.setenv("DATA_ROOT", str(root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    from backend.models.db import init_db
    init_db()
    return root


def _row(status: str, run_id: str, *, sealed: bool = False, with_job: bool = True):
    from backend.models.db import Job, Run, SessionLocal
    from backend.core.config import get_settings

    with SessionLocal() as db:
        db.add(Run(id=run_id, status=status, registry_source="api"))
        if with_job:
            db.add(Job(id=f"job-{run_id}", run_id=run_id, status=status, inputs_json="{}"))
        db.commit()
    if sealed:
        d = get_settings().runs_root / run_id
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")


def _get(run_id):
    from backend.models.db import Job, Run, SessionLocal
    with SessionLocal() as db:
        return db.get(Run, run_id), db.get(Job, f"job-{run_id}")


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------

def test_a_running_row_with_no_manifest_is_marked_failed_at_boot(env):
    from backend.models.db import SessionLocal
    from backend.services import jobs

    _row("running", "aoi-danish-straits-dead")
    with SessionLocal() as db:
        result = jobs.sweep_dead_runs(db)

    assert result["swept"] == ["aoi-danish-straits-dead"]
    run, job = _get("aoi-danish-straits-dead")
    assert run.status == "failed"
    assert "server restarted" in run.error
    assert run.finished_utc is not None
    assert job.status == "failed" and "server restarted" in job.error


def test_a_pending_row_is_dead_too(env):
    """Queued behind the gate when the process died: never started, never will."""
    from backend.models.db import SessionLocal
    from backend.services import jobs

    _row("pending", "inv-queued")
    with SessionLocal() as db:
        jobs.sweep_dead_runs(db)
    assert _get("inv-queued")[0].status == "failed"


def test_finished_rows_are_left_alone(env):
    from backend.models.db import SessionLocal
    from backend.services import jobs

    for status in ("complete", "failed", "cancelled"):
        _row(status, f"inv-{status}")
    with SessionLocal() as db:
        result = jobs.sweep_dead_runs(db)
    assert result["swept"] == []
    for status in ("complete", "failed", "cancelled"):
        assert _get(f"inv-{status}")[0].status == status


def test_a_sealed_run_is_not_declared_failed(env):
    """The manifest exists, so the pipeline finished; the process died before
    the row was updated. That is a reconciliation case, not a failure, and the
    sweep must say so rather than contradict the artefacts."""
    from backend.models.db import SessionLocal
    from backend.services import jobs

    _row("running", "inv-sealed-late", sealed=True)
    with SessionLocal() as db:
        result = jobs.sweep_dead_runs(db)
    assert result["sealed_but_unmarked"] == ["inv-sealed-late"]
    assert result["swept"] == []
    assert _get("inv-sealed-late")[0].status == "running"   # untouched, reported


def test_a_row_without_a_job_row_is_still_swept(env):
    """Scheduler-started runs have no jobs row. They die like any other."""
    from backend.models.db import SessionLocal
    from backend.services import jobs

    _row("running", "aoi-no-job", with_job=False)
    with SessionLocal() as db:
        jobs.sweep_dead_runs(db)
    run, job = _get("aoi-no-job")
    assert run.status == "failed" and job is None


def test_the_sweep_runs_at_startup(env):
    """The lifespan hook is where it has to live: a sweep nobody calls is a
    sweep that did not happen."""
    from fastapi.testclient import TestClient

    _row("running", "inv-died-before-boot")
    from backend.main import app
    with TestClient(app, base_url="https://testserver"):
        pass
    assert _get("inv-died-before-boot")[0].status == "failed"


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def test_pipelines_do_not_overlap(env, monkeypatch):
    """Two launches must execute one after the other, whatever thread started
    them. The scheduler's fan-out is exactly this shape."""
    from backend.api import routes

    active, peak, order = [0], [0], []
    lock = threading.Lock()

    def fake_execute(run_id, *a, **k):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            order.append(("start", run_id))
        time.sleep(0.15)
        with lock:
            active[0] -= 1
            order.append(("end", run_id))

    monkeypatch.setattr(routes, "_execute_run_now", fake_execute)
    threads = [threading.Thread(target=routes._execute_run, args=(f"r{i}", None, None, None, "auto"))
               for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)

    assert peak[0] == 1, f"pipelines overlapped: peak concurrency {peak[0]}"
    assert len(order) == 6


def test_the_gate_width_is_configurable_but_never_zero(monkeypatch):
    monkeypatch.setenv("OT_MAX_CONCURRENT_RUNS", "0")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    from backend.api import routes
    assert routes._pipeline_gate._initial_value == 1
