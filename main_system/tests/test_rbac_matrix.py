"""Every mounted route, against every role. This file is P07's deliverable.

The audit's finding was not that a particular guard was wrong -- it was that
per-route guards get *missed*, and a route with no guard is indistinguishable
from one that never needed one. So authentication is applied to whole routers
in `main.py`, and this test enumerates the app's real route table and asserts
the outcome for anonymous callers and for each of the five roles.

The enumeration is the point. A new endpoint added later appears here without
anyone remembering to add it, and if it answers anonymously the suite fails.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "matrix-test-password"

# The only routes reachable without a session. Anything else answering
# anonymously is a hole.
PUBLIC = {"/", "/health", "/healthz", "/readyz", "/docs", "/redoc",
          "/openapi.json", "/docs/oauth2-redirect", "/api/auth/login",
          "/api/auth/logout"}

# Routes that need more than "signed in", from the role matrix in master plan
# section 8. Everything not listed is readable by any authenticated role.
# `super_admin` appears in EVERY entry below, and that is the assertion rather
# than boilerplate: `require_role` grants it implicitly (see IMPLICIT_ROLES),
# so adding the role must not have narrowed any existing route. If someone
# writes a guard that excludes it, this table fails.
_OPS = {"investigator", "analyst", "admin", "super_admin"}
_ADMIN = {"admin", "super_admin"}
# The zone editor. `zone_officer` is admitted at the route and then narrowed to
# their own assignment inside the handler by `assert_may_edit_zone` -- a
# route-level guard cannot express "only zones you are assigned to", so that
# half is covered by test_zones.py and declared in VALUE_GUARDED below.
_DRAW = {"zone_officer", "admin", "super_admin"}

ELEVATED = {
    ("POST", "/api/investigations"): _OPS,
    ("POST", "/api/investigations/{investigation_id}/run"): _OPS,
    ("POST", "/api/investigations/{investigation_id}/replay"): _OPS,
    ("POST", "/api/runs/{run_id}/decisions"): _OPS,
    ("POST", "/api/aois/poll"): _OPS,
    ("POST", "/api/aois/{aoi_id}/poll"): _OPS,
    ("POST", "/api/apis/{provider}/test"): {"analyst", "admin", "super_admin"},
    ("POST", "/api/apis/test-all"): {"analyst", "admin", "super_admin"},
    ("POST", "/api/incidents"): _OPS,
    ("POST", "/api/incidents/from_run/{run_id}"): _OPS,
    ("GET", "/api/keys"): _ADMIN,
    ("PUT", "/api/keys"): _ADMIN,
    ("GET", "/api/keys/audit"): _ADMIN,
    ("POST", "/api/keys/{provider}/test"): _ADMIN,
    # --- operational zones ---------------------------------------------
    ("POST", "/api/zones"): _DRAW,
    ("PATCH", "/api/zones/{zone_id}"): _DRAW,
    ("DELETE", "/api/zones/{zone_id}"): _ADMIN,
    ("POST", "/api/zones/{zone_id}/assignments"): _ADMIN,
    ("DELETE", "/api/zones/{zone_id}/assignments/{user_id}"): _ADMIN,
    # --- live AIS ------------------------------------------------------
    # Reading the live picture is open to every authenticated role (global
    # situational awareness). Turning the tap on opens an outbound connection
    # and writes continuously to disk, so it is an administrator action.
    ("POST", "/api/ais/stream/start"): _ADMIN,
    ("POST", "/api/ais/stream/stop"): _ADMIN,
    ("POST", "/api/ais/stream/flush"): _ADMIN,
    ("POST", "/api/ais/live/prune"): _ADMIN,
    # --- automatic incidents -------------------------------------------
    # Same authority as promoting a run by hand: this is the same action,
    # triggered through the validation gate instead of by judgement.
    ("POST", "/api/incidents/auto/{run_id}"): _OPS,
    # Stamps zones onto historical incidents from the CURRENT boundaries, so
    # it rewrites routing metadata across the register.
    ("POST", "/api/incidents/backfill-zones"): _ADMIN,
}


# Routes whose permission depends on the VALUE being written, not just the
# caller's role, so a route-level guard cannot express it. Each one is covered
# by its own lifecycle test instead; listing them here is a declaration that
# the omission is deliberate rather than forgotten.
VALUE_GUARDED = {
    # An investigator may move a case to `investigating`, but only a reviewer
    # may conclude it as `attributed`/`closed`. See test_incidents.py.
    ("PATCH", "/api/incidents/{incident_id}"),
    # Two rules a role tuple cannot express, both in test_zones.py:
    #   * a protected jurisdiction boundary moves only for super_admin, while
    #     the same route renames it for an admin;
    #   * a zone_officer may edit only the zones assigned to them.
    # Both are enforced by `services.zones.assert_may_edit_zone`, which the
    # routes above call, and both depend on the TARGET rather than the caller.
    ("PATCH", "/api/zones/{zone_id}"),
}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("rbac_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'rbac.db').as_posix()}"
    os.environ["SECRET_KEY"] = "y" * 64
    os.environ["OT_ALLOW_LEGACY_ADMIN_TOKEN"] = "false"
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
        yield client, app

    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("OT_ALLOW_LEGACY_ADMIN_TOKEN", None)


def _mounted(app):
    """Every reachable route object, with its prefix and merged dependencies.

    This FastAPI keeps `include_router` results as `_IncludedRouter` wrappers
    rather than flattening them into `app.routes`, so walking `app.routes`
    naively finds only the handful defined directly on the app -- 15 of 45
    here. An earlier version of this file did exactly that and passed while
    checking almost nothing, which is the failure mode an RBAC test can least
    afford. `effective_candidates()` returns the real, prefixed routes with
    router-level dependencies already merged in.
    """
    found = []
    for route in app.routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            found.extend(candidates())
        elif getattr(route, "path", None):
            found.append(route)
    return found


def _routes(app):
    """Every mounted (method, path), excluding HEAD/OPTIONS."""
    out = []
    for route in _mounted(app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return sorted(set(out))


def test_the_walker_sees_the_whole_api(env):
    """Guard for the guards. If this drops back to a handful of routes, every
    assertion below becomes vacuous."""
    _, app = env
    paths = {p for _, p in _routes(app)}
    api = {p for p in paths if p.startswith("/api")}
    assert len(api) >= 30, f"only {len(api)} /api routes discovered: {sorted(api)[:5]}"
    assert "/api/runs" in api and "/api/keys" in api


def _call(client, method: str, path: str):
    """Invoke a route with placeholder path params.

    The values do not resolve to real records; that is deliberate. This test
    asks "was the caller allowed to reach the handler", so 404/422/500 all
    count as passing the guard, and only 401/403 count as blocked.
    """
    concrete = path
    for param in ("run_id", "investigation_id", "aoi_id", "provider",
                  "layer", "scene_id", "mmsi", "zone_id", "user_id",
                  "report_id", "alert_id", "incident_id", "job_id"):
        concrete = concrete.replace("{" + param + "}", "probe")
    if "{" in concrete:
        return None
    return client.request(method, concrete, json={})


def test_no_route_is_reachable_anonymously(env):
    """The headline assertion: enumerate everything, allow only PUBLIC."""
    client, app = env
    client.cookies.clear()

    leaked = []
    for method, path in _routes(app):
        if path in PUBLIC:
            continue
        r = _call(client, method, path)
        if r is None:
            continue
        if r.status_code not in (401, 403):
            leaked.append(f"{method} {path} -> {r.status_code}")

    assert not leaked, (
        "these routes answered without a session:\n  " + "\n  ".join(leaked))


def test_public_routes_stay_public(env):
    """Login must not require a session, or nobody can ever obtain one."""
    client, _ = env
    client.cookies.clear()

    assert client.get("/health").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert client.get("/").status_code == 200
    # Wrong credentials, but reachable -- 401 from the handler, not the guard.
    r = client.post("/api/auth/login", json={"email": "x@y.invalid", "password": "nope"})
    assert r.status_code == 401
    assert "invalid email or password" in r.text


@pytest.mark.parametrize("role", ["investigator", "analyst", "reviewer",
                                 "auditor", "admin", "super_admin",
                                 "zone_officer"])
def test_role_matrix(env, role):
    """Each role against every elevated route, read from the route table.

    Deliberately static rather than live. Several of these routes start a
    pipeline run in a background thread, so probing them to discover who may
    start a pipeline run would start pipeline runs -- and a daemon thread
    still opening netCDF files while pytest tears the process down segfaults
    the interpreter. Reading the declared guard answers the same question
    without the side effects, and answers it about the *declaration* rather
    than about whatever a handler happened to return.
    """
    from backend.core.authz import declared_roles

    _, app = env
    by_route = {}
    for route in _mounted(app):
        path, methods = getattr(route, "path", None), getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            by_route[(method, path)] = declared_roles(route)

    wrong = []
    for key, expected in ELEVATED.items():
        if key not in by_route:
            wrong.append(f"{key[0]} {key[1]} is not mounted")
            continue
        declared = by_route[key]
        if declared is None:
            wrong.append(f"{key[0]} {key[1]} declares NO role guard "
                         f"(expected {sorted(expected)})")
            continue
        permitted = role in declared
        if permitted != (role in expected):
            wrong.append(f"{key[0]} {key[1]} allows {sorted(declared)} "
                         f"-> {role} {'allowed' if permitted else 'denied'}, "
                         f"expected {'allow' if role in expected else 'deny'}")

    assert not wrong, f"role '{role}' mismatches:\n  " + "\n  ".join(wrong)


def test_elevated_list_covers_every_mutating_route(env):
    """A new POST/PUT/DELETE must be classified here, not silently inherit
    'any authenticated user'."""
    from backend.core.authz import declared_roles

    _, app = env
    unclassified = []
    for route in _mounted(app):
        path, methods = getattr(route, "path", None), getattr(route, "methods", None)
        if not path or not methods or not path.startswith("/api"):
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS", "GET"}):
            if path in PUBLIC:
                continue
            if (method, path) in ELEVATED or (method, path) in VALUE_GUARDED:
                continue
            if declared_roles(route) is None:
                unclassified.append(f"{method} {path}")

    assert not unclassified, (
        "mutating routes with no role guard and no entry in ELEVATED:\n  "
        + "\n  ".join(sorted(unclassified)))


def test_every_role_can_read(env):
    """Reading runs is common to all five roles; an auditor who cannot read
    the evidence cannot audit it."""
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor", "admin",
                 "super_admin", "zone_officer"):
        client.cookies.clear()
        client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid", "password": PASSWORD})
        assert client.get("/api/runs").status_code == 200, f"{role} cannot read runs"
        assert client.get("/api/apis/status").status_code == 200
        client.post("/api/auth/logout")


# --------------------------------------------------------------------------
# legacy admin token retirement
# --------------------------------------------------------------------------

def test_legacy_admin_token_is_off_by_default(env):
    """It identifies nobody, so an audit row it writes can only say 'admin'."""
    client, _ = env
    client.cookies.clear()

    from backend.core.config import get_settings

    r = client.get("/api/keys", headers={"X-Admin-Token": get_settings().admin_token})
    assert r.status_code == 401, "the shared token must not work unless opted in"


def test_legacy_admin_token_works_only_when_opted_in(env):
    client, _ = env
    client.cookies.clear()

    from backend.api import routes as routes_mod

    original = routes_mod.settings.allow_legacy_admin_token
    routes_mod.settings.allow_legacy_admin_token = True
    try:
        r = client.get("/api/keys",
                       headers={"X-Admin-Token": routes_mod.settings.admin_token})
        assert r.status_code == 200
    finally:
        routes_mod.settings.allow_legacy_admin_token = original


def test_admin_session_reaches_keys_without_any_token(env):
    """The replacement path: a real account, so the audit gets a real name."""
    client, _ = env
    client.cookies.clear()
    client.post("/api/auth/login",
                json={"email": "admin@example.invalid", "password": PASSWORD})
    assert client.get("/api/keys").status_code == 200
    client.post("/api/auth/logout")


def test_non_admin_session_cannot_reach_keys(env):
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor"):
        client.cookies.clear()
        client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid", "password": PASSWORD})
        assert client.get("/api/keys").status_code == 403, f"{role} reached /api/keys"
        client.post("/api/auth/logout")


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------

def test_cors_never_wildcards_with_credentials():
    """`*` is invalid alongside allow_credentials and would silently disable
    the cookie the entire session model depends on."""
    from backend.core.config import get_settings

    origins = get_settings().cors_origins
    assert origins, "at least one origin must be allowed"
    assert "*" not in origins
