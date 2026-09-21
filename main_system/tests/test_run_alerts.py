"""An alert when a run ends.

A run executes on a worker thread and takes minutes; whoever started it has
usually navigated away. Until this existed nothing told them it had finished --
the `Alert` model's own comment listed `run_failed` as a kind, and it was never
raised. Approved backend exception G3 of the UX programme.

What these tests hold:

* every failure raises, whoever started it -- a scheduled run dying overnight
  is exactly what nobody is watching for;
* a clean completion raises only for a run a PERSON started, and not when the
  run has just opened an incident (that already raised a `detection` alert
  about the same run) -- two rows for one event is noise, and a queue of noise
  is a queue nobody reads;
* a cancelled run raises nothing: the operator did that and knows;
* raising is idempotent per run and kind, and never fails the run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("run_alerts_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'alerts.db').as_posix()}"
    os.environ["SECRET_KEY"] = "k" * 64
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from backend.models.db import init_db

    init_db()
    yield root
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATA_ROOT", None)


def _run(db, run_id: str, **kw):
    from backend.models.db import Run

    row = Run(id=run_id, investigation_id=kw.pop("investigation_id", "inv-1"),
              scene_id=kw.pop("scene_id", "S1A_TEST"), registry_source="api", **kw)
    db.add(row)
    db.commit()
    return row


def _alerts(db, run_id):
    from backend.models.db import Alert

    return db.query(Alert).filter(Alert.run_id == run_id).all()


def test_a_failed_run_raises_a_warning_carrying_its_error(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-fail", status="failed", error="BadGrid: currents do not cover the slick")
        alert = run_alerts.alert_run_outcome(db, "run-fail")
        db.commit()

        assert alert is not None
        assert alert.kind == "run_failed"
        assert alert.severity == "warning"
        assert alert.status == "open"
        assert "run-fail" in alert.title
        assert "BadGrid" in alert.detail
        assert alert.run_id == "run-fail" and alert.investigation_id == "inv-1"


def test_a_failure_raises_even_when_the_scheduler_started_it(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-fail-sched", status="failed", error="boom")
        assert run_alerts.alert_run_outcome(db, "run-fail-sched",
                                            started_by_person=False) is not None


def test_a_completed_run_a_person_started_raises_an_info_alert(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-ok", status="complete", stages_total=5, stages_real=5,
             stages_failed=0, seconds=42.5)
        alert = run_alerts.alert_run_outcome(db, "run-ok")
        db.commit()

        assert alert.kind == "run_complete"
        assert alert.severity == "info"
        assert "5 of 5" in alert.detail
        assert "42.5 s" in alert.detail


def test_a_completion_with_failed_stages_is_a_warning_that_says_so(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-partial", status="complete", stages_total=5, stages_real=3,
             stages_failed=2)
        alert = run_alerts.alert_run_outcome(db, "run-partial")
        assert alert.severity == "warning"
        assert "2 failed stage(s)" in alert.title


@pytest.mark.parametrize("kwargs", [
    {"started_by_person": False},     # the watcher already raised new_scene
    {"opened_incident": True},        # incident_auto already raised `detection`
])
def test_a_completion_stays_quiet_when_something_else_already_spoke(env, kwargs):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        run_id = f"run-quiet-{'sched' if 'started_by_person' in kwargs else 'inc'}"
        _run(db, run_id, status="complete", stages_total=5, stages_real=5)
        assert run_alerts.alert_run_outcome(db, run_id, **kwargs) is None
        assert _alerts(db, run_id) == []


def test_a_cancelled_run_raises_nothing(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-cancelled", status="cancelled", error="cancelled by operator")
        assert run_alerts.alert_run_outcome(db, "run-cancelled") is None
        assert _alerts(db, "run-cancelled") == []


def test_raising_twice_does_not_raise_twice(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-twice", status="complete", stages_total=5, stages_real=5)
        assert run_alerts.alert_run_outcome(db, "run-twice") is not None
        db.commit()
        assert run_alerts.alert_run_outcome(db, "run-twice") is None
        assert len(_alerts(db, "run-twice")) == 1


def test_an_unknown_run_is_not_an_error(env):
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        assert run_alerts.alert_run_outcome(db, "run-does-not-exist") is None


def test_the_new_kinds_reach_the_queue_and_its_summary(env):
    """The alert feed and the bell count are what the user actually sees."""
    from backend.models.db import SessionLocal
    from backend.services import run_alerts

    with SessionLocal() as db:
        _run(db, "run-feed", status="failed", error="boom")
        run_alerts.alert_run_outcome(db, "run-feed")
        db.commit()

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import SessionLocal as S, User

    with S() as db:
        if not db.query(User).filter(User.email == "ra-admin@example.invalid").first():
            db.add(User(email="ra-admin@example.invalid",
                        password_hash=security.hash_password("run-alerts-password"),
                        display_name="admin", role="admin", active=True))
            db.commit()

    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/api/auth/login", json={
            "email": "ra-admin@example.invalid",
            "password": "run-alerts-password"}).status_code == 200
        feed = client.get("/api/alerts?status=open").json()
        kinds = {a["kind"] for a in feed["alerts"]}
        assert "run_failed" in kinds
        summary = client.get("/api/alerts/summary").json()
        assert summary["open"] >= 1
