"""Alerts: the queue that turns a watcher into a notification.

PROMPT 15. The watcher already opened investigations by itself; what it could
not do was tell anyone. An investigation appearing in a list is not a
notification, because nobody watches a list.

What these defend:

* **dismissal requires a reason.** A queue that clears with one unexplained
  click becomes a queue people clear rather than read, and the record of why
  nobody acted disappears with it;
* **assignment requires a real user.** Assigning to nobody looks handled and is
  not, which is worse than untouched;
* **deduplication.** A watcher polling hourly must not raise the same scene
  sixty times: a queue full of duplicates is a queue nobody reads;
* **SLA age is computed, never stored.** A stored age is wrong the moment it is
  written.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture(scope="module")
def client(sign_in_helper, tmp_path_factory):
    root = tmp_path_factory.mktemp("alerts")
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'a.db').as_posix()}"
    os.environ["DATA_ROOT"] = str(root)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        yield c
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATA_ROOT", None)


@pytest.fixture
def an_alert(client):
    from backend.api.alerts import raise_alert
    from backend.models.db import SessionLocal

    with SessionLocal() as db:
        alert = raise_alert(db, kind="new_scene", title="New pass over Gulf",
                            severity="info", scene_id=f"S1A_{os.urandom(4).hex()}",
                            aoi_id="gulf")
        db.commit()
        return alert.id


# --------------------------------------------------------------------------
# creation and dedupe
# --------------------------------------------------------------------------

def test_an_alert_is_raised_and_appears_in_the_feed(client, an_alert):
    body = client.get("/api/alerts").json()
    ids = {a["id"] for a in body["alerts"]}
    assert an_alert in ids
    row = next(a for a in body["alerts"] if a["id"] == an_alert)
    assert row["status"] == "open"
    assert row["kind"] == "new_scene"


def test_the_same_scene_is_not_raised_twice_while_open(client):
    """A watcher polling hourly must not fill the queue with one scene."""
    from backend.api.alerts import raise_alert
    from backend.models.db import SessionLocal

    with SessionLocal() as db:
        first = raise_alert(db, kind="new_scene", title="pass", scene_id="S1A_DUPE")
        db.commit()
        second = raise_alert(db, kind="new_scene", title="pass again",
                             scene_id="S1A_DUPE")
        db.commit()

    assert first is not None
    assert second is None, "the same scene was raised twice while still open"


def test_a_dismissed_scene_can_be_raised_again(client):
    """Dedupe is scoped to OPEN alerts: a new pass after one was closed is
    genuinely new information."""
    from backend.api.alerts import raise_alert
    from backend.models.db import Alert, SessionLocal

    with SessionLocal() as db:
        first = raise_alert(db, kind="new_scene", title="pass", scene_id="S1A_REDO")
        db.commit()
        row = db.get(Alert, first.id)
        row.status = "dismissed"
        row.dismiss_reason = "handled offline"
        db.commit()
        again = raise_alert(db, kind="new_scene", title="pass", scene_id="S1A_REDO")
        db.commit()

    assert again is not None


def test_age_is_computed_not_stored(client, an_alert):
    body = client.get("/api/alerts").json()
    row = next(a for a in body["alerts"] if a["id"] == an_alert)
    assert "age_seconds" in row
    assert row["age_seconds"] >= 0
    from backend.models.db import Alert, SessionLocal

    with SessionLocal() as db:
        assert not hasattr(db.get(Alert, an_alert), "age_seconds"), \
            "age is a stored column; it would be wrong the moment it was written"


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def test_acknowledge_is_recorded_and_audited(client, an_alert):
    r = client.post(f"/api/alerts/{an_alert}/ack")
    assert r.status_code == 200
    assert r.json()["status"] == "acknowledged"
    assert r.json()["acknowledged_utc"]

    from backend.models.db import AuditLog, SessionLocal

    with SessionLocal() as db:
        entry = (db.query(AuditLog).filter(AuditLog.resource == an_alert,
                                           AuditLog.action == "alert.ack")
                 .first())
        assert entry is not None, "acknowledging was not audited"


def test_dismiss_requires_a_reason(client, an_alert):
    """The whole point: an alert cleared without a reason erases the record of
    why nobody acted."""
    assert client.post(f"/api/alerts/{an_alert}/dismiss",
                       json={}).status_code == 422
    assert client.post(f"/api/alerts/{an_alert}/dismiss",
                       json={"reason": "x"}).status_code == 422


def test_dismiss_with_a_reason_records_it(client, an_alert):
    r = client.post(f"/api/alerts/{an_alert}/dismiss",
                    json={"reason": "look-alike; wind shadow over a rig field"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "dismissed"
    assert "wind shadow" in body["dismiss_reason"]

    from backend.models.db import AuditLog, SessionLocal

    with SessionLocal() as db:
        entry = (db.query(AuditLog)
                 .filter(AuditLog.resource == an_alert,
                         AuditLog.action == "alert.dismiss").first())
        assert entry is not None
        assert "wind shadow" in entry.detail


def test_dismissing_twice_is_refused(client, an_alert):
    client.post(f"/api/alerts/{an_alert}/dismiss", json={"reason": "first close"})
    again = client.post(f"/api/alerts/{an_alert}/dismiss",
                        json={"reason": "second close"})
    assert again.status_code == 409


def test_assign_requires_a_real_user(client, an_alert):
    """Assigning to nobody looks handled and is not."""
    r = client.post(f"/api/alerts/{an_alert}/assign", json={"user_id": 99999})
    assert r.status_code == 400
    assert "no user" in r.json()["detail"]


def test_assign_to_a_real_user_records_it(client, an_alert):
    from backend.models.db import SessionLocal, User

    with SessionLocal() as db:
        user = db.query(User).first()
        assert user is not None
        user_id = user.id

    r = client.post(f"/api/alerts/{an_alert}/assign", json={"user_id": user_id})
    assert r.status_code == 200
    assert r.json()["status"] == "assigned"
    assert r.json()["assigned_to"] == user_id


def test_a_dismissed_alert_cannot_be_acknowledged(client, an_alert):
    client.post(f"/api/alerts/{an_alert}/dismiss", json={"reason": "closed out"})
    assert client.post(f"/api/alerts/{an_alert}/ack").status_code == 409


# --------------------------------------------------------------------------
# feed shape
# --------------------------------------------------------------------------

def test_the_summary_counts_only_open_alerts(client):
    from backend.api.alerts import raise_alert
    from backend.models.db import SessionLocal

    with SessionLocal() as db:
        raise_alert(db, kind="detection", title="oil candidates",
                    severity="critical", scene_id="S1A_SUMMARY")
        db.commit()

    summary = client.get("/api/alerts/summary").json()
    assert summary["open"] >= 1
    assert summary["by_severity"].get("critical", 0) >= 1
    assert summary["oldest_age_seconds"] >= 0


def test_critical_alerts_sort_above_info(client):
    from backend.api.alerts import raise_alert
    from backend.models.db import SessionLocal

    with SessionLocal() as db:
        raise_alert(db, kind="detection", title="critical one",
                    severity="critical", scene_id="S1A_SORT_C")
        raise_alert(db, kind="new_scene", title="info one",
                    severity="info", scene_id="S1A_SORT_I")
        db.commit()

    alerts = client.get("/api/alerts").json()["alerts"]
    severities = [a["severity"] for a in alerts]
    if "critical" in severities and "info" in severities:
        assert severities.index("critical") < severities.index("info")


def test_the_feed_can_be_filtered_by_status(client):
    body = client.get("/api/alerts", params={"status": "dismissed"}).json()
    for alert in body["alerts"]:
        assert alert["status"] == "dismissed"


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------

def test_sse_sends_the_current_queue_on_connect():
    """A browser connecting mid-shift must see what is waiting, not only what
    arrives next.

    The generator is driven directly rather than through TestClient. An SSE
    endpoint is an infinite loop by design: consuming it over HTTP in a test
    means relying on client-side cancellation to end a stream that is built
    never to end, and a test that can hang the suite is worse than no test.
    Driving the async generator lets this assert the first frames and stop.
    """
    import asyncio

    from backend.api import alerts as alerts_api
    from backend.api.alerts import raise_alert
    from backend.models.db import SessionLocal

    with SessionLocal() as db:
        raise_alert(db, kind="new_scene", title="waiting already",
                    scene_id="S1A_SSE_EXISTING")
        db.commit()

    class _Request:
        async def is_disconnected(self):
            return True          # stop after the initial queue is drained

    async def _drain():
        frames = []
        async for frame in alerts_api._alert_stream(_Request()):
            frames.append(frame)
            if len(frames) > 60:
                break
        return frames

    frames = asyncio.run(_drain())
    body = "".join(frames)
    assert "event: alert" in body, "the current queue was not sent on connect"
    assert "waiting already" in body
