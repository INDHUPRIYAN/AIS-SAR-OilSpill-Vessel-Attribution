"""Scheduler / AOI watcher API -- STAGE 0 (design doc v2 §4, §21, §27·8).

    /api/aois              the registry, merged with each AOI's live watch state
    /api/aois/{id}         one AOI
    /api/aois/poll         force a sweep now (what the demo presses)
    /api/aois/{id}/poll    force a sweep of one AOI
    POST/PATCH/DELETE      register, edit and retire an AOI

The registry began as a config file (§21). It is now a table, with
``config/aois.yaml`` migrated in once on first use and each row recording where
it came from. The reason is narrow and practical: an operator drawing a box on
a map cannot edit a YAML file on the server, and two operators editing one file
cannot merge. The YAML remains a perfectly good way to define AOIs for a fixed
deployment, and nothing about it was deleted.

Alongside the definitions the API exposes the *state* of watching -- when each
AOI was last polled, what it last saw, and whether its provider chain is
healthy -- which is what the monitoring page and the demo need.

``/aois/poll`` exists mainly for the demo: Sentinel-1's revisit is measured in
days, so "wait for a pass" is not a thing you can show on stage. Pressing poll
and watching an investigation open by itself is.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.core.authz import require_role
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from pydantic import BaseModel, Field

from backend.models.db import Aoi, AoiWatch, get_db
from backend.services import audit as audit_service
from backend.services.scheduler import registry
from backend.services.scheduler.aoi import AOIConfigError, load_aois
from backend.services.scheduler.geometry import (AoiGeometryError,
                                                 validate_aoi_geometry)

router = APIRouter()


def build_watcher():
    """Construct the watcher with the real search chain and run starter.

    Wiring lives here rather than in the watcher so that module stays testable
    without a network or a pipeline -- both dependencies are injected.
    """
    from backend.services.scheduler.watcher import AOIWatcher

    return AOIWatcher(start_run=_start_run_for_investigation)


def _start_run_for_investigation(investigation_id: str, aoi, scene) -> Optional[str]:
    """Start the pipeline for an investigation the watcher just opened.

    Reuses the same code path as the manual "Run" button, so a scheduled run and
    an analyst-triggered run are the same run -- one state machine, one manifest
    format, one set of provenance rules. A scheduler with its own private
    pipeline would be the fastest way to make the two disagree.
    """
    import threading
    import uuid
    from datetime import datetime, timezone

    from backend.api.routes import _execute_run, _running, _run_lock
    from backend.models.db import Run, SessionLocal

    run_id = f"{investigation_id}-{uuid.uuid4().hex[:6]}"
    scene_path = getattr(scene, "file_path", None)
    scene_id = getattr(scene, "scene_id", None)

    with SessionLocal() as db:
        db.add(Run(id=run_id, investigation_id=investigation_id,
                   status="pending", scene_id=scene_id,
                   registry_source="api"))
        db.commit()

    with _run_lock:
        _running.add(run_id)
    threading.Thread(
        target=_execute_run,
        args=(run_id, investigation_id, scene_path, None, "auto"),
        daemon=True).start()
    return run_id


def _merge(aoi, state: Optional[AoiWatch]) -> Dict[str, Any]:
    """One AOI as the UI wants it: definition plus live watch state."""
    payload = aoi.to_dict()
    payload["watch"] = {
        "last_polled_utc": state.last_polled_utc if state else None,
        "last_scene_id": state.last_scene_id if state else None,
        "last_scene_time_utc": state.last_scene_time_utc if state else None,
        "status": (state.status if state else None) or "UNKNOWN",
        "last_error_class": (state.last_error_class if state else None) or "NONE",
        "last_error": state.last_error if state else None,
        "consecutive_failures": (state.consecutive_failures if state else 0) or 0,
        "polls": (state.polls if state else 0) or 0,
        "scenes_seen": (state.scenes_seen if state else 0) or 0,
        "investigations_opened": (state.investigations_opened if state else 0) or 0,
    }
    return payload


def _registry(db=None):
    """Every defined AOI.

    Reads the table when a session is available and falls back to the YAML
    when it is not, so the watcher -- which runs without a request scope --
    keeps working exactly as before.
    """
    if db is not None:
        return registry.list_aois(db)
    try:
        return load_aois()
    except AOIConfigError as exc:
        # 503, not 500: the service is fine, its configuration is not, and the
        # message says exactly which AOI and why.
        raise HTTPException(503, f"AOI registry unusable: {exc}")


@router.get("/aois")
def list_aois(db: Session = Depends(get_db), enabled_only: bool = False):
    """The AOI registry with each AOI's watch state.

    Disabled AOIs are listed by default so an operator can see that the off
    switch is set, rather than wondering where an AOI went.
    """
    rows = registry.list_rows(db, enabled_only=enabled_only)
    out = []
    for row in rows:
        merged = _merge(registry._to_aoi(row), db.get(AoiWatch, row.id))
        merged["geometry"] = json.loads(row.geometry_json) if row.geometry_json else None
        merged["source"] = row.source
        out.append(merged)
    return out


@router.get("/aois/{aoi_id}")
def get_aoi_detail(aoi_id: str, db: Session = Depends(get_db)):
    row = registry.get_row(db, aoi_id)
    if row is None:
        raise HTTPException(404, f"no AOI '{aoi_id}' in the registry")
    merged = _merge(registry._to_aoi(row), db.get(AoiWatch, aoi_id))
    merged["geometry"] = json.loads(row.geometry_json) if row.geometry_json else None
    merged["source"] = row.source
    return merged


@router.post("/aois/poll",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def poll_now(dry_run: bool = Query(
        False, description="search and report, but open no investigations")):
    """Force one sweep over every enabled AOI, ignoring poll_minutes.

    ``dry_run`` searches and reports what it *would* open without opening
    anything. Use it to confirm an AOI is wired to a live provider before
    letting it start pipeline runs unattended.
    """
    return build_watcher().tick(dry_run=dry_run).to_dict()


@router.post("/aois/{aoi_id}/poll",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def poll_one(aoi_id: str):
    """Force a sweep of a single AOI, ignoring its poll_minutes and enabled flag.

    The enabled flag is deliberately ignored here: an operator explicitly
    naming one AOI is asking for it, which is different from the unattended
    loop deciding to poll something switched off.
    """
    aoi = next((a for a in _registry() if a.id == aoi_id), None)
    if aoi is None:
        raise HTTPException(404, f"no AOI '{aoi_id}' in the registry")

    from backend.models.db import SessionLocal
    from backend.services.scheduler.watcher import AOIWatcher, WatchTick
    from backend.models.db import utcnow

    watcher = build_watcher()
    tick = WatchTick(started_utc=utcnow())
    tick.polled.append(aoi.id)
    with SessionLocal() as db:
        watcher.poll_aoi(aoi, db, tick=tick)
    return tick.to_dict()


# --------------------------------------------------------------------------
# registering an AOI (PROMPT 13)
# --------------------------------------------------------------------------


class AoiCreate(BaseModel):
    """A new watched area.

    Either `geometry` (a GeoJSON Polygon, which is what a map draw tool
    produces) or `bbox` must be given. Geometry wins when both are present and
    the bbox is derived from it, because a bbox that disagrees with the drawn
    shape is a bug waiting to be discovered at search time.
    """

    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9\-_]*$")
    name: str = Field(min_length=1, max_length=200)
    geometry: Optional[Dict[str, Any]] = None
    bbox: Optional[List[float]] = None
    ais_region: Optional[str] = Field(
        default=None,
        description="AISStore region partition. Null means no public bulk AIS "
                    "covers this area and Stage 6 will synthesise and badge it "
                    "SYNTHETIC -- being explicit here is what stops fabricated "
                    "traffic being presented as real.")
    poll_minutes: int = Field(default=60, ge=5, le=10080)
    lookback_hours: int = Field(default=24, ge=1, le=720)
    auto_run: bool = True
    enabled: bool = True
    notes: str = ""


class AoiUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    geometry: Optional[Dict[str, Any]] = None
    bbox: Optional[List[float]] = None
    ais_region: Optional[str] = None
    poll_minutes: Optional[int] = Field(default=None, ge=5, le=10080)
    lookback_hours: Optional[int] = Field(default=None, ge=1, le=720)
    auto_run: Optional[bool] = None
    enabled: Optional[bool] = None
    notes: Optional[str] = None


def _resolve_shape(geometry, bbox) -> Dict[str, Any]:
    """Validate whichever of geometry/bbox was supplied. 422 on refusal."""
    if geometry is not None:
        try:
            checked = validate_aoi_geometry(geometry)
        except AoiGeometryError as exc:
            raise HTTPException(422, str(exc))
        return {"bbox": checked["bbox"], "geometry": geometry,
                "sea": checked["sea"]}

    if bbox is None:
        raise HTTPException(422, "an AOI needs either a GeoJSON `geometry` or a `bbox`")
    if len(bbox) != 4:
        raise HTTPException(422, "bbox must be [lon_min, lat_min, lon_max, lat_max]")

    lon_min, lat_min, lon_max, lat_max = (float(v) for v in bbox)
    if lon_min >= lon_max or lat_min >= lat_max:
        raise HTTPException(422, "bbox must have min < max on both axes")
    ring = [(lon_min, lat_min), (lon_max, lat_min), (lon_max, lat_max),
            (lon_min, lat_max), (lon_min, lat_min)]
    try:
        checked = validate_aoi_geometry(
            {"type": "Polygon", "coordinates": [[list(p) for p in ring]]})
    except AoiGeometryError as exc:
        raise HTTPException(422, str(exc))
    # No geometry stored: the caller gave a bbox and nothing was drawn.
    return {"bbox": checked["bbox"], "geometry": None, "sea": checked["sea"]}


@router.post("/aois", status_code=201,
             dependencies=[Depends(require_role("admin", "investigator"))])
def create_aoi(request: Request, body: AoiCreate, db: Session = Depends(get_db)):
    """Register a new area to watch."""
    registry.ensure_migrated(db)
    if db.get(Aoi, body.id) is not None:
        raise HTTPException(409, f"an AOI with id '{body.id}' already exists")

    shape = _resolve_shape(body.geometry, body.bbox)
    row = Aoi(id=body.id, name=body.name,
              bbox_json=json.dumps(shape["bbox"]),
              geometry_json=json.dumps(shape["geometry"]) if shape["geometry"] else None,
              ais_region=body.ais_region, poll_minutes=body.poll_minutes,
              lookback_hours=body.lookback_hours, auto_run=body.auto_run,
              enabled=body.enabled, notes=body.notes, source="api")
    db.add(row)
    audit_service.record(db, "aoi.create", request=request, resource=body.id,
                         detail=json.dumps({"bbox": shape["bbox"],
                                            "sea": shape["sea"],
                                            "ais_region": body.ais_region}),
                         commit=False)
    db.commit()
    payload = registry.row_dict(row)
    payload["sea_check"] = shape["sea"]
    return payload


@router.patch("/aois/{aoi_id}",
              dependencies=[Depends(require_role("admin", "investigator"))])
def update_aoi(request: Request, aoi_id: str, body: AoiUpdate,
               db: Session = Depends(get_db)):
    row = registry.get_row(db, aoi_id)
    if row is None:
        raise HTTPException(404, f"no AOI '{aoi_id}' in the registry")

    changed: Dict[str, Any] = {}
    if body.geometry is not None or body.bbox is not None:
        shape = _resolve_shape(body.geometry, body.bbox)
        row.bbox_json = json.dumps(shape["bbox"])
        row.geometry_json = json.dumps(shape["geometry"]) if shape["geometry"] else None
        changed["bbox"] = shape["bbox"]

    for field in ("name", "ais_region", "poll_minutes", "lookback_hours",
                  "auto_run", "enabled", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
            changed[field] = value

    if not changed:
        raise HTTPException(422, "nothing to update")

    audit_service.record(db, "aoi.update", request=request, resource=aoi_id,
                         detail=json.dumps(changed, default=str), commit=False)
    db.commit()
    return registry.row_dict(row)


@router.delete("/aois/{aoi_id}",
               dependencies=[Depends(require_role("admin", "investigator"))])
def delete_aoi(request: Request, aoi_id: str, db: Session = Depends(get_db)):
    """Retire an AOI definition.

    The `AoiWatch` row is deliberately kept. It holds the high-water mark of
    scenes already handled, and discarding it would turn a delete-then-recreate
    into a burst of duplicate investigations for scenes the system has already
    seen.
    """
    row = registry.get_row(db, aoi_id)
    if row is None:
        raise HTTPException(404, f"no AOI '{aoi_id}' in the registry")

    db.delete(row)
    audit_service.record(db, "aoi.delete", request=request, resource=aoi_id,
                         detail=json.dumps({"watch_state": "kept"}), commit=False)
    db.commit()
    return {"deleted": aoi_id,
            "watch_state": "kept -- re-registering this id resumes from the "
                           "last scene already handled"}
