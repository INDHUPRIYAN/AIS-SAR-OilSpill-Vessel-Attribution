"""Scheduler / AOI watcher API -- STAGE 0 (design doc v2 §4, §21, §27·8).

    /api/aois              the registry, merged with each AOI's live watch state
    /api/aois/{id}         one AOI
    /api/aois/poll         force a sweep now (what the demo presses)
    /api/aois/{id}/poll    force a sweep of one AOI

The registry is a config file, not a database table (§21), so there is no
create/update/delete here: an AOI is added by editing ``config/aois.yaml`` and
that file is part of the reproducible record. What the API exposes is the
*state* of watching -- when each AOI was last polled, what it last saw, and
whether its provider chain is healthy -- which is what the monitoring page and
the demo need.

``/aois/poll`` exists mainly for the demo: Sentinel-1's revisit is measured in
days, so "wait for a pass" is not a thing you can show on stage. Pressing poll
and watching an investigation open by itself is.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.core.authz import require_role
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.models.db import AoiWatch, get_db
from backend.services.scheduler.aoi import AOIConfigError, load_aois

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
                   status="pending", scene_id=scene_id))
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


def _registry():
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
    aois = _registry()
    if enabled_only:
        aois = [a for a in aois if a.enabled]
    return [_merge(a, db.get(AoiWatch, a.id)) for a in aois]


@router.get("/aois/{aoi_id}")
def get_aoi_detail(aoi_id: str, db: Session = Depends(get_db)):
    aoi = next((a for a in _registry() if a.id == aoi_id), None)
    if aoi is None:
        raise HTTPException(404, f"no AOI '{aoi_id}' in the registry")
    return _merge(aoi, db.get(AoiWatch, aoi_id))


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
