"""Operational zone CRUD, the boundary editor's backend, and point routing.

The visibility model here is the asymmetry from spec section 20, and it is
worth stating because it looks like a mistake until you read it twice: **every
authenticated role can LIST and READ every zone**, including a zone officer
looking at another country's water. Global situational awareness is the point
of a maritime picture; an officer who cannot see the traffic approaching their
boundary from outside it is worse at their job, not more secure.

What is scoped is WRITING. That lives in `services.zones`, is checked on every
mutating route here, and returns a 403 that says which zones the caller does
own. Nothing in this module decides authorisation on its own -- if you add a
route that changes a zone, call `assert_may_edit_zone` or the route-table
audit in `test_rbac_matrix` will fail you.

Optimistic concurrency: a geometry edit must carry the `revision` the client
drew against. Two officers splitting the same jurisdiction in two tabs is not
hypothetical, and last-write-wins would silently discard one of them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.authz import current_user, require_role
from backend.models.db import (ZONE_KINDS, ZONE_STATUSES, Incident, User, Zone,
                               ZoneAssignment, ZoneRevision, get_db, utcnow)
from backend.services import audit as audit_service
from backend.services import zones as zsvc

router = APIRouter(tags=["zones"])

# Slug shape for a zone id. Enforced because the id appears in URLs, in
# `zone_path` strings and in alert routing detail, and a free-text id with a
# slash in it would corrupt all three.
ZONE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")

# Only an administrator or above may reshape the operational map at all;
# `assert_may_edit_zone` then narrows an officer to their own assignment. Both
# checks are needed: this one keeps an auditor or analyst out of the editor
# entirely, and that one keeps Officer 02 out of Zone 03.
_may_draw = require_role("zone_officer")


# --------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------

class ZoneCreate(BaseModel):
    id: str
    name: str
    geometry: Any
    parent_id: Optional[str] = None
    kind: str = "operational"
    jurisdiction: Optional[str] = None
    notes: str = ""


class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    geometry: Optional[Any] = None
    # Required when `geometry` is present. Named `revision` rather than
    # `if_match` because that is the number the editor was shown.
    revision: Optional[int] = None
    reason: Optional[str] = None


class AssignmentBody(BaseModel):
    user_id: int
    is_primary: bool = True


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------

@router.get("/zones")
def list_zones(kind: Optional[str] = None,
               jurisdiction: Optional[str] = None,
               status: Optional[str] = None,
               parent_id: Optional[str] = None,
               geometry: bool = Query(False,
                                      description="include full polygons; the "
                                                  "list view does not need them"),
               counts: bool = Query(True),
               db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    """Every zone, for every role. See the module docstring on why.

    Geometry is off by default: the Zone Management table shows name, status,
    jurisdiction, officer and incident count, and shipping forty polygons to
    render five columns is the kind of payload that makes a dashboard feel slow
    for no reason. The map and globe use `/zones/geojson`.
    """
    q = db.query(Zone)
    if kind:
        if kind not in ZONE_KINDS:
            raise HTTPException(422, f"kind must be one of {list(ZONE_KINDS)}")
        q = q.filter(Zone.kind == kind)
    if jurisdiction:
        q = q.filter(Zone.jurisdiction == jurisdiction)
    if status:
        if status not in ZONE_STATUSES:
            raise HTTPException(422, f"status must be one of {list(ZONE_STATUSES)}")
        q = q.filter(Zone.status == status)
    if parent_id:
        q = q.filter(Zone.parent_id == parent_id)

    rows = q.order_by(Zone.kind.asc(), Zone.name.asc()).all()
    mine = zsvc.assigned_zone_ids(db, user)
    out = []
    for zone in rows:
        item = zsvc.zone_dict(db, zone, include_geometry=geometry,
                              include_counts=counts)
        # Stated per row rather than left to the client to work out from a
        # separate /zones/mine call, so a list and its permissions cannot
        # disagree on screen.
        item["mine"] = zone.id in mine
        item["can_edit"], item["cannot_edit_reason"] = zsvc.may_edit_zone(
            db, user, zone, changing_geometry=True)
        out.append(item)
    return {"zones": out, "count": len(out),
            "assigned_zone_ids": sorted(mine), "your_role": user.role}


@router.get("/zones/geojson")
def zones_geojson(kind: Optional[str] = None,
                  status: str = "active",
                  db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Zones as a GeoJSON FeatureCollection.

    One endpoint for both the 2D map and the globe, so the two views cannot
    drift apart on what a zone's boundary is -- which is exactly what happens
    when each renderer gets its own serialiser.
    """
    q = db.query(Zone)
    if kind:
        q = q.filter(Zone.kind == kind)
    if status:
        q = q.filter(Zone.status == status)
    fc = zsvc.zone_feature_collection(db, q.all())
    mine = zsvc.assigned_zone_ids(db, user)
    for feature in fc["features"]:
        feature["properties"]["mine"] = feature["properties"]["id"] in mine
    return fc


