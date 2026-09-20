"""The public evaluator view (OT_PUBLIC_EVALUATOR).

For the SIH evaluation the deployed site must open with no login, while a real
sign-in still demonstrates production RBAC. What is pinned here:

  * with the flag OFF (the default) nothing changes: no session means 401;
  * with the flag ON, a visitor with no session is a password-less evaluator
    that can read everything and run the investigation workflow;
  * the evaluator can NOT touch credentials, accounts, zone staffing or the
    live AIS worker -- a public URL must not be able to break the demo;
  * a real session always wins over the evaluator, and signing out returns to it;
  * the evaluator account cannot be signed into by password.

Runs against a temporary DATA_ROOT; the live database is never touched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

GOOD_PASSWORD = "correct horse battery staple"
EVALUATOR = "evaluator@oceantrace.public"


def _fresh_app(root: Path, public: bool):
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'test.db').as_posix()}"
    os.environ["SECRET_KEY"] = "x" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    os.environ.pop("OT_EVALUATOR_ROLE", None)
    os.environ.pop("OT_EVALUATOR_EMAIL", None)
    # Set explicitly either way: removing it would let a developer's .env
    # (loaded with setdefault) switch the mode back on for later modules.
    os.environ["OT_PUBLIC_EVALUATOR"] = "true" if public else "false"
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from backend.core import security
    from backend.main import app
    from backend.models.db import SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        db.add(User(email="analyst@example.invalid",
                    password_hash=security.hash_password(GOOD_PASSWORD),
                    display_name="Analyst", role="analyst", active=True))
        db.commit()
    return app


@pytest.fixture()
def private_client(tmp_path):
    from fastapi.testclient import TestClient

    app = _fresh_app(tmp_path, public=False)
    with TestClient(app, base_url="https://testserver") as client:
        yield client
    os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def public_client(tmp_path):
    from fastapi.testclient import TestClient

    app = _fresh_app(tmp_path, public=True)
    with TestClient(app, base_url="https://testserver") as client:
        yield client
    os.environ["OT_PUBLIC_EVALUATOR"] = "false"
    os.environ.pop("DATABASE_URL", None)


# --------------------------------------------------------------------------
# off by default
# --------------------------------------------------------------------------

def test_default_deployment_still_requires_a_session(private_client):
    assert private_client.get("/api/auth/me").status_code == 401
    assert private_client.get("/api/runs").status_code == 401
    assert private_client.get("/api/auth/mode").json()["public_evaluator"] is False


# --------------------------------------------------------------------------
# on
# --------------------------------------------------------------------------

def test_visitor_without_a_session_is_the_evaluator(public_client):
    me = public_client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == EVALUATOR
    assert body["evaluator"] is True
    assert body["role"] == "admin"
    assert public_client.get("/api/auth/mode").json()["public_evaluator"] is True


def test_evaluator_reads_every_surface(public_client):
    for path in ("/api/runs", "/api/investigations", "/api/incidents",
                 "/api/reports", "/api/zones", "/api/audit", "/api/users",
                 "/api/keys", "/api/catalog", "/api/models"):
        r = public_client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_evaluator_can_run_the_workflow(public_client):
    # Opening an incident is an investigator/analyst action.
    r = public_client.post("/api/incidents", json={"title": "evaluator case"})
    assert r.status_code == 201, r.text
    # ...and it is attributed to the evaluator, not to "anonymous".
    audit = public_client.get("/api/audit", params={"limit": 20}).json()["items"]
    assert any(a["actor"] == EVALUATOR for a in audit)


@pytest.mark.parametrize("method,path,body", [
    ("put", "/api/keys", {"provider": "CDSE", "field": "CDSE_USERNAME", "value": "x"}),
    ("post", "/api/users", {"email": "new@example.invalid", "password": "p" * 16,
                            "role": "analyst"}),
    ("patch", "/api/users/1", {"role": "admin"}),
    ("post", "/api/ais/stream/start", None),
    ("post", "/api/logs/clear", None),
    ("delete", "/api/zones/zone-01", None),
    ("post", "/api/zones/zone-01/assignments", {"user_id": 1}),
])
def test_evaluator_cannot_change_credentials_accounts_or_staffing(public_client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(public_client, method)(path, **kwargs)
    assert r.status_code == 403, (path, r.status_code)
    assert "public evaluator view" in r.json()["detail"]


def test_a_real_session_wins_and_signing_out_returns_to_the_evaluator(public_client):
    r = public_client.post("/api/auth/login",
                           json={"email": "analyst@example.invalid", "password": GOOD_PASSWORD})
    assert r.status_code == 200
    me = public_client.get("/api/auth/me").json()
    assert me["email"] == "analyst@example.invalid"
    assert me["evaluator"] is False
    # the analyst's own RBAC applies, not the evaluator's
    assert public_client.get("/api/audit").status_code == 403

    public_client.post("/api/auth/logout")
    assert public_client.get("/api/auth/me").json()["evaluator"] is True


def test_the_evaluator_account_cannot_be_signed_into(public_client):
    public_client.get("/api/auth/me")                     # creates the row
    r = public_client.post("/api/auth/login", json={"email": EVALUATOR, "password": "anything"})
    assert r.status_code == 401
