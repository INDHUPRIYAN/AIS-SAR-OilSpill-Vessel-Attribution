"""Shared helpers for the main-system API tests.

Since PROMPT-07 every `/api/*` route requires a session, so a test that wants
to exercise a handler has to sign in first. `authenticated_client` does that:
it seeds one admin account in whatever database the caller's fixture set up,
then logs in and returns the client with the session cookie held.

Deliberately no test-only bypass. An escape hatch that skips the guard would
mean the suite exercises a configuration nobody deploys, which is exactly the
class of bug PROMPT-03 was about -- tests passing against an environment the
real system never runs in.

Note the https base URL: the session cookie is `Secure`, and a client on
http:// silently discards it, so the request that follows looks anonymous.
"""
from __future__ import annotations

import os

import pytest

# The public evaluator view (OT_PUBLIC_EVALUATOR) turns "no session" into a
# signed-in evaluator. A developer's .env may switch it on for a demo, and
# config only fills variables that are not already set -- so pin it OFF here,
# before any backend import, or every "requires a session" test would pass or
# fail depending on the machine. test_public_evaluator.py turns it on locally.
os.environ["OT_PUBLIC_EVALUATOR"] = "false"

TEST_ADMIN_EMAIL = "test-admin@example.invalid"
TEST_ADMIN_PASSWORD = "suite-fixture-password"


def _refuse_live_database() -> None:
    """Stop a test account from ever reaching the production database.

    This already happened once: two suites use the real DATA_ROOT on purpose
    (they read real artefacts), and when PROMPT-07 made every route require a
    session, the seeding below quietly created `test-admin@example.invalid`
    with a known password in `data/oceantrace.db`. Chasing which module did it
    fixes one case; refusing outright fixes the class.
    """
    from pathlib import Path

    from backend.core.config import get_settings

    url = get_settings().database_url
    if not url.startswith("sqlite"):
        return
    resolved = Path(url.split("///", 1)[1]).resolve()
    canonical = (Path(__file__).resolve().parents[2] / "data" / "oceantrace.db").resolve()
    if resolved == canonical:
        raise RuntimeError(
            f"refusing to seed a test account into the live database "
            f"({resolved}). Point DATABASE_URL at a temp file in this "
            f"module's fixture; DATA_ROOT may stay real if the test needs "
            f"real artefacts.")


def seed_admin(email: str = TEST_ADMIN_EMAIL,
               password: str = TEST_ADMIN_PASSWORD,
               role: str = "admin") -> None:
    """Ensure one account exists in the currently-configured database."""
    from backend.core import security
    from backend.models.db import SessionLocal, User, init_db

    _refuse_live_database()
    init_db()
    with SessionLocal() as db:
        if db.query(User).filter(User.email == email).one_or_none() is None:
            db.add(User(email=email, password_hash=security.hash_password(password),
                        display_name="Suite Admin", role=role, active=True))
            db.commit()


def sign_in(client, email: str = TEST_ADMIN_EMAIL,
            password: str = TEST_ADMIN_PASSWORD) -> None:
    """Log `client` in, seeding the account if it is not there yet."""
    seed_admin(email, password)
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"fixture login failed: {r.status_code} {r.text[:200]}"


def authenticated_client(app, role: str = "admin", base_url: str = "https://testserver"):
    """A TestClient with a live session. Use as a context manager."""
    from fastapi.testclient import TestClient

    email = f"test-{role}@example.invalid"
    client = TestClient(app, base_url=base_url)
    seed_admin(email, TEST_ADMIN_PASSWORD, role=role)
    r = client.post("/api/auth/login",
                    json={"email": email, "password": TEST_ADMIN_PASSWORD})
    assert r.status_code == 200, f"fixture login failed: {r.status_code} {r.text[:200]}"
    return client


# Exposed as fixtures rather than imported by name: `pytest.ini` uses importlib
# mode and several module roots ship their own `conftest.py`, so a bare
# `from conftest import sign_in` resolves to whichever one loaded first.
# Fixtures are looked up per-directory, so they cannot collide.

@pytest.fixture(scope="session")
def sign_in_helper():
    return sign_in


@pytest.fixture(scope="session")
def seed_admin_helper():
    return seed_admin