@router.get("/zones/mine")
def my_zones(db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    """The zones the caller is answerable for.

    For a super_admin or admin this is their explicit assignments, which is
    usually none -- deliberately. An administrator is not the responsible
    officer for every zone; they supervise the officers who are. The Officer
    Dashboard uses this, and an empty result is the honest "you have no zone"
    rather than a silent fallback to everything.
    """
    ids = zsvc.assigned_zone_ids(db, user)
    rows = db.query(Zone).filter(Zone.id.in_(ids)).all() if ids else []
    return {"zones": [zsvc.zone_dict(db, z, include_geometry=False,
                                     include_counts=True) for z in rows],
            "count": len(rows), "your_role": user.role,
            "report_scope": ("all" if user.role != "zone_officer"
                             else sorted(ids))}


@router.get("/zones/lookup")
def lookup_point(lon: float = Query(..., ge=-180, le=180),
                 lat: float = Query(..., ge=-90, le=90),
                 db: Session = Depends(get_db),
                 _user: User = Depends(current_user)):
    """Which zone owns a coordinate, and whose desk it lands on.

    This is the routing decision (spec section 18) exposed as a query, so the
    boundary editor can show an operator where a point would route BEFORE they
    save a boundary, and so an alert's routing can be re-checked by hand
    without reading the database.

    `zone: null` is a real answer -- open ocean outside every declared zone.
    """
    chain = zsvc.zones_covering_point(db, lon, lat)
    # The routed zone comes from `zone_for_point`, NOT from scanning the chain.
    # Both would usually agree, and on a point lying exactly on the line
    # between two divisions they would not -- and this endpoint exists so an
    # operator can preview routing, so showing a different answer than routing
    # will actually produce would be worse than showing nothing.
    zone = zsvc.zone_for_point(db, lon, lat)
    if zone is None and chain:
        # Covered by a jurisdiction but by no operational division: the theatre
        # owns it and no sub-zone does. Reported as the jurisdiction rather
        # than as "no zone", because the two mean different things
        # operationally.
        zone = chain[-1]

    officer = zsvc.primary_officer(db, zone) if zone is not None else None
    return {
        "lon": lon, "lat": lat,
        "zone": zsvc.zone_dict(db, zone, include_geometry=False) if zone else None,
        "zone_path": "/".join(z.id for z in chain) or None,
        "zone_names": [z.name for z in chain],
        "chain": [{"id": z.id, "name": z.name, "kind": z.kind,
                   "protected": bool(z.protected)} for z in chain],
        "responsible_officer": (
            {"user_id": officer.id, "email": officer.email,
             "display_name": officer.display_name, "role": officer.role}
            if officer else None),
        "routing": _routing_label(db, zone, officer),
    }


def _routing_label(db: Session, zone: Optional[Zone],
                   officer: Optional[User]) -> str:
    """How an alert at this point would be routed. See `Alert.routing`."""
    if zone is None:
        return "unrouted"
    if officer is None:
        return "unrouted"
    direct = (db.query(ZoneAssignment)
              .filter(ZoneAssignment.zone_id == zone.id)
              .filter(ZoneAssignment.user_id == officer.id)
              .first())
    return "zone" if direct is not None else "escalated"


@router.get("/zones/{zone_id}")
def get_zone(zone_id: str, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(404, f"no zone '{zone_id}'")
    out = zsvc.zone_dict(db, zone, include_geometry=True, include_counts=True)
    out["can_edit"], out["cannot_edit_reason"] = zsvc.may_edit_zone(
        db, user, zone, changing_geometry=True)
    out["children"] = [
        zsvc.zone_dict(db, child, include_geometry=False, include_counts=True)
        for child in db.query(Zone).filter(Zone.parent_id == zone_id).all()]
    out["can_read_reports"] = zsvc.may_read_zone_reports(db, user, zone_id)
    return out


@router.get("/zones/{zone_id}/revisions")
def zone_revisions(zone_id: str, limit: int = Query(50, le=500),
                   db: Session = Depends(get_db),
                   _user: User = Depends(current_user)):
    """Who changed this boundary, when, and by how much.

    Readable by any authenticated role. A boundary edit changes who is
    accountable for a stretch of ocean; hiding the history of that from an
    auditor would defeat the reason the history is kept.
    """
    if db.get(Zone, zone_id) is None:
        raise HTTPException(404, f"no zone '{zone_id}'")
    rows = (db.query(ZoneRevision)
            .filter(ZoneRevision.zone_id == zone_id)
            .order_by(ZoneRevision.changed_utc.desc())
            .limit(limit).all())
    out = []
    for row in rows:
        actor = db.get(User, row.actor_id) if row.actor_id else None
        out.append({
            "id": row.id, "revision": row.revision, "change": row.change,
            "changed_utc": row.changed_utc, "reason": row.reason,
            "actor": (actor.email if actor else None),
            "actor_role": row.actor_role,
            "area_before_km2": row.area_before_km2,
            "area_after_km2": row.area_after_km2,
            "area_delta_km2": (
                round(row.area_after_km2 - row.area_before_km2, 4)
                if row.area_after_km2 is not None
                and row.area_before_km2 is not None else None),
            "geometry_changed": row.geometry_after is not None,
        })
    return {"zone_id": zone_id, "revisions": out, "count": len(out)}


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------

def _resolve_parent(db: Session, parent_id: Optional[str]) -> Optional[Zone]:
    if not parent_id:
        return None
    parent = db.get(Zone, parent_id)
    if parent is None:
        raise HTTPException(422, f"parent zone '{parent_id}' does not exist")
    return parent


@router.post("/zones", status_code=201,
             dependencies=[Depends(_may_draw)])
def create_zone(body: ZoneCreate, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Draw a new zone.

    A top-level zone (no parent) is a jurisdiction declaration and is
    super_admin only -- see `assert_may_create_under`. Everything else must
    name a parent and must fit inside it.
    """
    if not ZONE_ID_RE.match(body.id or ""):
        raise HTTPException(
            422, "zone id must be a lowercase slug, 3-64 chars, letters, "
                 "digits and hyphens only (e.g. 'zone-bob-03')")
    if db.get(Zone, body.id) is not None:
        raise HTTPException(409, f"zone '{body.id}' already exists")
    if body.kind not in ZONE_KINDS:
        raise HTTPException(422, f"kind must be one of {list(ZONE_KINDS)}")

    parent = _resolve_parent(db, body.parent_id)
    zsvc.assert_may_create_under(db, user, parent)

    try:
        geom = zsvc.normalise_geometry(body.geometry)
    except zsvc.ZoneGeometryError as exc:
        raise HTTPException(422, str(exc)) from exc

    if parent is not None:
        from shapely.geometry import shape

        zsvc.assert_inside_parent(shape(json.loads(geom.geometry_json)), parent)

    # Inherited, not accepted from the body, when a parent exists: an
    # operational zone inside Indian waters cannot declare itself Sri Lankan.
    jurisdiction = (parent.jurisdiction if parent is not None
                    else (body.jurisdiction or None))

    zone = Zone(
        id=body.id, name=body.name.strip() or body.id, kind=body.kind,
        parent_id=(parent.id if parent else None),
        geometry_json=geom.geometry_json,
        bbox_json=json.dumps(geom.bbox, separators=(",", ":")),
        jurisdiction=jurisdiction,
        # A jurisdiction's own outer boundary is protected by definition. It is
        # derived from `kind` rather than accepted from the body so a client
        # cannot create an unprotected jurisdiction.
        protected=(body.kind == "jurisdiction"),
        status="active", area_km2=geom.area_km2, notes=body.notes or "",
        revision=1, source="api", created_by=user.id)
    db.add(zone)
    db.flush()
    zsvc.record_revision(db, zone, "created", user,
                         geometry_after=geom.geometry_json,
                         area_after=geom.area_km2,
                         reason="zone created")
    audit_service.record(
        db, "zone.create", request=request, resource=f"zone:{zone.id}",
        detail=json.dumps({"name": zone.name, "kind": zone.kind,
                           "parent_id": zone.parent_id,
                           "area_km2": zone.area_km2,
                           "jurisdiction": zone.jurisdiction}),
        commit=False)
    db.commit()
    return zsvc.zone_dict(db, zone, include_counts=True)


@router.patch("/zones/{zone_id}", dependencies=[Depends(_may_draw)])
def update_zone(zone_id: str, body: ZoneUpdate, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Rename, restatus, or reshape a zone.

    Geometry and metadata are authorised separately: a protected jurisdiction
    can be renamed by an admin and reshaped only by a super_admin, because the
    thing being protected is the border, not the label.
    """
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(404, f"no zone '{zone_id}'")

    changing_geometry = body.geometry is not None
    zsvc.assert_may_edit_zone(db, user, zone, changing_geometry)

    changed: dict[str, Any] = {}

    if body.name is not None and body.name.strip():
        changed["name"] = {"from": zone.name, "to": body.name.strip()}
        zone.name = body.name.strip()
    if body.notes is not None:
        zone.notes = body.notes
        changed["notes"] = True
    if body.status is not None:
        if body.status not in ZONE_STATUSES:
            raise HTTPException(422, f"status must be one of {list(ZONE_STATUSES)}")
        if body.status != zone.status:
            changed["status"] = {"from": zone.status, "to": body.status}
            zone.status = body.status

    if changing_geometry:
        if body.revision is None:
            raise HTTPException(
                422, "a geometry change must carry the `revision` it was drawn "
                     "against, so a concurrent edit is rejected rather than "
                     "silently overwritten")
        if body.revision != zone.revision:
            raise HTTPException(
                409, f"zone '{zone_id}' is at revision {zone.revision}; you "
                     f"drew against {body.revision}. Someone else has moved "
                     f"this boundary -- reload and re-apply your change.")
        try:
            geom = zsvc.normalise_geometry(body.geometry)
        except zsvc.ZoneGeometryError as exc:
            raise HTTPException(422, str(exc)) from exc

        from shapely.geometry import shape

        new_poly = shape(json.loads(geom.geometry_json))

        parent = db.get(Zone, zone.parent_id) if zone.parent_id else None
        if parent is not None:
            zsvc.assert_inside_parent(new_poly, parent)

        # Shrinking a zone must not orphan a sub-zone. Without this an officer
        # could redraw Zone 03 away from its own sub-zone and leave a polygon
        # whose parent no longer contains it -- which would then route points
        # to a zone the parent chain says is elsewhere.
        for child in db.query(Zone).filter(Zone.parent_id == zone.id).all():
            spill = zsvc.outside_fraction(zsvc.zone_polygon(child), new_poly)
            if spill > zsvc.CONTAINMENT_TOLERANCE:
                raise HTTPException(
                    422, f"the new boundary would leave sub-zone '{child.id}' "
                         f"({child.name}) {spill * 100:.2f}% outside it. Move "
                         f"or delete the sub-zone first.")

        before_geom, before_area = zone.geometry_json, zone.area_km2
        zone.geometry_json = geom.geometry_json
        zone.bbox_json = json.dumps(geom.bbox, separators=(",", ":"))
        zone.area_km2 = geom.area_km2
        zone.revision = (zone.revision or 1) + 1
        zsvc.record_revision(db, zone, "geometry", user,
                             geometry_before=before_geom,
                             geometry_after=geom.geometry_json,
                             area_before=before_area,
                             area_after=geom.area_km2,
                             reason=body.reason)
        audit_service.record(
            db, "zone.geometry", request=request, resource=f"zone:{zone.id}",
            detail=json.dumps({"revision": zone.revision,
                               "area_before_km2": before_area,
                               "area_after_km2": geom.area_km2,
                               "protected": bool(zone.protected),
                               "reason": body.reason}),
            commit=False)
    elif changed:
        zsvc.record_revision(db, zone, "metadata", user,
                             reason=body.reason or json.dumps(changed,
                                                              default=str))
        audit_service.record(
            db, "zone.change", request=request, resource=f"zone:{zone.id}",
            detail=json.dumps(changed, default=str), commit=False)

    zone.updated_utc = utcnow()
    db.commit()
    out = zsvc.zone_dict(db, zone, include_counts=True)
    out["changed"] = sorted(changed) + (["geometry"] if changing_geometry else [])
    return out


@router.delete("/zones/{zone_id}", dependencies=[Depends(require_role("admin"))])
def delete_zone(zone_id: str, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Remove a zone, if nothing depends on it.

    Refused rather than cascaded. An incident's `zone_id` records which desk
    it was routed to, and deleting the zone out from under it would rewrite
    that history -- so a zone with incidents is deactivated, not deleted, and
    the error says so.
    """
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(404, f"no zone '{zone_id}'")
    if zone.protected and user.role != "super_admin":
        raise HTTPException(
            403, f"zone '{zone_id}' is a protected {zone.kind} boundary; only "
                 f"super_admin may delete it")

    children = db.query(Zone).filter(Zone.parent_id == zone_id).count()
    if children:
        raise HTTPException(
            409, f"zone '{zone_id}' has {children} sub-zone(s); delete or "
                 f"re-parent them first")
    incidents = db.query(Incident).filter(Incident.zone_id == zone_id).count()
    if incidents:
        raise HTTPException(
            409, f"zone '{zone_id}' is referenced by {incidents} incident(s), "
                 f"which record it as the desk they were routed to. Set its "
                 f"status to 'inactive' instead -- deleting it would rewrite "
                 f"that history.")

    (db.query(ZoneAssignment)
     .filter(ZoneAssignment.zone_id == zone_id).delete())
    audit_service.record(
        db, "zone.delete", request=request, resource=f"zone:{zone_id}",
        detail=json.dumps({"name": zone.name, "kind": zone.kind,
                           "area_km2": zone.area_km2}), commit=False)
    db.delete(zone)
    db.commit()
    # The revision rows are deliberately left behind. They name a zone that no
    # longer exists, which is the correct record of a zone that was deleted.
    return {"deleted": zone_id, "revisions_retained": True}


# --------------------------------------------------------------------------
# officer assignment
# --------------------------------------------------------------------------

@router.post("/zones/{zone_id}/assignments", status_code=201,
             dependencies=[Depends(require_role("admin"))])
def assign_officer(zone_id: str, body: AssignmentBody, request: Request,
                   db: Session = Depends(get_db),
                   _user: User = Depends(current_user)):
    """Make a user answerable for a zone.

    Assignment is an administrator action, not an officer one -- an officer who
    could assign themselves a zone would have defeated the scoping. Any role
    can be assigned: an investigator covering a zone during a handover is
    legitimate, and restricting this to `zone_officer` would force real
    operations to invent a second account.
    """
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(404, f"no zone '{zone_id}'")
    target = db.get(User, body.user_id)
    if target is None:
        raise HTTPException(404, f"no user {body.user_id}")
    if not target.active:
        raise HTTPException(
            422, f"{target.email} is deactivated; an inactive account cannot "
                 f"be the responsible officer for a zone")

    existing = (db.query(ZoneAssignment)
                .filter(ZoneAssignment.zone_id == zone_id)
                .filter(ZoneAssignment.user_id == body.user_id).first())

    if body.is_primary:
        # One primary per zone. Demoting the incumbent rather than rejecting
        # the request is deliberate: "assign Zone 03 to Officer 07" is an
        # unambiguous instruction, and making the caller unassign first would
        # leave the zone briefly unrouted.
        for row in (db.query(ZoneAssignment)
                    .filter(ZoneAssignment.zone_id == zone_id)
                    .filter(ZoneAssignment.is_primary.is_(True)).all()):
            if row.user_id != body.user_id:
                row.is_primary = False

    if existing is not None:
        existing.is_primary = body.is_primary
        existing.assigned_utc = utcnow()
        existing.assigned_by = _user.id
    else:
        db.add(ZoneAssignment(zone_id=zone_id, user_id=body.user_id,
                              is_primary=body.is_primary,
                              assigned_by=_user.id))

    zsvc.record_revision(db, zone, "assignment", _user,
                         reason=f"{'primary' if body.is_primary else 'deputy'} "
                                f"-> {target.email}")
    audit_service.record(
        db, "zone.assign", request=request, resource=f"zone:{zone_id}",
        detail=json.dumps({"user_id": target.id, "email": target.email,
                           "role": target.role,
                           "is_primary": body.is_primary}), commit=False)
    db.commit()
    return {"zone_id": zone_id, "officers": zsvc.zone_officers(db, zone_id)}


@router.delete("/zones/{zone_id}/assignments/{user_id}",
               dependencies=[Depends(require_role("admin"))])
def unassign_officer(zone_id: str, user_id: int, request: Request,
                     db: Session = Depends(get_db),
                     _user: User = Depends(current_user)):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(404, f"no zone '{zone_id}'")
    row = (db.query(ZoneAssignment)
           .filter(ZoneAssignment.zone_id == zone_id)
           .filter(ZoneAssignment.user_id == user_id).first())
    if row is None:
        raise HTTPException(404, f"user {user_id} is not assigned to '{zone_id}'")
    was_primary = bool(row.is_primary)
    db.delete(row)
    zsvc.record_revision(db, zone, "assignment", _user,
                         reason=f"unassigned user {user_id}")
    audit_service.record(
        db, "zone.unassign", request=request, resource=f"zone:{zone_id}",
        detail=json.dumps({"user_id": user_id, "was_primary": was_primary}),
        commit=False)
    db.commit()
    remaining = zsvc.zone_officers(db, zone_id)
    return {"zone_id": zone_id, "officers": remaining,
            # Said out loud because it changes where future alerts go, and an
            # operator who removed the last officer should be told the zone is
            # now routing by escalation rather than discovering it from a queue.
            "now_unrouted": was_primary and not any(
                o["is_primary"] for o in remaining)}
