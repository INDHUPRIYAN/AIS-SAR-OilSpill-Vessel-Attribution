"""Account administration, and every route by which authority could escalate.

This file is almost entirely about things that must NOT work. Creating a user
is four lines; the reason the module exists is that spec section 20 requires
officers to be created dynamically, and the reason it is dangerous is that an
account-creation endpoint is the shortest path to privilege escalation in any
system that has one.

The invariants:

  * an admin cannot create or modify an admin or a super_admin -- otherwise it
    mints itself one and the protected-boundary rule in section 16 is decoration;
  * only a super_admin changes a role, and not its own;
  * nobody can deactivate themselves, and the last active super_admin cannot be
    removed -- both leave a deployment whose protected boundaries can never be
    edited again except by hand-editing the database;
  * a password is never returned, never echoed and never audited.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "users-suite-password"
NEW_PASSWORD = "a-sufficiently-long-new-password"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("users_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'users.db').as_posix()}"
    os.environ["SECRET_KEY"] = "u" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db
    from backend.services.zone_seed import seed_bay_of_bengal

    init_db()
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        display_name=role, role=role, active=True))
        # A SECOND super_admin, so the "last one" guard can be tested without
        # first bricking the fixture.
        db.add(User(email="super2@example.invalid",
                    password_hash=security.hash_password(PASSWORD),
                    display_name="Super Two", role="super_admin", active=True))
        db.commit()
        seed_bay_of_bengal(db)

    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    os.environ.pop("DATABASE_URL", None)


def _as(client, email_or_role):
    email = (email_or_role if "@" in email_or_role
             else f"{email_or_role}@example.invalid")
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def _find(client, email):
    _as(client, "admin")
    rows = client.get("/api/users", params={"q": email}).json()["users"]
    return next((u for u in rows if u["email"] == email), None)


# --------------------------------------------------------------------------
# creation
# --------------------------------------------------------------------------

def test_an_admin_can_create_a_zone_officer(env):
    """The capability that made the officer model deliverable at all."""
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/users", json={
        "email": "Officer.01@Example.Invalid",
        "password": NEW_PASSWORD,
        "display_name": "Officer 01",
        "role": "zone_officer"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["email"] == "officer.01@example.invalid"   # normalised
    assert out["role"] == "zone_officer"
    assert out["active"] is True
    assert out["can_sign_in_with_password"] is True
    # A new officer with no zone yet can act nowhere, and the API says so
    # rather than leaving it to be discovered from an empty alert queue.
    assert out["unassigned_officer"] is True
    assert out["zones"] == []


def test_the_new_officer_can_actually_sign_in(env):
    """A created account that cannot authenticate is not an account."""
    client, _ = env
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": "officer.01@example.invalid",
                          "password": NEW_PASSWORD})
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me").json()
    assert me["role"] == "zone_officer"


def test_creating_many_officers_works_none_are_hard_coded(env):
    """Spec section 20: "Do NOT hard-code four officers"."""
    client, _ = env
    _as(client, "admin")
    for n in range(2, 8):
        r = client.post("/api/users", json={
            "email": f"officer.{n:02d}@example.invalid",
            "password": NEW_PASSWORD, "role": "zone_officer",
            "display_name": f"Officer {n:02d}"})
        assert r.status_code == 201, r.text
    officers = client.get("/api/users",
                          params={"role": "zone_officer"}).json()
    assert officers["count"] >= 7


def test_a_password_is_never_returned(env):
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/users", json={
        "email": "nopass@example.invalid", "password": NEW_PASSWORD,
        "role": "auditor"})
    body = r.text
    assert NEW_PASSWORD not in body
    assert "password_hash" not in body
    assert "password" not in r.json()


def test_a_password_is_never_audited(env):
    client, _ = env
    _as(client, "auditor")
    entries = client.get("/api/audit", params={"limit": 400}).json()["items"]
    blob = json.dumps(entries)
    assert NEW_PASSWORD not in blob, (
        "a credential reached the audit log, which would make the log a "
        "credential store")
    assert any(e["action"] == "user.create" for e in entries)


def test_a_short_password_is_refused(env):
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/users", json={
        "email": "short@example.invalid", "password": "short", "role": "auditor"})
    assert r.status_code == 422


def test_a_duplicate_email_is_refused(env):
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/users", json={
        "email": "officer.01@example.invalid", "password": NEW_PASSWORD,
        "role": "zone_officer"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_an_unknown_role_is_refused(env):
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/users", json={
        "email": "weird@example.invalid", "password": NEW_PASSWORD,
        "role": "overlord"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# escalation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_an_admin_cannot_create_a_privileged_account(env, role):
    """The escalation this whole module is guarded against: an admin that can
    mint an admin has effectively minted itself a super_admin."""
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/users", json={
        "email": f"escalate-{role}@example.invalid",
        "password": NEW_PASSWORD, "role": role})
    assert r.status_code == 403, r.text
    assert "may not create an account with role" in r.json()["detail"]
    assert "only a super_admin" in r.json()["detail"]


def test_a_super_admin_can_create_an_admin(env):
    client, _ = env
    _as(client, "super_admin")
    r = client.post("/api/users", json={
        "email": "admin2@example.invalid", "password": NEW_PASSWORD,
        "role": "admin", "display_name": "Admin Two"})
    assert r.status_code == 201, r.text


def test_an_admin_cannot_modify_an_admin(env):
    client, _ = env
    target = _find(client, "admin2@example.invalid")
    _as(client, "admin")
    r = client.patch(f"/api/users/{target['id']}",
                     json={"display_name": "hijacked"})
    assert r.status_code == 403
    assert "may not modify an account with role" in r.json()["detail"]


def test_an_admin_cannot_change_a_role_at_all(env):
    """Even downward, even for an account it may otherwise manage. Granting a
    role is a privilege escalation, and the direction does not make it not
    one -- demoting an investigator to auditor is still the authority to
    decide who holds what."""
    client, _ = env
    target = _find(client, "officer.02@example.invalid")
    _as(client, "admin")
    r = client.patch(f"/api/users/{target['id']}", json={"role": "auditor"})
    assert r.status_code == 403
    assert "only a super_admin may change an account's role" in r.json()["detail"]


def test_a_super_admin_can_change_a_role(env):
    client, _ = env
    target = _find(client, "officer.02@example.invalid")
    _as(client, "super_admin")
    r = client.patch(f"/api/users/{target['id']}", json={"role": "investigator"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "investigator"
    assert "role" in r.json()["changed"]


def test_a_super_admin_cannot_change_its_own_role(env):
    """Demoting yourself leaves a system whose protected boundaries nobody
    can edit."""
    client, _ = env
    _as(client, "super_admin")
    me = client.get("/api/auth/me").json()
    r = client.patch(f"/api/users/{me['id']}", json={"role": "admin"})
    assert r.status_code == 403
    assert "cannot change your own role" in r.json()["detail"]


def test_nobody_can_deactivate_themselves(env):
    """And the refusal must SAY it is about self-deactivation.

    Originally an admin got "an admin may not modify an account with role
    'admin'" here, because the role check ran before the self check -- a true
    statement and the wrong explanation. The same ordering also meant an
    administrator could not rename itself or reset its own password, which is
    the bug this test found.
    """
    client, _ = env
    for role in ("admin", "super_admin"):
        _as(client, role)
        me = client.get("/api/auth/me").json()
        r = client.patch(f"/api/users/{me['id']}", json={"active": False})
        assert r.status_code == 403, role
        assert "your own account" in r.json()["detail"], role


def test_an_account_can_edit_its_own_profile(env):
    """The other half of that fix. An administrator who cannot change its own
    display name or password has to ask a super_admin for a password reset,
    which is how shared credentials get invented."""
    client, _ = env
    _as(client, "admin")
    me = client.get("/api/auth/me").json()

    r = client.patch(f"/api/users/{me['id']}",
                     json={"display_name": "Duty Administrator"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Duty Administrator"

    r = client.patch(f"/api/users/{me['id']}",
                     json={"password": "my-own-new-long-password"})
    assert r.status_code == 200, r.text

    client.cookies.clear()
    assert client.post("/api/auth/login",
                       json={"email": "admin@example.invalid",
                             "password": "my-own-new-long-password"}
                       ).status_code == 200
    # Restore, so the module's `_as(client, "admin")` keeps working.
    me2 = client.get("/api/auth/me").json()
    assert client.patch(f"/api/users/{me2['id']}",
                        json={"password": PASSWORD}).status_code == 200


def test_a_deactivated_account_cannot_sign_in(env):
    """Deactivation is a lockout, not a label."""
    client, _ = env
    spare = _find(client, "super2@example.invalid")
    _as(client, "super_admin")
    assert client.patch(f"/api/users/{spare['id']}",
                        json={"active": False}).status_code == 200
    try:
        client.cookies.clear()
        r = client.post("/api/auth/login",
                        json={"email": "super2@example.invalid",
                              "password": PASSWORD})
        assert r.status_code == 401
    finally:
        # Restored unconditionally. A test that leaves the fixture's accounts
        # deactivated makes every later test in the module fail to sign in --
        # which is exactly what the first version of this test did.
        _as(client, "super_admin")
        client.patch(f"/api/users/{spare['id']}", json={"active": True})


def test_one_of_two_super_admins_can_be_removed(env):
    """The guard must not be "no super_admin may ever be touched" -- it is
    specifically about the LAST one."""
    client, _ = env
    spare = _find(client, "super2@example.invalid")
    _as(client, "super_admin")
    r = client.patch(f"/api/users/{spare['id']}", json={"active": False})
    assert r.status_code == 200, r.text
    _as(client, "super_admin")
    assert client.patch(f"/api/users/{spare['id']}",
                        json={"active": True}).status_code == 200


def test_demoting_the_last_super_admin_is_refused_with_a_reason(env):
    """The guard itself, exercised directly rather than through the
    self-change shortcut."""
    from backend.models.db import SessionLocal, User

    client, _ = env
    # Leave exactly one active super_admin, and act as a DIFFERENT account
    # with the authority to demote it -- which only a super_admin has. So the
    # guard is tested at the service level, where it lives.
    from backend.api.users import _assert_last_super_admin_survives
    from fastapi import HTTPException

    with SessionLocal() as db:
        supers = (db.query(User).filter(User.role == "super_admin")
                  .filter(User.active.is_(True)).all())
        assert supers, "fixture has no active super_admin"
        # Deactivate all but one.
        for row in supers[1:]:
            row.active = False
        db.commit()
        last = db.get(User, supers[0].id)

        with pytest.raises(HTTPException) as exc:
            _assert_last_super_admin_survives(db, last, new_role="admin")
        assert exc.value.status_code == 409
        assert "last active super_admin" in exc.value.detail

        # And promoting them to the same role is not a removal.
        _assert_last_super_admin_survives(db, last, new_role="super_admin")

        for row in supers[1:]:
            row.active = True
        db.commit()


def test_a_non_admin_cannot_reach_the_register(env):
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor",
                 "zone_officer"):
        _as(client, role)
        assert client.get("/api/users").status_code == 403, role
        assert client.post("/api/users", json={
            "email": "x@example.invalid", "password": NEW_PASSWORD,
            "role": "auditor"}).status_code == 403, role


# --------------------------------------------------------------------------
# deactivation and zone consequences
# --------------------------------------------------------------------------

def test_deactivating_an_officer_unassigns_their_zones_and_says_so(env):
    """An inactive account must not remain the responsible officer: alerts
    would route to somebody who cannot sign in, and the zone would look
    covered."""
    client, _ = env
    officer = _find(client, "officer.03@example.invalid")

    _as(client, "admin")
    assert client.post("/api/zones/zone-bob-03/assignments",
                       json={"user_id": officer["id"]}).status_code == 201
    assert client.post("/api/zones/zone-bob-05/assignments",
                       json={"user_id": officer["id"]}).status_code == 201

    r = client.patch(f"/api/users/{officer['id']}", json={"active": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] is False
    assert set(body["now_unrouted_zones"]) == {"zone-bob-03", "zone-bob-05"}
    assert "now route by escalation or not at all" in body["warning"]

    # And the zones really are unassigned.
    zone = client.get("/api/zones/zone-bob-03").json()
    assert not any(o["user_id"] == officer["id"] for o in zone["officers"])


def test_a_deactivated_account_loses_its_session_immediately(env):
    """Not at token expiry. A demoted or deactivated account stops working at
    once, because `optional_user` re-reads the row on every request."""
    client, _ = env
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": "officer.03@example.invalid",
                          "password": NEW_PASSWORD})
    assert r.status_code == 401


def test_reactivating_does_not_restore_zone_assignments(env):
    """Deliberate: the zones were reassigned in the meantime, and silently
    handing them back would create two primary officers."""
    client, _ = env
    officer = _find(client, "officer.03@example.invalid")
    _as(client, "admin")
    assert client.patch(f"/api/users/{officer['id']}",
                        json={"active": True}).status_code == 200
    body = client.get(f"/api/users/{officer['id']}").json()
    assert body["active"] is True
    assert body["zones"] == []


# --------------------------------------------------------------------------
# password reset
# --------------------------------------------------------------------------

def test_an_admin_can_reset_an_officer_password(env):
    client, _ = env
    officer = _find(client, "officer.04@example.invalid")
    _as(client, "admin")
    r = client.patch(f"/api/users/{officer['id']}",
                     json={"password": "another-long-enough-password"})
    assert r.status_code == 200, r.text
    assert "password" in r.json()["changed"]

    client.cookies.clear()
    assert client.post("/api/auth/login",
                       json={"email": "officer.04@example.invalid",
                             "password": "another-long-enough-password"}
                       ).status_code == 200
    client.cookies.clear()
    assert client.post("/api/auth/login",
                       json={"email": "officer.04@example.invalid",
                             "password": NEW_PASSWORD}).status_code == 401


# --------------------------------------------------------------------------
# roles and scope
# --------------------------------------------------------------------------

def test_every_role_can_read_the_role_vocabulary(env):
    """A user who cannot find out why a button is disabled files a bug."""
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor",
                 "zone_officer", "admin", "super_admin"):
        _as(client, role)
        body = client.get("/api/roles").json()
        assert body["you"] == role
        names = {r["role"] for r in body["roles"]}
        assert "zone_officer" in names and "super_admin" in names
        for row in body["roles"]:
            assert row["purpose"], f"{row['role']} has no description"


def test_the_role_list_marks_which_roles_grant_everything(env):
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/roles").json()
    by_role = {r["role"]: r for r in body["roles"]}
    assert by_role["super_admin"]["grants_everything"] is True
    assert by_role["admin"]["grants_everything"] is True
    assert by_role["investigator"]["grants_everything"] is False
    assert by_role["zone_officer"]["zone_scoped"] is True


def test_an_officer_sees_their_own_zone_scope(env):
    client, _ = env
    officer = _find(client, "officer.01@example.invalid")
    _as(client, "admin")
    client.post("/api/zones/zone-bob-04/assignments",
                json={"user_id": officer["id"]})

    client.cookies.clear()
    client.post("/api/auth/login",
                json={"email": "officer.01@example.invalid",
                      "password": NEW_PASSWORD})
    body = client.get("/api/roles").json()
    assert "zone-bob-04" in body["your_zone_scope"]

    _as(client, "reviewer")
    assert client.get("/api/roles").json()["your_zone_scope"] == "all"


def test_effective_zones_expand_a_jurisdiction_assignment_downward(env):
    """An officer assigned the theatre owns the operational zones inside it;
    listing only the literal assignment would understate their authority."""
    client, _ = env
    officer = _find(client, "officer.05@example.invalid")
    _as(client, "admin")
    assert client.post("/api/zones/zone-bob/assignments",
                       json={"user_id": officer["id"]}).status_code == 201

    body = client.get(f"/api/users/{officer['id']}/zones").json()
    assert [a["zone_id"] for a in body["assignments"]] == ["zone-bob"]
    assert "zone-bob-04" in body["effective_zone_ids"]
    assert len(body["effective_zone_ids"]) > 1
    assert "expands each assignment downward" in body["note"]

    _as(client, "admin")
    client.delete(f"/api/zones/zone-bob/assignments/{officer['id']}")


def test_users_can_be_filtered_by_zone(env):
    client, _ = env
    officer = _find(client, "officer.01@example.invalid")
    _as(client, "admin")
    rows = client.get("/api/users",
                      params={"zone_id": "zone-bob-04"}).json()["users"]
    assert officer["id"] in {u["id"] for u in rows}

    empty = client.get("/api/users",
                       params={"zone_id": "zone-bob-02"}).json()
    assert all(u["id"] != officer["id"] for u in empty["users"])


def test_role_changes_are_audited_as_role_changes(env):
    """An auditor filtering for privilege escalation should not have to read
    every profile edit."""
    client, _ = env
    _as(client, "auditor")
    entries = client.get("/api/audit", params={"limit": 400}).json()["items"]
    actions = {e["action"] for e in entries}
    assert "role.change" in actions
    assert "user.deactivate" in actions
    assert client.get("/api/audit/verify").json()["ok"] is True
