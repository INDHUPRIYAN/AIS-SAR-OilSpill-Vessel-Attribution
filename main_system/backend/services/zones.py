"""Operational zones: geometry, containment, routing and who may redraw what.

This module is where the jurisdiction rule is actually enforced. The UI hides
the handles on a protected boundary, but hiding a control is a courtesy, not a
permission check -- a zone officer with `curl` must get a 403, and that answer
comes from `assert_may_edit_zone` below.

Three decisions worth reading before changing anything here.

**Geometry is GeoJSON text, predicates are Shapely.** There is no PostGIS
geometry column. The deployment runs SQLite; Shapely computes `contains` and
`intersects` identically on either backend, so moving to Postgres later is a
`DATABASE_URL` change rather than a rewrite of this file. What we give up is
server-side spatial indexing, and the `bbox_json` prefilter in
`zone_for_point` is the substitute: it rejects a zone with four float
comparisons before anything parses a polygon.

**Areas are geodesic, not planar.** `shapely.area` on lon/lat degrees is not
an area -- it is a number that varies by a factor of two between the equator
and 60 degrees north. Every figure a human reads comes from `pyproj.Geod` on
the WGS84 ellipsoid. Planar degree-area is used only for tolerance ratios,
where the units cancel.

**Containment uses a relative tolerance, not `contains`.** An officer
splitting a jurisdiction is expected to draw along its edge, and two polygons
that share an edge fail a strict `contains` on floating-point rounding. So the
test is "how much of the child falls outside the parent, relative to the
child's own size", which passes a shared edge and still rejects a zone that
genuinely pokes out.

LIMITATION: none of this handles a polygon crossing the antimeridian. A zone
whose longitudes span +180/-180 will have a bbox covering the whole planet and
its containment test will be wrong. The Bay of Bengal does not cross it. A
deployment that needs to must split the polygon at the seam first; this is
stated rather than silently mishandled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.db import (Zone, ZoneAssignment, ZoneRevision, User,
                               utcnow)

# A zone boundary is drawn by hand, so a few hundred metres of slop along a
# shared edge is normal. This is the fraction of the child's own planar area
# that may fall outside the parent before the edit is rejected. At Bay of
# Bengal scale 1e-4 is on the order of tens of metres along a 1000 km edge.
CONTAINMENT_TOLERANCE = 1e-4

# Fewer than three distinct points is not a polygon, and more than this is
# almost certainly a client sending a traced coastline into a boundary editor.
MIN_RING_POINTS = 3
MAX_RING_POINTS = 2000


class ZoneGeometryError(ValueError):
    """The submitted geometry is not a usable zone boundary."""


@dataclass(frozen=True)
class NormalisedGeometry:
    """A validated boundary and the two figures derived from it."""

    geometry_json: str
    bbox: list[float]                 # [lon_min, lat_min, lon_max, lat_max]
    area_km2: float


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _shapely_polygon(geometry: dict):
    """A Shapely polygon from a GeoJSON dict, or raise `ZoneGeometryError`."""
    from shapely.geometry import shape
    from shapely.validation import explain_validity

    gtype = (geometry or {}).get("type")
    if gtype != "Polygon":
        raise ZoneGeometryError(
            f"zone geometry must be a GeoJSON Polygon, got {gtype!r}")

    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings:
        raise ZoneGeometryError("Polygon has no coordinate ring")

    exterior = rings[0]
    if not isinstance(exterior, list) or len(exterior) < MIN_RING_POINTS:
        raise ZoneGeometryError(
            f"a boundary needs at least {MIN_RING_POINTS} points, got "
            f"{len(exterior) if isinstance(exterior, list) else 0}")
    if len(exterior) > MAX_RING_POINTS:
        raise ZoneGeometryError(
            f"boundary has {len(exterior)} points, limit is {MAX_RING_POINTS}")

    for point in exterior:
        if (not isinstance(point, (list, tuple)) or len(point) < 2
                or not all(isinstance(c, (int, float)) for c in point[:2])):
            raise ZoneGeometryError(f"malformed coordinate pair: {point!r}")
        lon, lat = float(point[0]), float(point[1])
        # LONGITUDE FIRST, per the frozen convention. A caller who swapped
        # them usually trips this, which is the point of checking both.
        if not -180.0 <= lon <= 180.0:
            raise ZoneGeometryError(
                f"longitude {lon} out of range; coordinates are "
                "[longitude, latitude] -- longitude first")
        if not -90.0 <= lat <= 90.0:
            raise ZoneGeometryError(
                f"latitude {lat} out of range; coordinates are "
                "[longitude, latitude] -- longitude first")

    try:
        poly = shape(geometry)
    except Exception as exc:                       # noqa: BLE001 - any shapely parse failure
        raise ZoneGeometryError(f"unusable geometry: {type(exc).__name__}: {exc}") from exc

    if poly.is_empty:
        raise ZoneGeometryError("boundary encloses no area")
    if not poly.is_valid:
        # Self-intersection is the common case (an operator dragged a vertex
        # across an edge). Reporting Shapely's own reason gives the UI
        # something honest to put next to the offending point.
        raise ZoneGeometryError(f"boundary is not a simple polygon: "
                                f"{explain_validity(poly)}")
    return poly


def geodesic_area_km2(poly) -> float:
    """Area on the WGS84 ellipsoid, in square kilometres."""
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    area_m2, _perimeter = geod.geometry_area_perimeter(poly)
    # `geometry_area_perimeter` signs the area by ring orientation; a
    # clockwise ring comes back negative and is the same patch of ocean.
    return abs(area_m2) / 1e6


def normalise_geometry(geometry: Any) -> NormalisedGeometry:
    """Validate a submitted boundary and derive its bbox and area.

    Accepts a GeoJSON Polygon dict, a Feature wrapping one, or a JSON string
    of either -- a boundary editor will send any of the three, and rejecting
    two of them as "malformed" would be a parsing opinion dressed up as
    validation.
    """
    if isinstance(geometry, str):
        try:
            geometry = json.loads(geometry)
        except json.JSONDecodeError as exc:
            raise ZoneGeometryError(f"geometry is not valid JSON: {exc}") from exc
    if isinstance(geometry, dict) and geometry.get("type") == "Feature":
        geometry = geometry.get("geometry")

    poly = _shapely_polygon(geometry if isinstance(geometry, dict) else {})
    lon_min, lat_min, lon_max, lat_max = poly.bounds
    # Re-serialised from Shapely rather than echoing the client's bytes, so a
    # ring the client left unclosed is stored closed and every reader after
    # this point sees one canonical shape.
    canonical = {"type": "Polygon",
                 "coordinates": [[[round(x, 7), round(y, 7)]
                                  for x, y in poly.exterior.coords]]}
    return NormalisedGeometry(
        geometry_json=json.dumps(canonical, separators=(",", ":")),
        bbox=[round(lon_min, 7), round(lat_min, 7),
              round(lon_max, 7), round(lat_max, 7)],
        area_km2=round(geodesic_area_km2(poly), 4),
    )


def zone_polygon(zone: Zone):
    """The stored boundary of `zone` as a Shapely polygon."""
    from shapely.geometry import shape

    return shape(json.loads(zone.geometry_json))


def outside_fraction(child_poly, parent_poly) -> float:
    """How much of `child_poly` lies outside `parent_poly`, as a fraction.

    Planar degree-area on both sides, which is meaningless as an area and
    exactly right as a ratio: the projection distortion is the same for the
    numerator and the denominator at these scales, so it cancels.
    """
    if child_poly.area <= 0:
        return 0.0
    return child_poly.difference(parent_poly).area / child_poly.area


def assert_inside_parent(child_poly, parent: Zone) -> None:
    """A child zone must not extend past the boundary it divides.

    This is the geometric half of the jurisdiction rule (spec section 16). The
    authorisation half is `assert_may_edit_zone`: together they mean an officer
    can reshape their own patch freely and cannot annex water that belongs to
    someone else, whether by editing the outer boundary or by drawing over it.
    """
    spill = outside_fraction(child_poly, zone_polygon(parent))
    if spill > CONTAINMENT_TOLERANCE:
        raise HTTPException(
            status_code=422,
            detail=(f"boundary extends outside its parent zone "
                    f"'{parent.id}' ({parent.name}): {spill * 100:.2f}% of the "
                    f"drawn area falls outside it. An operational zone must "
                    f"stay within the jurisdiction it divides."))


# --------------------------------------------------------------------------
# lookup and routing
# --------------------------------------------------------------------------

def _bbox_hit(zone: Zone, lon: float, lat: float) -> bool:
    try:
        lon_min, lat_min, lon_max, lat_max = json.loads(zone.bbox_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        # A row with an unreadable bbox cache must not be skipped silently --
        # fall through to the real polygon test rather than pretend the point
        # is outside it.
        return True
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _depth(db: Session, zone: Zone, _seen: Optional[set] = None) -> int:
    """How many parents `zone` has. Used to prefer the most specific match."""
    seen = _seen or set()
    depth, cursor = 0, zone
    while cursor.parent_id and cursor.parent_id not in seen:
        seen.add(cursor.id)
        cursor = db.get(Zone, cursor.parent_id)
        if cursor is None:
            break
        depth += 1
    return depth


def _covers_point(zone: Zone, point) -> bool:
    """Whether `zone`'s boundary encloses `point`, boundary included.

    `covers`, NOT `contains`. Shapely's `contains` is False for a point lying
    exactly on the ring, and the seeded Bay of Bengal divisions meet on whole
    degrees -- so a detection at longitude exactly 87.000000 belonged to no
    zone at all and routed to nobody. That is a silent gap in alert routing,
    which is the one thing routing must never have, and it was invisible until
    a round-number test coordinate hit it.

    Using `covers` makes a point on a shared line match BOTH neighbours; the
    tie-break in `zone_for_point` is what turns that into one answer.
    """
    try:
        return zone_polygon(zone).covers(point)
    except Exception:                              # noqa: BLE001 - corrupt stored geometry
        # A row whose polygon will not parse must not silently swallow points.
        # Returning False excludes it and lets a neighbouring zone answer; the
        # corrupt row shows up as a zone that never matches anything, which is
        # findable, rather than as a zone that matches everything.
        return False


def zone_for_point(db: Session, lon: float, lat: float,
                   kind: str = "operational") -> Optional[Zone]:
    """The most specific active zone covering the point, or None.

    "Most specific" means deepest in the parent chain: a point inside Zone 03
    is also inside the Bay of Bengal theatre, and routing an alert to the
    theatre when a sub-zone owns it would put it on the wrong desk.

    Ties are broken by lowest zone id. A point on the line between two
    divisions genuinely belongs to both, and routing has to pick one -- so it
    picks the same one every time. An arbitrary-but-stable rule is better than
    a correct-sounding one that depends on database row order, because the
    latter would route the same coordinate to different officers on different
    days.

    Returns None when no zone covers the point. That is a real answer -- open
    ocean outside every declared zone -- and the caller must represent it as
    "outside all operational zones", never by falling back to a nearest guess.
    """
    from shapely.geometry import Point

    point = Point(lon, lat)
    q = db.query(Zone).filter(Zone.status == "active")
    if kind:
        q = q.filter(Zone.kind == kind)

    best: Optional[Zone] = None
    best_key: tuple[int, str] = (-1, "")
    for zone in q.all():
        if not _bbox_hit(zone, lon, lat):
            continue
        if not _covers_point(zone, point):
            continue
        # Deeper wins; on equal depth the lower id wins. Negating the id
        # ordering is why the comparison is built as a tuple rather than two
        # nested ifs.
        key = (_depth(db, zone), zone.id)
        if best is None or key[0] > best_key[0] or (
                key[0] == best_key[0] and key[1] < best_key[1]):
            best, best_key = zone, key
    return best


def zones_covering_point(db: Session, lon: float, lat: float) -> list[Zone]:
    """Every active zone containing the point, jurisdiction first.

    The full chain, not just the winner, because an incident report has to be
    able to say "Bay of Bengal / Zone 03" and an alert escalation has to know
    what sits above the zone it started in.
    """
    from shapely.geometry import Point

    point = Point(lon, lat)
    hits = []
    for zone in db.query(Zone).filter(Zone.status == "active").all():
        if not _bbox_hit(zone, lon, lat):
            continue
        if _covers_point(zone, point):
            hits.append(zone)
    # Same tie-break as `zone_for_point`, so the chain and the routed zone can
    # never disagree about which division owns a boundary point.
    return sorted(hits, key=lambda z: (_depth(db, z), z.id))


def primary_officer(db: Session, zone: Zone,
                    escalate: bool = True) -> Optional[User]:
    """The officer answerable for `zone`.

    Walks up to the parent when the zone itself has no primary assignment and
    `escalate` is set. An unassigned zone with an assigned jurisdiction above
    it should reach a human rather than nobody; an unassigned zone with nothing
    above it returns None, and the alert is recorded as unrouted rather than
    quietly dropped.
    """
    seen: set[str] = set()
    cursor: Optional[Zone] = zone
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        row = (db.query(ZoneAssignment)
               .filter(ZoneAssignment.zone_id == cursor.id)
               .filter(ZoneAssignment.is_primary.is_(True))
               .first())
        if row is not None:
            user = db.get(User, row.user_id)
            if user is not None and user.active:
                return user
        if not escalate or not cursor.parent_id:
            return None
        cursor = db.get(Zone, cursor.parent_id)
    return None


def zone_officers(db: Session, zone_id: str) -> list[dict]:
    """Everyone assigned to one zone, primary first."""
    rows = (db.query(ZoneAssignment)
            .filter(ZoneAssignment.zone_id == zone_id)
            .order_by(ZoneAssignment.is_primary.desc(),
                      ZoneAssignment.assigned_utc.asc())
            .all())
    out = []
    for row in rows:
        user = db.get(User, row.user_id)
        if user is None:
            continue
        out.append({"user_id": user.id, "email": user.email,
                    "display_name": user.display_name, "role": user.role,
                    "active": bool(user.active),
                    "is_primary": bool(row.is_primary),
                    "assigned_utc": row.assigned_utc})
    return out


# --------------------------------------------------------------------------
# scope and authorisation
# --------------------------------------------------------------------------

def _descendants(db: Session, zone_id: str) -> set[str]:
    """`zone_id` and every zone beneath it."""
    out = {zone_id}
    frontier = [zone_id]
    while frontier:
        parent = frontier.pop()
        for child in db.query(Zone).filter(Zone.parent_id == parent).all():
            if child.id not in out:
                out.add(child.id)
                frontier.append(child.id)
    return out


def assigned_zone_ids(db: Session, user: User) -> set[str]:
    """Every zone `user` may act inside, assignments expanded downward.

    An officer assigned to a jurisdiction owns the operational zones inside it
    too -- otherwise assigning someone the Bay of Bengal would give them
    authority over no actual water.

    An empty set means "no zone authority", NOT "all zones". Callers must
    treat it that way; `assert_may_edit_zone` does.
    """
    ids: set[str] = set()
    for row in (db.query(ZoneAssignment)
                .filter(ZoneAssignment.user_id == user.id).all()):
        ids |= _descendants(db, row.zone_id)
    return ids


def may_edit_zone(db: Session, user: User, zone: Zone,
                  changing_geometry: bool) -> tuple[bool, str]:
    """Whether `user` may change `zone`, and why not when they may not.

    The reason string is returned rather than logged because it goes into the
    403 body. An operator who is told only "forbidden" files a bug; one who is
    told "Zone 03 is a protected jurisdiction boundary; only super_admin may
    move it" understands the system.
    """
    role = (user.role or "").strip()

    if role == "super_admin":
        return True, ""

    if zone.protected and changing_geometry:
        # The rule from spec section 16, and the only place it is enforced.
        # Note it gates GEOMETRY only: an admin may still rename a protected
        # zone or assign an officer to it. Moving the border is the act that
        # requires the higher authority, not touching the record.
        return False, (
            f"zone '{zone.id}' is a protected {zone.kind} boundary; only "
            f"super_admin may modify it. Operational divisions inside it can "
            f"be edited freely.")

    if role == "admin":
        return True, ""

    if role == "zone_officer":
        scope = assigned_zone_ids(db, user)
        if not scope:
            return False, ("you have no zone assignment, so you cannot modify "
                           "any boundary. Ask an administrator to assign you a "
                           "zone.")
        if zone.id not in scope:
            return False, (
                f"zone '{zone.id}' is outside your assignment. You may view "
                f"every zone in the system and act only within "
                f"{sorted(scope)}.")
        return True, ""

    return False, (f"role '{role}' may not modify operational zones")


def assert_may_edit_zone(db: Session, user: User, zone: Zone,
                         changing_geometry: bool) -> None:
    """`may_edit_zone`, as a 403."""
    ok, reason = may_edit_zone(db, user, zone, changing_geometry)
    if not ok:
        raise HTTPException(status_code=403, detail=reason)


def assert_may_create_under(db: Session, user: User,
                            parent: Optional[Zone]) -> None:
    """Whether `user` may add a zone beneath `parent`.

    Creating a top-level zone (no parent) declares a new jurisdiction, so it
    is super_admin only -- an operational administrator who could do that
    could route around the protection rule by declaring a jurisdiction of
    their own.
    """
    role = (user.role or "").strip()
    if role == "super_admin":
        return
    if parent is None:
        raise HTTPException(
            status_code=403,
            detail="creating a top-level zone declares a new jurisdiction; "
                   "only super_admin may do that. Create an operational zone "
                   "inside an existing jurisdiction instead.")
    if role == "admin":
        return
    if role == "zone_officer":
        scope = assigned_zone_ids(db, user)
        if parent.id not in scope:
            raise HTTPException(
                status_code=403,
                detail=f"zone '{parent.id}' is outside your assignment, so you "
                       f"may not create sub-zones inside it.")
        return
    raise HTTPException(status_code=403,
                        detail=f"role '{role}' may not create zones")


def may_read_zone_reports(db: Session, user: User,
                          zone_id: Optional[str]) -> bool:
    """Report access for one zone (spec section 20).

    The asymmetry is the design: an officer sees every zone, incident, vessel
    and drift analysis in the system for situational awareness, and reads
    REPORTS only for the zone they are answerable for. A report is a finding
    attributed to an officer; the global view is context.

    A zone_officer asked about an unzoned resource (`zone_id` None) is denied:
    a report that belongs to no zone belongs to no officer.
    """
    role = (user.role or "").strip()
    if role in ("super_admin", "admin", "reviewer", "auditor"):
        return True
    if role in ("investigator", "analyst"):
        return True
    if role == "zone_officer":
        if zone_id is None:
            return False
        return zone_id in assigned_zone_ids(db, user)
    return False


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def record_revision(db: Session, zone: Zone, change: str, actor: Optional[User],
                    geometry_before: Optional[str] = None,
                    geometry_after: Optional[str] = None,
                    area_before: Optional[float] = None,
                    area_after: Optional[float] = None,
                    reason: Optional[str] = None) -> ZoneRevision:
    """Append one row to a zone's history. Never updates an existing row."""
    row = ZoneRevision(
        zone_id=zone.id, revision=zone.revision, change=change,
        geometry_before=geometry_before, geometry_after=geometry_after,
        area_before_km2=area_before, area_after_km2=area_after,
        reason=reason,
        actor_id=getattr(actor, "id", None),
        actor_role=getattr(actor, "role", None),
        changed_utc=utcnow())
    db.add(row)
    return row


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------

