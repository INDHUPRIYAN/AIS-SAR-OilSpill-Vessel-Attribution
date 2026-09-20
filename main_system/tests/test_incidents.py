"""Incidents: the case file, and who may conclude one.

The CRUD is unremarkable. What matters here is the lifecycle: `attributed` and
`closed` are statements about who polluted, so an investigator may gather
evidence and move a case to `investigating` but may not conclude it. That check
is value-dependent -- it inspects the status being written -- so it cannot live
in a route-level guard, which is exactly why it needs tests of its own.

Filter and pagination correctness is checked against brute force rather than
against a second query, so a wrong WHERE clause cannot agree with itself.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "incident-test-password"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("inc_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'inc.db').as_posix()}"
    os.environ["SECRET_KEY"] = "n" * 64
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
        yield client, root

    os.environ.pop("DATABASE_URL", None)


def _as(client, role):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def _create(client, **kw):
    body = {"title": kw.pop("title", "Test incident"), **kw}
    r = client.post("/api/incidents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------
# identity and creation
# --------------------------------------------------------------------------

def test_ids_are_readable_and_sequential(env):
    client, _ = env
    _as(client, "investigator")
    a = _create(client, title="First")
    b = _create(client, title="Second")

    year = datetime.now(timezone.utc).year
    assert a["id"].startswith(f"INC-{year}-")
    assert b["id"] > a["id"], "ids must sort in creation order"
    assert a["status"] == "open"


def test_geometry_must_be_geojson(env):
    client, _ = env
    _as(client, "investigator")
    bad = client.post("/api/incidents",
                      json={"title": "bad", "geometry": {"type": "Circle", "r": 5}})
    assert bad.status_code == 422
    ok = _create(client, title="with shape",
                 geometry={"type": "Point", "coordinates": [80.3, 13.1]})
    assert ok["geometry"]["type"] == "Point"


def test_unknown_assignee_is_rejected(env):
    client, _ = env
    _as(client, "investigator")
    r = client.post("/api/incidents", json={"title": "x", "assignee_id": 9999})
    assert r.status_code == 400


def test_reviewer_and_auditor_cannot_create(env):
    """Opening a case is investigative work, not a review action."""
    client, _ = env
    for role in ("reviewer", "auditor"):
        _as(client, role)
        assert client.post("/api/incidents", json={"title": "nope"}).status_code == 403


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def test_investigator_may_progress_but_not_conclude(env):
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Lifecycle")

    ok = client.patch(f"/api/incidents/{inc['id']}", json={"status": "investigating"})
    assert ok.status_code == 200 and ok.json()["status"] == "investigating"

    for concluding in ("attributed", "closed"):
        denied = client.patch(f"/api/incidents/{inc['id']}", json={"status": concluding})
        assert denied.status_code == 403, f"investigator concluded a case as {concluding}"
        assert "reviewer" in denied.json()["detail"]


def test_read_only_and_unassigned_roles_cannot_edit(env):
    """An auditor never writes, and an officer with no assignment covering the
    incident has no authority over it. Authentication alone is not enough."""
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Hands off")
    for role in ("auditor", "zone_officer"):
        _as(client, role)
        for body in ({"title": "defaced"}, {"status": "investigating"}):
            r = client.patch(f"/api/incidents/{inc['id']}", json=body)
            assert r.status_code == 403, f"{role} edited an incident with {body}"
    _as(client, "investigator")
    assert client.get(f"/api/incidents/{inc['id']}").json()["title"] == "Hands off"


def test_super_admin_may_conclude(env):
    """super_admin is implicit everywhere else; it must not rank below admin here."""
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Supervised")
    _as(client, "super_admin")
    r = client.patch(f"/api/incidents/{inc['id']}", json={"status": "closed"})
    assert r.status_code == 200, r.text


def test_reviewer_may_conclude(env):
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="For review")

    _as(client, "reviewer")
    r = client.patch(f"/api/incidents/{inc['id']}", json={"status": "attributed"})
    assert r.status_code == 200 and r.json()["status"] == "attributed"


def test_admin_may_conclude_too(env):
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="For admin")
    _as(client, "admin")
    assert client.patch(f"/api/incidents/{inc['id']}",
                        json={"status": "closed"}).status_code == 200


def test_unknown_status_is_rejected(env):
    client, _ = env
    _as(client, "reviewer")
    inc_id = client.get("/api/incidents").json()["items"][0]["id"]
    assert client.patch(f"/api/incidents/{inc_id}",
                        json={"status": "guilty"}).status_code == 422


def test_status_change_is_audited_with_the_real_actor(env):
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Audited transition")
    _as(client, "reviewer")
    client.patch(f"/api/incidents/{inc['id']}", json={"status": "attributed"})

    _as(client, "auditor")
    rows = client.get("/api/audit", params={"action": "incident.status",
                                            "limit": 200}).json()["items"]
    mine = [r for r in rows if r["resource"] == inc["id"]]
    assert mine, "a lifecycle change must be audited"
    assert mine[0]["actor"] == "reviewer@example.invalid"
    detail = json.loads(mine[0]["detail"])
    assert detail["status"] == ["open", "attributed"], "before and after are recorded"


def test_creation_is_audited(env):
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Audited creation")

    _as(client, "auditor")
    rows = client.get("/api/audit", params={"action": "incident.create",
                                            "limit": 200}).json()["items"]
    assert any(r["resource"] == inc["id"] for r in rows)


def test_a_no_op_patch_writes_no_audit_row(env):
    """Re-submitting the same values is not an event."""
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Idempotent")

    def edits_for(resource):
        # Scoped to this incident, because switching accounts to read the log
        # writes auth.login rows of its own -- counting everything would
        # measure the test rather than the patch.
        rows = client.get("/api/audit", params={"limit": 500}).json()["items"]
        return [r for r in rows
                if r["resource"] == resource
                and r["action"] in ("incident.update", "incident.status")]

    _as(client, "auditor")
    before = len(edits_for(inc["id"]))

    _as(client, "investigator")
    client.patch(f"/api/incidents/{inc['id']}", json={"title": "Idempotent"})

    _as(client, "auditor")
    assert len(edits_for(inc["id"])) == before


# --------------------------------------------------------------------------
# filtering, sorting, pagination -- checked against brute force
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus(env):
    """A spread of incidents to filter against."""
    client, _ = env
    _as(client, "analyst")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    made = []
    for i in range(12):
        made.append(_create(
            client,
            title=f"Corpus {i:02d} {'Bay' if i % 2 else 'Strait'}",
            region="IN-E" if i % 3 == 0 else "IN-W",
            detected_utc=(base + timedelta(days=i)).isoformat(),
        ))
    return made


def _all_items(client):
    return client.get("/api/incidents", params={"limit": 500}).json()["items"]


def test_region_filter_matches_brute_force(env, corpus):
    client, _ = env
    _as(client, "analyst")
    expected = {i["id"] for i in _all_items(client) if i["region"] == "IN-E"}
    got = {i["id"] for i in client.get("/api/incidents",
                                       params={"region": "IN-E",
                                               "limit": 500}).json()["items"]}
    assert got == expected and got


def test_status_filter_matches_brute_force(env, corpus):
    client, _ = env
    _as(client, "analyst")
    expected = {i["id"] for i in _all_items(client) if i["status"] == "open"}
    got = {i["id"] for i in client.get("/api/incidents",
                                       params={"status": "open",
                                               "limit": 500}).json()["items"]}
    assert got == expected


def test_text_search_matches_brute_force(env, corpus):
    client, _ = env
    _as(client, "analyst")
    expected = {i["id"] for i in _all_items(client) if "Bay" in i["title"]}
    got = {i["id"] for i in client.get("/api/incidents",
                                       params={"q": "Bay", "limit": 500}).json()["items"]}
    assert got == expected and got


def test_date_range_filter(env, corpus):
    client, _ = env
    _as(client, "analyst")
    r = client.get("/api/incidents", params={"from": "2026-01-05T00:00:00Z",
                                             "to": "2026-01-08T00:00:00Z",
                                             "limit": 500}).json()
    for item in r["items"]:
        assert "2026-01-05" <= str(item["detected_utc"])[:10] <= "2026-01-08"


def test_pagination_is_a_stable_partition(env, corpus):
    """Paging must cover everything exactly once -- no gaps, no repeats."""
    client, _ = env
    _as(client, "analyst")
    total = client.get("/api/incidents", params={"limit": 1}).json()["total"]

    seen, offset = [], 0
    while offset < total:
        page = client.get("/api/incidents",
                          params={"limit": 5, "offset": offset,
                                  "sort": "created_utc", "order": "asc"}).json()
        seen.extend(i["id"] for i in page["items"])
        offset += 5

    assert len(seen) == total
    assert len(set(seen)) == total, "a record appeared on two pages"


def test_sorting_both_directions(env, corpus):
    client, _ = env
    _as(client, "analyst")
    asc = [i["id"] for i in client.get(
        "/api/incidents", params={"sort": "created_utc", "order": "asc",
                                  "limit": 500}).json()["items"]]
    desc = [i["id"] for i in client.get(
        "/api/incidents", params={"sort": "created_utc", "order": "desc",
                                  "limit": 500}).json()["items"]]
    assert asc == list(reversed(desc))


# --------------------------------------------------------------------------
# linkage to runs
# --------------------------------------------------------------------------

def test_detail_lists_linked_investigations_and_runs(env):
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Linked")

    inv = client.post("/api/investigations",
                      json={"name": "linked inv", "incident_id": inc["id"]})
    assert inv.status_code == 200, inv.text

    detail = client.get(f"/api/incidents/{inc['id']}").json()
    assert detail["investigations"] == 1
    assert detail["investigation_list"][0]["id"] == inv.json()["id"]


def test_investigation_rejects_an_unknown_incident(env):
    client, _ = env
    _as(client, "investigator")
    r = client.post("/api/investigations",
                    json={"name": "orphan", "incident_id": "INC-1999-001"})
    assert r.status_code == 400


def test_promote_from_run_seeds_geometry_from_the_slick(env):
    """The case is anchored to what the pipeline detected, not to a typed-in
    coordinate."""
    client, root = env
    from backend.models.db import Investigation, Run, SessionLocal

    run_id = "inv-promote-1"
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "slick.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "metadata": {"acquired_utc": "2026-02-03T04:05:06Z"},
        "features": [{"type": "Feature",
                      "geometry": {"type": "Polygon", "coordinates": []},
                      "properties": {"centroid": [80.5, 13.2]}}],
    }), encoding="utf-8")

    with SessionLocal() as db:
        db.add(Investigation(id="inv-promote", name="promote"))
        db.add(Run(id=run_id, investigation_id="inv-promote", status="complete"))
        db.commit()

    _as(client, "investigator")
    r = client.post(f"/api/incidents/from_run/{run_id}")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["geometry"] == {"type": "Point", "coordinates": [80.5, 13.2]}
    assert body["status"] == "investigating"

    again = client.post(f"/api/incidents/from_run/{run_id}")
    assert again.status_code == 409, "a run already filed must not be promoted twice"


def test_promote_without_a_slick_still_opens_a_case(env):
    """An incident with no shape is honest; one with an invented shape is not."""
    client, _ = env
    from backend.models.db import Investigation, Run, SessionLocal

    with SessionLocal() as db:
        db.add(Investigation(id="inv-noslick", name="no slick"))
        db.add(Run(id="inv-noslick-1", investigation_id="inv-noslick", status="complete"))
        db.commit()

    _as(client, "investigator")
    r = client.post("/api/incidents/from_run/inv-noslick-1")
    assert r.status_code == 201
    assert r.json()["geometry"] is None


def test_promoting_an_unknown_run_is_404(env):
    client, _ = env
    _as(client, "investigator")
    assert client.post("/api/incidents/from_run/nope").status_code == 404


def test_runs_are_stamped_with_the_incident_at_creation(env):
    """Stamped, not joined: re-filing the investigation later must not rewrite
    what a sealed run was evidence for."""
    client, _ = env
    _as(client, "investigator")
    inc = _create(client, title="Stamping")
    inv_id = client.post("/api/investigations",
                         json={"name": "stamped", "incident_id": inc["id"]}).json()["id"]

    from backend.models.db import Investigation, Run, SessionLocal

    with SessionLocal() as db:
        db.add(Run(id=f"{inv_id}-r1", investigation_id=inv_id,
                   incident_id=db.get(Investigation, inv_id).incident_id))
        db.commit()
        run = db.get(Run, f"{inv_id}-r1")
        assert run.incident_id == inc["id"]

        # Re-file the investigation elsewhere; the sealed run keeps its case.
        db.get(Investigation, inv_id).incident_id = None
        db.commit()
        assert db.get(Run, f"{inv_id}-r1").incident_id == inc["id"]
