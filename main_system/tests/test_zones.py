"""Operational zones: the jurisdiction rule, and the routing it decides.

The CRUD is not what this file is about. Three things here are load-bearing and
each one was a bug before it was a test:

  * **A point exactly on an internal division line must route somewhere.** The
    seeded divisions meet on whole degrees, and `contains` is False on a
    polygon's own boundary -- so longitude exactly 87.000000 belonged to no
    zone and routed to nobody. A silent gap in alert routing is the worst
    failure this subsystem has, because nothing reports it.

  * **A protected boundary must refuse a zone officer and an admin alike, over
    HTTP.** Hiding the drag handles in the editor is not the protection. The
    403 is.

  * **Geodesic and planar areas are not interchangeable.** The seed's
    self-check tiled the theatre and reported 100.19% coverage, which reads
    exactly like an overlap bug and is not one -- Shapely clips in planar
    lon/lat while pyproj measures along geodesics. The coverage assertion here
    is planar on purpose.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "zone-test-password"

# A small square well inside the seeded theatre's Zone 04 (lon >= 87, lat
# 11..18). Used as the geometry for sub-zones created by the tests.
INSIDE_SQUARE = {"type": "Polygon",
                 "coordinates": [[[89.0, 13.0], [90.0, 13.0],
                                  [90.0, 14.0], [89.0, 14.0]]]}
# Straddles the theatre's eastern limit, so containment must reject it.
STRADDLING = {"type": "Polygon",
              "coordinates": [[[94.0, 13.0], [99.0, 13.0],
                               [99.0, 14.0], [94.0, 14.0]]]}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("zone_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'zones.db').as_posix()}"
    os.environ["SECRET_KEY"] = "q" * 64
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
        # A second officer, to prove the scoping is per-assignment and not
        # per-role. Without this, "zone_officer is denied" could be passing
        # because officers are denied everything.
        db.add(User(email="officer2@example.invalid",
                    password_hash=security.hash_password(PASSWORD),
                    display_name="Officer 2", role="zone_officer", active=True))
        db.commit()
        seed_bay_of_bengal(db)

    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    os.environ.pop("DATABASE_URL", None)


def _as(client, email_or_role):
    email = (email_or_role if "@" in email_or_role
             else f"{email_or_role}@example.invalid")
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email,
                                             "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def _uid(client, email_or_role) -> int:
    """The account's id, WITHOUT leaving the client signed in as them.

    The obvious version signs in to read /auth/me and returns, so a later
    `client.post(...)` in the same test ran as whoever was looked up rather
    than as the admin the test had authenticated. That produced a 403 that
    looked like an authorisation bug and was a fixture bug.
    """
    was = dict(client.cookies)
    try:
        _as(client, email_or_role)
        return client.get("/api/auth/me").json()["id"]
    finally:
        client.cookies.clear()
        for k, v in was.items():
            client.cookies.set(k, v)


# --------------------------------------------------------------------------
# the seed
# --------------------------------------------------------------------------

def test_the_theatre_and_its_divisions_exist(env):
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/zones").json()
    ids = {z["id"] for z in body["zones"]}
    assert "zone-bob" in ids
    assert {f"zone-bob-0{n}" for n in range(1, 7)} <= ids

    theatre = next(z for z in body["zones"] if z["id"] == "zone-bob")
    assert theatre["kind"] == "jurisdiction"
    assert theatre["protected"] is True
    # INTL, not a country code. Stamping one littoral state's code on the whole
    # basin would assert something untrue.
    assert theatre["jurisdiction"] == "INTL"
    assert theatre["area_km2"] > 1_000_000

    for zone in body["zones"]:
        if zone["kind"] == "operational":
            assert zone["protected"] is False
            assert zone["parent_id"] == "zone-bob"
            # The disclaimer travels on the row, because this is the text the
            # Zone Management page and the report annex display.
            assert "Asserts nothing about EEZ" in zone["notes"]


def test_divisions_tile_the_theatre_with_no_gap(env):
    """Coverage measured PLANAR. See the module docstring on why."""
    from shapely.ops import unary_union

    from backend.models.db import SessionLocal, Zone
    from backend.services import zones as zsvc

    with SessionLocal() as db:
        theatre = zsvc.zone_polygon(db.get(Zone, "zone-bob"))
        parts = [zsvc.zone_polygon(z) for z in
                 db.query(Zone).filter(Zone.kind == "operational").all()]
    covered = unary_union(parts).intersection(theatre).area / theatre.area
    assert covered == pytest.approx(1.0, abs=1e-9), (
        f"the divisions leave {(1 - covered) * 100:.4f}% of the theatre "
        f"unassigned; water that routes to nobody")


def test_every_division_lies_inside_the_theatre(env):
    from backend.models.db import SessionLocal, Zone
    from backend.services import zones as zsvc

    with SessionLocal() as db:
        theatre = db.get(Zone, "zone-bob")
        for zone in db.query(Zone).filter(Zone.kind == "operational").all():
            spill = zsvc.outside_fraction(zsvc.zone_polygon(zone),
                                          zsvc.zone_polygon(theatre))
            assert spill <= zsvc.CONTAINMENT_TOLERANCE, (
                f"{zone.id} is {spill * 100:.4f}% outside its parent")


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lon,lat,expect", [
    (85.0, 15.0, "zone-bob-03"),
    (90.0, 15.0, "zone-bob-04"),
    (85.5, 19.0, "zone-bob-01"),
    (90.0, 20.0, "zone-bob-02"),
    (83.0, 7.5, "zone-bob-05"),
    (93.0, 7.0, "zone-bob-06"),
])
def test_interior_points_route_to_their_division(env, lon, lat, expect):
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/zones/lookup", params={"lon": lon, "lat": lat}).json()
    assert body["zone"]["id"] == expect


@pytest.mark.parametrize("lon,lat", [
    (87.0, 15.0),     # exactly on the Zone 03 / Zone 04 line
    (88.0, 11.0),     # exactly on a four-zone corner
    (88.0, 18.0),
    (87.0, 11.0),
])
def test_a_point_on_an_internal_line_still_routes(env, lon, lat):
    """The silent-gap regression. `contains` is False on a boundary; `covers`
    is not, and the tie-break turns two matches into one answer."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/zones/lookup",
                      params={"lon": lon, "lat": lat}).json()
    assert body["zone"] is not None, (
        f"({lon}, {lat}) lies exactly on an internal division line and routed "
        f"to no zone -- an incident there would reach nobody")
    assert body["zone"]["kind"] == "operational"
    assert body["routing"] in ("zone", "escalated", "unrouted")


