"""Incidents: the case file a run is evidence for.

A spill event can span several scenes and several runs, so the run cannot be
the unit of accountability -- it is immutable evidence, and evidence does not
have a status. The incident is the mutable case around it.

The lifecycle is the point of this module, not the CRUD. `attributed` and
`closed` are conclusions about who polluted, so only a reviewer or an admin may
set them (master plan section 8), and every transition is audited with the
account that made it. An investigator can gather evidence and move a case to
`investigating`; concluding it is someone else's decision.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.authz import current_user, require_role
from backend.models.db import (INCIDENT_REVIEWER_STATUSES, INCIDENT_STATUSES,
                               Incident, Investigation, Run, User, get_db,
                               utcnow)
from backend.services import audit as audit_service

router = APIRouter()

SORTABLE = {
    "detected_utc": Incident.detected_utc,
    "created_utc": Incident.created_utc,
    "updated_utc": Incident.updated_utc,
    "status": Incident.status,
    "title": Incident.title,
}


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    geometry: Optional[Dict[str, Any]] = None
    detected_utc: Optional[datetime] = None
    region: Optional[str] = Field(default=None, max_length=120)
    assignee_id: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("geometry")
    @classmethod
    def _geojson(cls, v):
        if v is None:
            return v
        if v.get("type") not in ("Point", "Polygon", "MultiPolygon"):
            raise ValueError("geometry must be a GeoJSON Point, Polygon or MultiPolygon")
        if "coordinates" not in v:
            raise ValueError("geometry must carry coordinates")
        return v


class IncidentPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[str] = None
    region: Optional[str] = Field(default=None, max_length=120)
    assignee_id: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _known(cls, v):
        if v is not None and v not in INCIDENT_STATUSES:
            raise ValueError(f"status must be one of {list(INCIDENT_STATUSES)}")
        return v


def _dict(inc: Incident, runs: int = 0, investigations: int = 0) -> dict:
    return {
        "id": inc.id,
        "title": inc.title,
        "geometry": json.loads(inc.geometry_json) if inc.geometry_json else None,
        "detected_utc": inc.detected_utc,
        "status": inc.status,
        "region": inc.region,
        "assignee_id": inc.assignee_id,
        "notes": inc.notes,
        "created_utc": inc.created_utc,
        "updated_utc": inc.updated_utc,
        "investigations": investigations,
        "runs": runs,
        # --- zone routing and provenance (spec sections 17, 18) ---------
        "zone_id": inc.zone_id,
        "zone_path": inc.zone_path,
        # "auto" for a case the pipeline opened, "manual" for one a human
        # promoted, null for rows predating the distinction. It says whether a
        # person looked at this before it became a case, which is provenance,
        # not bookkeeping.
        "origin": inc.origin,
        "severity": inc.severity,
        "detection_confidence": inc.detection_confidence,
        "area_km2": inc.area_km2,
        "source_run_id": inc.source_run_id,
        "scene_id": inc.scene_id,
    }


def _next_id(db: Session) -> str:
    """`INC-<year>-<seq>`, sequential within the year.

    Readable in a report and sortable by eye, which a UUID is not -- these
    identifiers get quoted in documents people read.
    """
    year = utcnow().year
    prefix = f"INC-{year}-"
    last = (db.query(Incident.id)
            .filter(Incident.id.like(f"{prefix}%"))
            .order_by(Incident.id.desc()).first())
    seq = 1
    if last:
        try:
            seq = int(str(last[0]).rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            seq = db.query(Incident).filter(Incident.id.like(f"{prefix}%")).count() + 1
    return f"{prefix}{seq:03d}"


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@router.post("/incidents", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
def create_incident(request: Request, body: IncidentCreate,
                    db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    if body.assignee_id is not None and db.get(User, body.assignee_id) is None:
        raise HTTPException(400, f"no user with id {body.assignee_id}")

    inc = Incident(
        id=_next_id(db), title=body.title,
        geometry_json=json.dumps(body.geometry) if body.geometry else None,
        detected_utc=body.detected_utc, region=body.region,
        assignee_id=body.assignee_id, notes=body.notes,
        status="open", created_by=user.id,
    )
    db.add(inc)
    audit_service.record(db, "incident.create", request=request, resource=inc.id,
                         detail=json.dumps({"title": inc.title, "region": inc.region}),
                         commit=False)
    db.commit()
    return _dict(inc)


@router.get("/incidents")
def list_incidents(db: Session = Depends(get_db),
                   status: Optional[str] = None,
                   region: Optional[str] = None,
                   assignee: Optional[int] = None,
                   q: Optional[str] = None,
                   since: Optional[datetime] = Query(None, alias="from"),
                   until: Optional[datetime] = Query(None, alias="to"),
                   sort: str = "detected_utc",
                   order: str = Query("desc", pattern="^(asc|desc)$"),
                   offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=500)):
    """Filtered, sorted, paginated register."""
    query = db.query(Incident)
    if status:
        query = query.filter(Incident.status == status)
    if region:
        query = query.filter(Incident.region == region)
    if assignee is not None:
        query = query.filter(Incident.assignee_id == assignee)
    if q:
        like = f"%{q}%"
        query = query.filter(Incident.title.ilike(like) | Incident.id.ilike(like))
    if since:
        query = query.filter(Incident.detected_utc >= since)
    if until:
        query = query.filter(Incident.detected_utc <= until)

    total = query.count()
    column = SORTABLE.get(sort, Incident.detected_utc)
    query = query.order_by(column.desc() if order == "desc" else column.asc())
    rows: List[Incident] = query.offset(offset).limit(limit).all()

    # Counted in two grouped queries rather than per row, so a page of 50 does
    # not issue 100 follow-up selects.
    ids = [r.id for r in rows]
    run_counts = dict(db.query(Run.incident_id, func.count(Run.id))
                      .filter(Run.incident_id.in_(ids))
                      .group_by(Run.incident_id).all()) if ids else {}
    inv_counts = dict(db.query(Investigation.incident_id, func.count(Investigation.id))
                      .filter(Investigation.incident_id.in_(ids))
                      .group_by(Investigation.incident_id).all()) if ids else {}

    return {
        "total": total, "offset": offset, "limit": limit,
        "statuses": list(INCIDENT_STATUSES),
        "items": [_dict(r, run_counts.get(r.id, 0), inv_counts.get(r.id, 0))
                  for r in rows],
    }


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, f"no incident {incident_id}")

    investigations = (db.query(Investigation)
                      .filter(Investigation.incident_id == incident_id)
                      .order_by(Investigation.created_utc.desc()).all())
    runs = (db.query(Run).filter(Run.incident_id == incident_id)
            .order_by(Run.started_utc.desc()).all())

    payload = _dict(inc, len(runs), len(investigations))
    payload["investigation_list"] = [
        {"id": i.id, "name": i.name, "scene_id": i.scene_id,
         "created_utc": i.created_utc} for i in investigations]
    payload["run_list"] = [
        {"id": r.id, "status": r.status, "scene_id": r.scene_id,
         "started_utc": r.started_utc, "stages_real": r.stages_real,
         "stages_total": r.stages_total, "stages_mock": r.stages_mock}
        for r in runs]
    return payload


@router.patch("/incidents/{incident_id}")
def update_incident(request: Request, incident_id: str, body: IncidentPatch,
                    db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """Edit a case. Concluding statuses are reviewer/admin only.

    The role check lives here rather than on the route because it depends on
    the VALUE being set: an investigator may move a case to `investigating`
    but not to `attributed`. A route-level guard cannot see the body.
    """
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, f"no incident {incident_id}")

    changes: Dict[str, Any] = {}

    if body.status is not None and body.status != inc.status:
        if (body.status in INCIDENT_REVIEWER_STATUSES
                and user.role not in ("reviewer", "admin")):
            raise HTTPException(
                403, f"role '{user.role}' may not set status '{body.status}'; "
                     f"concluding a case is a reviewer decision")
        changes["status"] = [inc.status, body.status]
        inc.status = body.status

    if body.assignee_id is not None and body.assignee_id != inc.assignee_id:
        if db.get(User, body.assignee_id) is None:
            raise HTTPException(400, f"no user with id {body.assignee_id}")
        changes["assignee_id"] = [inc.assignee_id, body.assignee_id]
        inc.assignee_id = body.assignee_id

    for attr in ("title", "region", "notes"):
        value = getattr(body, attr)
        if value is not None and value != getattr(inc, attr):
            changes[attr] = [getattr(inc, attr), value]
            setattr(inc, attr, value)

    if changes:
        inc.updated_utc = utcnow()
        # `incident.status` when the lifecycle moved, so the status history is
        # filterable on its own; otherwise the generic edit event.
        action = "incident.status" if "status" in changes else "incident.update"
        audit_service.record(db, action, request=request, resource=inc.id,
                             detail=json.dumps(changes, default=str), commit=False)
    db.commit()
    return _dict(inc)


@router.get("/incidents/auto/preview/{run_id}")
def preview_auto_incident(run_id: str, _user: User = Depends(current_user)):
    """What the validation gate would decide for this run, without acting.

    Readable by any authenticated role, and the reason it exists: a gate whose
    decision cannot be inspected before it fires is a gate nobody trusts. The
    verdict lists its reasons on a pass as well as a failure.
    """
    from backend.services import incident_auto

    return incident_auto.validate_detection(run_id).as_dict()


@router.post("/incidents/auto/{run_id}", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
def create_auto_incident(request: Request, run_id: str,
                         force: bool = Query(
                             False,
                             description="open the case even though the gate "
                                         "refused; the refusal is recorded on "
                                         "the incident either way"),
                         db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    """Run the automatic path by hand.

    The pipeline calls the same function when a run seals, so this is a retry
    for a run that completed while the zone table was empty, or before the
    feature existed -- not a second implementation.

    `force` exists because a human may legitimately overrule the gate. It is a
    query parameter rather than a silent default, and the verdict that refused
    is stored on the incident, so a forced case never looks like a validated
    one.
    """
    from backend.services import incident_auto

    outcome = incident_auto.create_incident_from_run(
        db, run_id, request=request, actor=user, force=force)
    if not outcome.get("created"):
        # 409, not 500: the gate refusing is a decision, and the body carries
        # the reasons so a UI can show them rather than "failed".
        raise HTTPException(409, detail=outcome)
    return outcome


@router.post("/incidents/backfill-zones",
             dependencies=[Depends(require_role("admin"))])
def backfill_incident_zones(request: Request, db: Session = Depends(get_db),
                            _user: User = Depends(current_user)):
    """Stamp zones onto incidents that predate the zone model.

    Explicit rather than automatic on read. It resolves against the CURRENT
    boundaries, and doing that lazily would silently re-attribute closed cases
    whenever somebody moved a line on a map.
    """
    from backend.services import incident_auto

    updated, skipped = [], 0
    for inc in db.query(Incident).filter(Incident.zone_id.is_(None)).all():
        zone_id = incident_auto.backfill_zone(db, inc)
        if zone_id:
            updated.append({"incident_id": inc.id, "zone_id": zone_id})
        else:
            skipped += 1
    if updated:
        audit_service.record(
            db, "incident.status", request=request, resource="incidents",
            detail=json.dumps({"backfilled_zones": len(updated)}), commit=False)
    db.commit()
    return {"updated": updated, "count": len(updated),
            "skipped": skipped,
            "note": ("skipped incidents either have no geometry or fall "
                     "outside every declared zone; neither is backfilled with "
                     "a nearest guess")}


@router.post("/incidents/from_run/{run_id}", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
def promote_run(request: Request, run_id: str, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Open a case from a run that found something.

    Geometry is seeded from the run's own slick centroid rather than typed in
    again, so the case is anchored to what the pipeline actually detected. If
    the artefact is unreadable the incident is still created, without geometry
    -- an incident with no shape is honest; one with an invented shape is not.
    """
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, f"no run {run_id}")
    if run.incident_id:
        raise HTTPException(409, f"run {run_id} already belongs to {run.incident_id}")

    geometry, detected = None, run.started_utc
    try:
        from backend.core.config import get_settings

        slick_path = get_settings().runs_root / run_id / "slick.geojson"
        if slick_path.exists():
            slick = json.loads(slick_path.read_text(encoding="utf-8"))
            feature = (slick.get("features") or [None])[0]
            if feature:
                centroid = feature["properties"].get("centroid")
                if centroid:
                    geometry = {"type": "Point", "coordinates": list(centroid)}
            acquired = (slick.get("metadata") or {}).get("acquired_utc")
            if acquired:
                detected = acquired
    except Exception:                              # noqa: BLE001 - shape is optional
        geometry = None

    inc = Incident(
        id=_next_id(db),
        title=f"Incident from {run_id}",
        geometry_json=json.dumps(geometry) if geometry else None,
        detected_utc=detected if isinstance(detected, datetime) else None,
        status="investigating", created_by=user.id,
    )
    db.add(inc)
    db.flush()

    run.incident_id = inc.id
    if run.investigation_id:
        inv = db.get(Investigation, run.investigation_id)
        if inv is not None and inv.incident_id is None:
            inv.incident_id = inc.id

    audit_service.record(db, "incident.create", request=request, resource=inc.id,
                         detail=json.dumps({"promoted_from_run": run_id,
                                            "geometry": bool(geometry)}),
                         commit=False)
    db.commit()
    return _dict(inc, runs=1, investigations=1 if run.investigation_id else 0)
