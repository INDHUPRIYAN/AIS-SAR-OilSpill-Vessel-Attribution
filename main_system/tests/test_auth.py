"""Sessions, password storage, and the guarded migration.

The output of this system names vessels as suspects, so an action has to be
attributable to a person. Three properties carry that, and each has a test
here because each fails silently if it regresses:

  * the token is in an HttpOnly cookie, so a scripting bug cannot read it;
  * the login response is identical for "no such account" and "wrong
    password", so the form is not an account-enumeration oracle;
  * a deactivated account stops working immediately, not at token expiry.

Every test runs against a temporary DATA_ROOT, so the live database with its
138 investigations is never touched.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

GOOD_PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    """A throwaway database with one user per role."""
    root = tmp_path_factory.mktemp("auth_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'test.db').as_posix()}"
    os.environ["SECRET_KEY"] = "x" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db

    # The app creates the schema at startup, but the seed below runs first.
    init_db()

    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(GOOD_PASSWORD),
                        display_name=role.title(), role=role, active=True))
        db.add(User(email="retired@example.invalid",
                    password_hash=security.hash_password(GOOD_PASSWORD),
                    display_name="Retired", role="analyst", active=False))
        db.commit()

    # https:// so the client actually stores the Secure cookie. Testing over
    # http would force SESSION_COOKIE_SECURE off and quietly exercise a
    # weaker configuration than the one that ships.
    with TestClient(app, base_url="https://testserver") as client:
        yield client

    os.environ.pop("DATABASE_URL", None)


def _login(client, email: str, password: str = GOOD_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --------------------------------------------------------------------------
# password storage
# --------------------------------------------------------------------------

def test_passwords_are_argon2id(app_env):
    from backend.core import security

    h = security.hash_password(GOOD_PASSWORD)
    assert h.startswith("$argon2id$"), "argon2id is the specified scheme"
    assert GOOD_PASSWORD not in h
    assert security.verify_password(h, GOOD_PASSWORD) is True
    assert security.verify_password(h, "wrong") is False


def test_password_hashing_never_degrades_silently(app_env):
    """The credential vault may fall back to a visible `plain:` marker because
    an unreadable provider key breaks the demo. A password has no such excuse:
    a silent downgrade would be invisible to everyone."""
    from backend.core import security

    with pytest.raises(ValueError):
        security.hash_password("short")
    # An absent hash is not an authentication bypass.
    assert security.verify_password(None, GOOD_PASSWORD) is False
    assert security.verify_password("", GOOD_PASSWORD) is False
    assert security.verify_password("$argon2id$garbage", GOOD_PASSWORD) is False


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def test_login_me_logout_lifecycle(app_env):
    assert app_env.get("/api/auth/me").status_code == 401

    r = _login(app_env, "investigator@example.invalid")
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "investigator"
    assert "password" not in r.text.lower()

    me = app_env.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "investigator@example.invalid"

    assert app_env.post("/api/auth/logout").status_code == 204
    assert app_env.get("/api/auth/me").status_code == 401


def test_logout_is_safe_without_a_session(app_env):
    app_env.cookies.clear()
    assert app_env.post("/api/auth/logout").status_code == 204


def test_wrong_password_and_unknown_account_are_indistinguishable(app_env):
    app_env.cookies.clear()
    wrong = _login(app_env, "analyst@example.invalid", "not the password")
    missing = _login(app_env, "nobody@example.invalid", GOOD_PASSWORD)

    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json(), "the response must not reveal which failed"
    assert "invalid email or password" in wrong.json()["detail"]


def test_deactivated_account_cannot_sign_in(app_env):
    app_env.cookies.clear()
    r = _login(app_env, "retired@example.invalid")
    assert r.status_code == 401


def test_email_is_case_and_space_insensitive(app_env):
    app_env.cookies.clear()
    r = _login(app_env, "  Reviewer@Example.Invalid  ")
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "reviewer"
    app_env.post("/api/auth/logout")


# --------------------------------------------------------------------------
# the cookie
# --------------------------------------------------------------------------

def test_session_cookie_flags(app_env):
    app_env.cookies.clear()
    r = _login(app_env, "admin@example.invalid")
    raw = r.headers["set-cookie"].lower()

    assert "httponly" in raw, "a readable token defeats the point of not using localStorage"
    assert "samesite=lax" in raw, "Lax is what makes mutating routes CSRF-safe here"
    assert "secure" in raw
    assert "max-age=28800" in raw, "8 hour session per the production spec"
    app_env.post("/api/auth/logout")


def test_token_is_not_returned_in_the_body(app_env):
    """If the body carried the token, a client could store it somewhere
    readable and the HttpOnly cookie would be pointless."""
    app_env.cookies.clear()
    body = _login(app_env, "auditor@example.invalid").json()
    assert "token" not in body and "access_token" not in body
    assert set(body) == {"user"}
    app_env.post("/api/auth/logout")


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------

def test_expired_token_is_rejected(app_env):
    import jwt

    from backend.core.authz import decode_token
    from backend.core.config import get_settings

    s = get_settings()
    expired = jwt.encode(
        {"sub": "1", "email": "admin@example.invalid", "role": "admin",
         "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())},
        s.jwt_secret, algorithm=s.jwt_algorithm)

    assert decode_token(expired) is None
    app_env.cookies.clear()
    app_env.cookies.set(s.session_cookie, expired)
    assert app_env.get("/api/auth/me").status_code == 401
    app_env.cookies.clear()


def test_tampered_token_is_rejected(app_env):
    import jwt

    from backend.core.authz import decode_token
    from backend.core.config import get_settings

    s = get_settings()
    forged = jwt.encode({"sub": "1", "role": "admin",
                         "exp": int((datetime.now(timezone.utc)
                                     + timedelta(hours=1)).timestamp())},
                        "a-different-secret-that-is-long-enough-to-sign", algorithm="HS256")
    assert decode_token(forged) is None

    app_env.cookies.clear()
    app_env.cookies.set(s.session_cookie, forged)
    assert app_env.get("/api/auth/me").status_code == 401
    app_env.cookies.clear()


def test_a_short_signing_secret_is_refused_not_downgraded():
    """Signing with a weak key would produce tokens that look fine."""
    from backend.core import authz

    original = authz.settings.jwt_secret
    try:
        authz.settings.jwt_secret = "tooshort"
        with pytest.raises(authz.SessionConfigError):
            authz._secret()
    finally:
        authz.settings.jwt_secret = original


def test_role_change_takes_effect_without_waiting_for_expiry(app_env):
    """The token embeds a role, but the database is authoritative on every
    request -- otherwise a demotion would not apply for up to 8 hours."""
    from backend.models.db import SessionLocal, User

    app_env.cookies.clear()
    assert _login(app_env, "analyst@example.invalid").status_code == 200
    assert app_env.get("/api/auth/me").json()["role"] == "analyst"

    with SessionLocal() as db:
        row = db.query(User).filter(User.email == "analyst@example.invalid").one()
        row.active = False
        db.commit()
    try:
        assert app_env.get("/api/auth/me").status_code == 401, \
            "deactivating an account must end its live session"
    finally:
        with SessionLocal() as db:
            row = db.query(User).filter(User.email == "analyst@example.invalid").one()
            row.active = True
            db.commit()
        app_env.cookies.clear()


# --------------------------------------------------------------------------
# role guard
# --------------------------------------------------------------------------

def test_require_role_rejects_unknown_role_names(app_env):
    from backend.core.authz import require_role

    with pytest.raises(ValueError):
        require_role("superuser")
    assert require_role("reviewer") is not None


def test_admin_satisfies_every_role_guard(app_env):
    """Listing admin at every call site is how one eventually omits it."""
    from backend.core.authz import require_role
    from backend.models.db import SessionLocal, User

    guard = require_role("reviewer")
    with SessionLocal() as db:
        admin = db.query(User).filter(User.email == "admin@example.invalid").one()
        investigator = db.query(User).filter(
            User.email == "investigator@example.invalid").one()

    assert guard(admin) is admin
    with pytest.raises(Exception) as exc:
        guard(investigator)
    assert getattr(exc.value, "status_code", None) == 403


# --------------------------------------------------------------------------
# bootstrap
# --------------------------------------------------------------------------

def test_bootstrap_does_nothing_without_credentials(app_env):
    from backend.api.auth import bootstrap_admin

    assert bootstrap_admin() is None, "a checkout with no env vars gets no account"


def test_bootstrap_never_overwrites_an_existing_account(app_env):
    """Otherwise anyone able to set an env var could reset an operator's
    password by restarting the process."""
    from backend.api import auth as auth_mod
    from backend.models.db import SessionLocal, User

    with SessionLocal() as db:
        before = db.query(User).filter(User.email == "admin@example.invalid").one()
        original_hash = before.password_hash

    auth_mod.settings.admin_email = "admin@example.invalid"
    auth_mod.settings.admin_password = "a-completely-different-password"
    try:
        note = auth_mod.bootstrap_admin()
        assert "left unchanged" in note or "already exist" in note
        with SessionLocal() as db:
            after = db.query(User).filter(User.email == "admin@example.invalid").one()
            assert after.password_hash == original_hash
    finally:
        auth_mod.settings.admin_email = ""
        auth_mod.settings.admin_password = ""


# --------------------------------------------------------------------------
# the guarded migration (H3)
# --------------------------------------------------------------------------

def test_migration_refuses_a_database_with_no_runs(tmp_path):
    """Two databases exist in this tree; migrating the empty one 'succeeds'
    and leaves a backend serving nothing."""
    import sqlite3

    from backend.migrations import m001_production as m001

    db = tmp_path / "orphan.db"
    con = sqlite3.connect(db)
    con.execute("create table runs (id text)")
    con.execute("create table investigations (id text)")
    con.commit()
    con.close()

    original = m001.resolve_database
    m001.resolve_database = lambda: db
    try:
        with pytest.raises(m001.MigrationRefused) as exc:
            m001.migrate(allow_empty=False, check_only=True)
        assert "wrong file" in str(exc.value)
        # ...but a genuinely fresh system can say so.
        assert m001.migrate(allow_empty=True, check_only=True)["allow_empty"] is True
    finally:
        m001.resolve_database = original


def test_migration_reports_counts_and_writes_nothing_in_check_mode(tmp_path):
    import sqlite3

    from backend.migrations import m001_production as m001

    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute("create table runs (id text)")
    con.execute("insert into runs values ('r1')")
    con.commit()
    con.close()
    before = db.read_bytes()

    original = m001.resolve_database
    m001.resolve_database = lambda: db
    try:
        report = m001.migrate(check_only=True)
        assert report["before"]["runs"] == 1
        assert "backup" not in report
        assert db.read_bytes() == before, "check mode must not modify the file"
    finally:
        m001.resolve_database = original