def test_boundary_point_routing_is_stable_across_calls(env):
    """A boundary point is covered by two divisions. It must not alternate:
    the same coordinate routing to a different officer on a different day is
    worse than either answer."""
    client, _ = env
    _as(client, "auditor")
    seen = {client.get("/api/zones/lookup",
                       params={"lon": 87.0, "lat": 15.0}).json()["zone"]["id"]
            for _ in range(5)}
    assert len(seen) == 1, f"routing is not deterministic: {seen}"


def test_the_lookup_and_the_router_agree(env):
    """`/zones/lookup` exists so an operator can preview routing. If it
    disagreed with `zone_for_point`, the preview would be a lie."""
    from backend.models.db import SessionLocal
    from backend.services import zones as zsvc

    client, _ = env
    _as(client, "auditor")
    for lon, lat in [(87.0, 15.0), (88.0, 11.0), (90.0, 15.0), (85.5, 19.0)]:
        previewed = client.get("/api/zones/lookup",
                               params={"lon": lon, "lat": lat}).json()["zone"]
        with SessionLocal() as db:
            actual = zsvc.zone_for_point(db, lon, lat)
        assert (previewed or {}).get("id") == (actual.id if actual else None)


@pytest.mark.parametrize("lon,lat", [(60.0, 15.0), (79.0, 13.0), (0.0, 0.0)])
def test_water_outside_every_zone_reports_no_zone(env, lon, lat):
    """Not a nearest-zone guess. Open ocean outside the theatre is a real
    answer and the API says so."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/zones/lookup", params={"lon": lon, "lat": lat}).json()
    assert body["zone"] is None
    assert body["chain"] == []
    assert body["routing"] == "unrouted"
    assert body["responsible_officer"] is None


# --------------------------------------------------------------------------
# the jurisdiction rule
# --------------------------------------------------------------------------

def test_admin_cannot_move_a_protected_boundary(env):
    """Spec section 16. An operational administrator runs the operation; they
    do not get to redraw the theatre."""
    client, _ = env
    _as(client, "admin")
    before = client.get("/api/zones/zone-bob").json()
    r = client.patch("/api/zones/zone-bob",
                     json={"geometry": INSIDE_SQUARE,
                           "revision": before["revision"]})
    assert r.status_code == 403, r.text
    assert "protected" in r.json()["detail"]
    assert "super_admin" in r.json()["detail"]

    # And the boundary really did not move.
    after = client.get("/api/zones/zone-bob").json()
    assert after["revision"] == before["revision"]
    assert after["area_km2"] == before["area_km2"]


def test_admin_can_still_rename_a_protected_zone(env):
    """The thing protected is the border, not the label. An admin who could
    not fix a typo would just get super_admin credentials, which is worse."""
    client, _ = env
    _as(client, "admin")
    r = client.patch("/api/zones/zone-bob", json={"name": "Bay of Bengal"})
    assert r.status_code == 200, r.text
    assert r.json()["protected"] is True


def test_zone_officer_cannot_move_a_protected_boundary(env):
    client, _ = env
    _as(client, "zone_officer")
    before = client.get("/api/zones/zone-bob").json()
    r = client.patch("/api/zones/zone-bob",
                     json={"geometry": INSIDE_SQUARE,
                           "revision": before["revision"]})
    assert r.status_code == 403


def test_super_admin_may_move_a_protected_boundary(env):
    """Otherwise the rule is not 'protected', it is 'frozen'."""
    from backend.models.db import SessionLocal, Zone
    from backend.services import zones as zsvc

    from shapely.geometry import shape

    client, _ = env
    _as(client, "super_admin")
    before = client.get("/api/zones/zone-bob").json()

    # Buffered strictly OUTWARD, so no existing division can end up outside
    # the new boundary. A first attempt nudged each vertex by +/-0.01 degrees
    # keyed on longitude, which on a non-convex ring pulls some edges INWARD --
    # and the sub-zone containment check correctly refused it. The check was
    # right; the test geometry was wrong.
    widened = shape(before["geometry"]).buffer(0.05, join_style=2)
    r = client.patch(
        "/api/zones/zone-bob",
        json={"geometry": {"type": "Polygon",
                           "coordinates": [[list(c) for c in
                                            widened.exterior.coords]]},
              "revision": before["revision"],
              "reason": "test: widen the theatre"})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == before["revision"] + 1

    # Restore, so later tests see the seeded boundary.
    now = client.get("/api/zones/zone-bob").json()
    restored = client.patch(
        "/api/zones/zone-bob",
        json={"geometry": before["geometry"], "revision": now["revision"],
              "reason": "test: restore"})
    assert restored.status_code == 200, restored.text
    with SessionLocal() as db:
        area = db.get(Zone, "zone-bob").area_km2
    assert area == pytest.approx(before["area_km2"], rel=1e-9)


def test_an_analyst_cannot_reach_the_editor_at_all(env):
    """The route guard, as distinct from the per-zone scoping."""
    client, _ = env
    _as(client, "analyst")
    r = client.patch("/api/zones/zone-bob-04", json={"name": "nope"})
    assert r.status_code == 403
    r = client.post("/api/zones", json={"id": "zone-x", "name": "X",
                                        "geometry": INSIDE_SQUARE,
                                        "parent_id": "zone-bob"})
    assert r.status_code == 403


def test_only_super_admin_declares_a_new_jurisdiction(env):
    """A top-level zone has no parent to be protected by, so an admin who
    could create one could route around the protection rule entirely."""
    client, _ = env
    _as(client, "admin")
    r = client.post("/api/zones", json={"id": "zone-rogue", "name": "Rogue",
                                        "kind": "jurisdiction",
                                        "geometry": INSIDE_SQUARE})
    assert r.status_code == 403
    assert "super_admin" in r.json()["detail"]


# --------------------------------------------------------------------------
# officer scoping
# --------------------------------------------------------------------------

def test_officer_scoping_is_per_assignment_not_per_role(env):
    client, _ = env
    officer1 = _uid(client, "zone_officer")
    officer2 = _uid(client, "officer2@example.invalid")

    _as(client, "admin")
    assert client.post("/api/zones/zone-bob-04/assignments",
                       json={"user_id": officer1}).status_code == 201
    assert client.post("/api/zones/zone-bob-05/assignments",
                       json={"user_id": officer2}).status_code == 201

    # Officer 1 may rename their own zone...
    _as(client, "zone_officer")
    r = client.patch("/api/zones/zone-bob-04",
                     json={"notes": "officer 1 was here"})
    assert r.status_code == 200, r.text

    # ...and not Officer 2's.
    r = client.patch("/api/zones/zone-bob-05", json={"notes": "trespass"})
    assert r.status_code == 403
    assert "outside your assignment" in r.json()["detail"]


def test_an_unassigned_officer_owns_nothing(env):
    """An empty assignment set must mean no authority, never all authority."""
    client, _ = env
    _as(client, "reviewer")           # a role with no zone assignment
    body = client.get("/api/zones/mine").json()
    assert body["zones"] == []

    _as(client, "admin")
    fresh = client.post("/api/zones/zone-bob-06/assignments",
                        json={"user_id": _uid(client, "auditor")})
    # Any role may be assigned -- an investigator covering a zone during a
    # handover is legitimate.
    assert fresh.status_code == 201, fresh.text


def test_officers_see_every_zone_regardless_of_assignment(env):
    """Spec section 20: global visibility, scoped control. An officer who
    cannot see traffic approaching their boundary from outside it is worse at
    their job, not more secure."""
    client, _ = env
    _as(client, "zone_officer")
    body = client.get("/api/zones").json()
    assert len({z["id"] for z in body["zones"]}) >= 7
    assert client.get("/api/zones/zone-bob-05").status_code == 200
    assert client.get("/api/zones/geojson").status_code == 200

    mine = {z["id"] for z in body["zones"] if z["mine"]}
    assert mine and mine != {z["id"] for z in body["zones"]}, (
        "'mine' must distinguish the officer's own zones from the rest")


def test_report_scope_is_the_officers_zones_only(env):
    client, _ = env
    _as(client, "zone_officer")
    body = client.get("/api/zones/mine").json()
    assert body["report_scope"] != "all"
    assert "zone-bob-04" in body["report_scope"]

    _as(client, "reviewer")
    assert client.get("/api/zones/mine").json()["report_scope"] == "all"


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

def test_officer_can_split_their_own_zone(env):
    """The zone-splitting workflow, end to end."""
    client, _ = env
    _as(client, "zone_officer")
    r = client.post("/api/zones", json={
        "id": "zone-bob-04a", "name": "Zone 04A - Inner",
        "geometry": INSIDE_SQUARE, "parent_id": "zone-bob-04",
        "notes": "split for tasking"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["parent_id"] == "zone-bob-04"
    # Jurisdiction is inherited, never accepted from the body.
    assert created["jurisdiction"] == "INTL"
    assert created["protected"] is False
    assert created["area_km2"] > 0

    # The sub-zone is now the most specific zone over its own area.
    body = client.get("/api/zones/lookup",
                      params={"lon": 89.5, "lat": 13.5}).json()
    assert body["zone"]["id"] == "zone-bob-04a"
    assert [c["id"] for c in body["chain"]] == [
        "zone-bob", "zone-bob-04", "zone-bob-04a"]


def test_a_zone_cannot_be_drawn_outside_its_parent(env):
    client, _ = env
    _as(client, "zone_officer")
    r = client.post("/api/zones", json={
        "id": "zone-bob-04b", "name": "Straddler",
        "geometry": STRADDLING, "parent_id": "zone-bob-04"})
    assert r.status_code == 422, r.text
    assert "outside its parent" in r.json()["detail"]


def test_shrinking_a_zone_cannot_orphan_a_sub_zone(env):
    """Otherwise a sub-zone would keep routing points its parent chain says
    belong somewhere else."""
    client, _ = env
    _as(client, "zone_officer")
    parent = client.get("/api/zones/zone-bob-04").json()
    # A square that excludes the 04a sub-zone entirely.
    away = {"type": "Polygon",
            "coordinates": [[[92.0, 16.0], [93.0, 16.0],
                             [93.0, 17.0], [92.0, 17.0]]]}
    r = client.patch("/api/zones/zone-bob-04",
                     json={"geometry": away, "revision": parent["revision"]})
    assert r.status_code == 422, r.text
    assert "zone-bob-04a" in r.json()["detail"]


def test_a_geometry_edit_must_declare_the_revision_it_drew_against(env):
    client, _ = env
    _as(client, "zone_officer")
    r = client.patch("/api/zones/zone-bob-04a", json={"geometry": INSIDE_SQUARE})
    assert r.status_code == 422
    assert "revision" in r.json()["detail"]


def test_a_stale_revision_is_rejected_not_merged(env):
    """Two officers splitting the same zone in two tabs. Last-write-wins would
    silently discard one of them."""
    client, _ = env
    _as(client, "zone_officer")
    stale = client.get("/api/zones/zone-bob-04a").json()["revision"]

    smaller = {"type": "Polygon",
               "coordinates": [[[89.1, 13.1], [89.9, 13.1],
                                [89.9, 13.9], [89.1, 13.9]]]}
    assert client.patch("/api/zones/zone-bob-04a",
                        json={"geometry": smaller,
                              "revision": stale}).status_code == 200

    r = client.patch("/api/zones/zone-bob-04a",
                     json={"geometry": INSIDE_SQUARE, "revision": stale})
    assert r.status_code == 409, r.text
    assert "moved this boundary" in r.json()["detail"]


@pytest.mark.parametrize("geometry,fragment", [
    ({"type": "Point", "coordinates": [80, 10]}, "Polygon"),
    ({"type": "Polygon", "coordinates": [[[89, 13], [90, 13]]]}, "at least 3"),
    ({"type": "Polygon",
      "coordinates": [[[89, 13], [90, 14], [90, 13], [89, 14]]]},
     "not a simple polygon"),
    ({"type": "Polygon", "coordinates": [[[13, 189], [14, 189], [14, 190]]]},
     "out of range"),
])
def test_unusable_geometry_is_refused_with_a_reason(env, geometry, fragment):
    """A 422 that says which point is wrong is what the editor puts on screen.
    'Invalid geometry' is not actionable."""
    client, _ = env
    _as(client, "zone_officer")
    r = client.post("/api/zones", json={"id": "zone-bob-04z", "name": "Bad",
                                        "geometry": geometry,
                                        "parent_id": "zone-bob-04"})
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_a_malformed_zone_id_is_refused(env):
    client, _ = env
    _as(client, "zone_officer")
    for bad in ["Zone 04", "zone/04", "z", "ZONE-04", "zone_04"]:
        r = client.post("/api/zones", json={"id": bad, "name": "X",
                                            "geometry": INSIDE_SQUARE,
                                            "parent_id": "zone-bob-04"})
        assert r.status_code == 422, f"{bad!r} was accepted"


# --------------------------------------------------------------------------
# history and deletion
# --------------------------------------------------------------------------

def test_every_boundary_change_leaves_a_revision(env):
    client, _ = env
    _as(client, "zone_officer")
    body = client.get("/api/zones/zone-bob-04a/revisions").json()
    kinds = [r["change"] for r in body["revisions"]]
    assert "created" in kinds
    assert "geometry" in kinds

    geom = next(r for r in body["revisions"] if r["change"] == "geometry")
    assert geom["actor"] == "zone_officer@example.invalid"
    assert geom["actor_role"] == "zone_officer"
    assert geom["area_delta_km2"] is not None


def test_revision_history_is_readable_by_an_auditor(env):
    client, _ = env
    _as(client, "auditor")
    assert client.get("/api/zones/zone-bob/revisions").status_code == 200


def test_a_zone_with_sub_zones_cannot_be_deleted(env):
    client, _ = env
    _as(client, "admin")
    r = client.delete("/api/zones/zone-bob-04")
    assert r.status_code == 409
    assert "sub-zone" in r.json()["detail"]


def test_a_zone_referenced_by_an_incident_is_deactivated_not_deleted(env):
    """Deleting it would rewrite which desk a closed case was routed to."""
    from backend.models.db import Incident, SessionLocal, utcnow

    client, _ = env
    with SessionLocal() as db:
        db.add(Incident(id="INC-ZONE-TEST", title="zone ref",
                        zone_id="zone-bob-06", status="open",
                        created_utc=utcnow()))
        db.commit()

    _as(client, "admin")
    r = client.delete("/api/zones/zone-bob-06")
    assert r.status_code == 409, r.text
    assert "incident" in r.json()["detail"]
    assert "inactive" in r.json()["detail"]

    # Deactivating is allowed, and takes it out of routing.
    assert client.patch("/api/zones/zone-bob-06",
                        json={"status": "inactive"}).status_code == 200
    body = client.get("/api/zones/lookup",
                      params={"lon": 93.0, "lat": 7.0}).json()
    assert (body["zone"] or {}).get("id") != "zone-bob-06"

    assert client.patch("/api/zones/zone-bob-06",
                        json={"status": "active"}).status_code == 200


def test_deleting_a_leaf_zone_keeps_its_history(env):
    """The revision rows name a zone that no longer exists, which is the
    correct record of a zone that was deleted."""
    client, _ = env
    _as(client, "super_admin")
    assert client.post("/api/zones", json={
        "id": "zone-bob-04t", "name": "Temporary",
        "geometry": {"type": "Polygon",
                     "coordinates": [[[91.0, 12.0], [91.5, 12.0],
                                      [91.5, 12.5], [91.0, 12.5]]]},
        "parent_id": "zone-bob-04"}).status_code == 201

    r = client.delete("/api/zones/zone-bob-04t")
    assert r.status_code == 200, r.text
    assert r.json()["revisions_retained"] is True
    assert client.get("/api/zones/zone-bob-04t").status_code == 404

    from backend.models.db import SessionLocal, ZoneRevision
    with SessionLocal() as db:
        kept = (db.query(ZoneRevision)
                .filter(ZoneRevision.zone_id == "zone-bob-04t").count())
    assert kept >= 1


# --------------------------------------------------------------------------
# assignment
# --------------------------------------------------------------------------

def test_assigning_a_new_primary_demotes_the_incumbent(env):
    """One primary per zone. Rejecting the request instead would leave the
    zone briefly unrouted while an operator unassigned the old officer."""
    client, _ = env
    officer2 = _uid(client, "officer2@example.invalid")

    _as(client, "admin")
    body = client.post("/api/zones/zone-bob-04/assignments",
                       json={"user_id": officer2, "is_primary": True}).json()
    primaries = [o for o in body["officers"] if o["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["user_id"] == officer2


def test_an_officer_cannot_assign_themselves_a_zone(env):
    """Otherwise the scoping defeats itself."""
    client, _ = env
    officer1 = _uid(client, "zone_officer")
    _as(client, "zone_officer")
    r = client.post("/api/zones/zone-bob-01/assignments",
                    json={"user_id": officer1})
    assert r.status_code == 403


def test_a_deactivated_account_cannot_be_the_responsible_officer(env):
    from backend.models.db import SessionLocal, User

    client, _ = env
    with SessionLocal() as db:
        row = (db.query(User)
               .filter(User.email == "officer2@example.invalid").one())
        row.active = False
        db.commit()
        uid = row.id
    try:
        _as(client, "admin")
        r = client.post("/api/zones/zone-bob-03/assignments",
                        json={"user_id": uid})
        assert r.status_code == 422
        assert "deactivated" in r.json()["detail"]
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.id == uid).one().active = True
            db.commit()


def test_routing_escalates_to_the_parent_when_a_zone_has_no_officer(env):
    """An unassigned zone under an assigned theatre should reach a human. An
    unassigned zone under nothing reports unrouted, and is never quietly
    handed to an administrator to make the queue look clean."""
    client, _ = env
    reviewer = _uid(client, "reviewer")

    _as(client, "admin")
    # Zone 01 has no officer; give the theatre one.
    assert client.post("/api/zones/zone-bob/assignments",
                       json={"user_id": reviewer}).status_code == 201

    _as(client, "auditor")
    body = client.get("/api/zones/lookup",
                      params={"lon": 85.5, "lat": 19.0}).json()
    assert body["zone"]["id"] == "zone-bob-01"
    assert body["routing"] == "escalated"
    assert body["responsible_officer"]["email"] == "reviewer@example.invalid"

    _as(client, "admin")
    assert client.delete(
        f"/api/zones/zone-bob/assignments/{reviewer}").status_code == 200

    _as(client, "auditor")
    body = client.get("/api/zones/lookup",
                      params={"lon": 85.5, "lat": 19.0}).json()
    assert body["routing"] == "unrouted"
    assert body["responsible_officer"] is None


def test_unassigning_the_last_primary_says_the_zone_is_now_unrouted(env):
    """An operator who removed the last officer should be told, not discover
    it from a queue nothing arrives in."""
    client, _ = env
    officer2 = _uid(client, "officer2@example.invalid")
    _as(client, "admin")
    client.post("/api/zones/zone-bob-02/assignments",
                json={"user_id": officer2, "is_primary": True})
    r = client.delete(f"/api/zones/zone-bob-02/assignments/{officer2}")
    assert r.status_code == 200, r.text
    assert r.json()["now_unrouted"] is True


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

def test_zone_changes_are_audited_with_a_real_actor(env):
    """A boundary edit changes who receives future work. An audit row whose
    actor the request body could choose would be decoration."""
    client, _ = env
    _as(client, "auditor")
    entries = client.get("/api/audit", params={"limit": 400}).json()["items"]
    actions = {e["action"] for e in entries}
    assert "zone.geometry" in actions
    assert "zone.create" in actions
    assert "zone.assign" in actions

    geom = next(e for e in entries if e["action"] == "zone.geometry")
    assert geom["actor"].endswith("@example.invalid")
    assert geom["resource"].startswith("zone:")

    assert client.get("/api/audit/verify").json()["ok"] is True