def zone_dict(db: Session, zone: Zone, include_geometry: bool = True,
              include_counts: bool = False) -> dict:
    """One zone as the API renders it."""
    out: dict[str, Any] = {
        "id": zone.id,
        "name": zone.name,
        "kind": zone.kind,
        "parent_id": zone.parent_id,
        "jurisdiction": zone.jurisdiction,
        "protected": bool(zone.protected),
        "status": zone.status,
        "area_km2": zone.area_km2,
        "revision": zone.revision,
        "notes": zone.notes or "",
        "source": zone.source,
        "created_utc": zone.created_utc,
        "updated_utc": zone.updated_utc,
        "bbox": json.loads(zone.bbox_json) if zone.bbox_json else None,
        "officers": zone_officers(db, zone.id),
    }
    if include_geometry:
        out["geometry"] = json.loads(zone.geometry_json)
    if include_counts:
        from backend.models.db import Incident

        out["incident_count"] = (db.query(Incident)
                                 .filter(Incident.zone_id == zone.id).count())
        out["open_incident_count"] = (
            db.query(Incident)
            .filter(Incident.zone_id == zone.id)
            .filter(Incident.status.in_(("open", "investigating"))).count())
    return out


def zone_feature_collection(db: Session, zones: Iterable[Zone]) -> dict:
    """Zones as GeoJSON, for the globe and the 2D map to render identically."""
    features = []
    for zone in zones:
        props = zone_dict(db, zone, include_geometry=False)
        features.append({"type": "Feature",
                         "geometry": json.loads(zone.geometry_json),
                         "properties": props})
    return {"type": "FeatureCollection", "features": features}
