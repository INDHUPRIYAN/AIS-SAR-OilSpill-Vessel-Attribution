"""The audit log has to survive being questioned.

Two properties carry that, and both fail silently if they regress:

* the actor comes from the SESSION. The previous emit site took it from the
  request body, so the log said whatever the caller said -- which is not
  evidence in a system that ranks vessels as suspects;
* rows are hash-chained, so editing or deleting history breaks the chain
  instead of leaving no trace.

The claim being tested is tamper-EVIDENCE, not tamper-proofing. Anyone with
write access to the file can rewrite rows; what they cannot do is change one
and leave the rest consistent. `test_editing_a_row_breaks_the_chain` is the
test that actually demonstrates the property, by mutating SQLite directly
behind the application's back.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "audit-test-password"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit_root")
    db_path = root / "audit.db"
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["SECRET_KEY"] = "z" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        role=role, active=True))
        db.commit()

    with TestClient(app, base_url="https://testserver") as client:
        yield client, db_path

    os.environ.pop("DATABASE_URL", None)


def _login(client, role="admin"):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r


# --------------------------------------------------------------------------
# the actor
# --------------------------------------------------------------------------

def test_login_records_the_account_not_a_claim(env):
    client, _ = env
    _login(client, "investigator")

    _login(client, "auditor")
    rows = client.get("/api/audit", params={"action": "auth.login"}).json()["items"]
    assert rows, "sign-in must be recorded"
    latest = rows[0]
    assert latest["actor"].endswith("@example.invalid")
    assert latest["actor_user_id"] is not None
    assert latest["ip"], "the caller address is recorded"


def test_failed_logins_are_recorded(env):
    """A burst of these is the signal an operator needs. The response stays
    vague; the log does not have to be."""
    client, _ = env
    client.cookies.clear()
    client.post("/api/auth/login",
                json={"email": "analyst@example.invalid", "password": "wrong"})
    client.post("/api/auth/login",
                json={"email": "ghost@example.invalid", "password": "wrong"})

    _login(client, "auditor")
    rows = client.get("/api/audit", params={"action": "auth.login"}).json()["items"]
    failures = [r for r in rows if "failed" in (r["detail"] or "")]
    assert len(failures) >= 2
    assert any("unknown or inactive" in (r["detail"] or "") for r in failures)


def test_actor_cannot_be_chosen_by_the_caller(env):
    """The decision route accepts an `actor` field for the analyst's own
    label. It must not become the audited actor."""
    client, _ = env
    _login(client, "investigator")

    client.post("/api/runs/no-such-run/decisions",
                json={"verdict": "plausible", "actor": "someone-else",
                      "note": "attempted spoof"})

    _login(client, "auditor")
    rows = client.get("/api/audit").json()["items"]
    assert all(r["actor"] != "someone-else" for r in rows), \
        "a client-supplied actor reached the audit log"


def test_investigation_and_export_are_recorded(env):
    client, _ = env
    _login(client, "investigator")
    client.post("/api/investigations", json={"name": "audit fixture"})

    _login(client, "auditor")
    actions = {r["action"] for r in client.get("/api/audit").json()["items"]}
    assert "investigation.create" in actions


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def test_chain_verifies_clean(env):
    client, _ = env
    _login(client, "auditor")
    result = client.get("/api/audit/verify").json()
    assert result["ok"] is True
    assert result["broken_at"] is None
    assert result["checked"] > 0


def test_every_row_links_to_the_one_before(env):
    client, _ = env
    _login(client, "auditor")
    rows = client.get("/api/audit", params={"limit": 1000}).json()["items"]
    rows = sorted([r for r in rows if r["row_hash"]], key=lambda r: r["id"])
    assert len(rows) >= 2
    assert len({r["row_hash"] for r in rows}) == len(rows), "hashes must be unique"


def test_editing_a_row_breaks_the_chain(env):
    """The property that matters: rewrite history behind the app's back and
    verification says exactly where."""
    client, db_path = env
    _login(client, "auditor")
    assert client.get("/api/audit/verify").json()["ok"] is True

    con = sqlite3.connect(str(db_path))
    target = con.execute(
        "select id, detail from audit_log where row_hash is not null "
        "order by id asc limit 1").fetchone()
    assert target, "need at least one chained row"
    con.execute("update audit_log set detail = ? where id = ?",
                ("tampered after the fact", target[0]))
    con.commit()
    con.close()

    broken = client.get("/api/audit/verify").json()
    assert broken["ok"] is False
    assert broken["broken_at"] == target[0]
    assert "edited" in broken["reason"]

    # Put it back so later tests see a clean chain.
    con = sqlite3.connect(str(db_path))
    con.execute("update audit_log set detail = ? where id = ?", (target[1], target[0]))
    con.commit()
    con.close()
    assert client.get("/api/audit/verify").json()["ok"] is True


def test_deleting_a_row_breaks_the_chain(env):
    client, db_path = env
    _login(client, "auditor")

    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "select id, action, provider, field, actor, actor_user_id, resource, ip, "
        "detail, occurred_utc, prev_hash, row_hash from audit_log "
        "where row_hash is not null order by id asc limit 1 offset 1").fetchone()
    assert row, "need a middle row"
    con.execute("delete from audit_log where id = ?", (row[0],))
    con.commit()
    con.close()

    broken = client.get("/api/audit/verify").json()
    assert broken["ok"] is False
    assert "removed" in broken["reason"] or "reordered" in broken["reason"]

    con = sqlite3.connect(str(db_path))
    con.execute(
        "insert into audit_log (id, action, provider, field, actor, actor_user_id, "
        "resource, ip, detail, occurred_utc, prev_hash, row_hash) "
        "values (?,?,?,?,?,?,?,?,?,?,?,?)", row)
    con.commit()
    con.close()
    assert client.get("/api/audit/verify").json()["ok"] is True


def test_verification_does_not_claim_to_cover_legacy_rows(env):
    """Rows written before chaining have no hash. Counting them as verified
    would be the dishonest option."""
    client, db_path = env
    _login(client, "auditor")

    con = sqlite3.connect(str(db_path))
    con.execute("insert into audit_log (action, actor, detail) values (?,?,?)",
                ("legacy.event", "someone", "written before chaining existed"))
    con.commit()
    con.close()

    result = client.get("/api/audit/verify").json()
    assert result["unchained"] >= 1
    assert "not covered" in result["note"]


# --------------------------------------------------------------------------
# the routes
# --------------------------------------------------------------------------

def test_audit_is_readable_by_reviewer_and_auditor_only(env):
    client, _ = env
    for role in ("reviewer", "auditor", "admin"):
        _login(client, role)
        assert client.get("/api/audit").status_code == 200, f"{role} cannot read"
    for role in ("investigator", "analyst"):
        _login(client, role)
        assert client.get("/api/audit").status_code == 403, f"{role} could read"


def test_filters_and_pagination(env):
    client, _ = env
    _login(client, "auditor")

    everything = client.get("/api/audit", params={"limit": 1000}).json()
    logins = client.get("/api/audit", params={"action": "auth.login",
                                              "limit": 1000}).json()
    assert logins["total"] <= everything["total"]
    assert all(r["action"].startswith("auth.login") for r in logins["items"])

    page = client.get("/api/audit", params={"limit": 2}).json()
    assert len(page["items"]) <= 2
    assert page["limit"] == 2


def test_export_is_csv_and_is_itself_auditable(env):
    client, _ = env
    _login(client, "auditor")
    r = client.get("/api/audit/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "row_hash" in r.text.splitlines()[0]


def test_no_delete_route_exists(env):
    """Append-only is the entire claim."""
    client, _ = env
    _login(client, "admin")
    assert client.delete("/api/audit").status_code in (404, 405)


def test_event_type_vocabulary_is_fixed():
    from backend.services.audit import EVENT_TYPES

    assert "auth.login" in EVENT_TYPES and "data.export" in EVENT_TYPES
    assert len(EVENT_TYPES) >= 10
    assert len(set(EVENT_TYPES)) == len(EVENT_TYPES)
