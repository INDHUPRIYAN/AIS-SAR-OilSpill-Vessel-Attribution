"""Investigation-page endpoints: the workspace's own view of a run.

The generic /api/runs and /api/layers routes stay as they are; these routes
speak the investigation-first shape the workspace uses -- resolve the latest
run for an investigation, expose the LIVE per-stage status the pipeline
flushes after every stage, validate contract files on the way out (a
malformed file becomes a 422 with the validation summary, never a blank
page), and serve replay mode: the same UI fed from files already on disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.db import Investigation, Run, get_db

router = APIRouter()
settings = get_settings()

LAYER_FILES = {
    "scene_meta": "scene_meta.json",
    "detect": "detect_response.json",
    "slick": "slick.geojson",
    "origin_cloud": "origin_cloud.geojson",
    "forecast": "forecast.geojson",
    "suspects": "suspects.json",
    "manifest": "manifest.json",
}

# Which contract validator guards each layer. Serving is the last moment a
# malformed file can be caught before it reaches the map.
VALIDATED = {"slick", "origin_cloud", "forecast", "suspects", "scene_meta"}


def _latest_run(db: Session, investigation_id: str,
                completed_only: bool = False) -> Optional[Run]:
    q = db.query(Run).filter(Run.investigation_id == investigation_id)
    if completed_only:
        q = q.filter(Run.status == "complete")
    return q.order_by(Run.started_utc.desc()).first()


def _run_dir(run_id: str) -> Path:
    root = settings.runs_root.resolve()
    d = (root / run_id).resolve()
    if not str(d).startswith(str(root)):
        raise HTTPException(400, "invalid run id")
    return d


@router.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: str, db: Session = Depends(get_db)):
    inv = db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(404, "unknown investigation")
    run = _latest_run(db, investigation_id)
    meta = {}
    if run:
        mp = _run_dir(run.id) / "scene_meta.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
    return {
        "id": inv.id, "name": inv.name, "scene_id": inv.scene_id,
        "created_utc": inv.created_utc,
        "latest_run_id": run.id if run else None,
        "run_status": run.status if run else None,
        "acquired_utc": meta.get("acquired_utc"),
        "bbox": meta.get("bbox"),
        "scene_source": meta.get("source"),
        "provider_used": meta.get("provider_used"),
    }


@router.get("/investigations/{investigation_id}/status")
def investigation_status(investigation_id: str, run: Optional[str] = None,
                         db: Session = Depends(get_db)):
    """Per-stage statuses for the latest run -- live during execution.

    Reads the status.json the pipeline flushes after every stage, so the UI
    can flip a stage green and render its layer immediately. Falls back to
    the manifest for runs made before live flushing existed.
    """
    # Replay mode may borrow a run from another investigation of the same
    # scene; the page passes that run id explicitly so status and layers
    # describe what is actually on screen.
    row = db.get(Run, run) if run else _latest_run(db, investigation_id)
    if not row:
        return {"state": "new", "run_id": None, "stages": []}
    run = row
    d = _run_dir(run.id)
    for candidate in ("status.json", "manifest.json"):
        f = d / candidate
        if f.exists():
            try:
                payload = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            stages = payload.get("stages", [])
            state = payload.get("state") or (
                "complete" if run.status == "complete" else run.status)
            # Which layer files exist right now -- drives incremental render
            # and the disabled toggles for not-yet-produced layers.
            present = {name: (d / fn).exists() for name, fn in LAYER_FILES.items()}
            present["vessels"] = any(
                (d / n).exists() for n in
                ("vessels.parquet", "engine_native/vessels_generated.parquet"))
            return {"run_id": run.id, "state": state,
                    "run_status": run.status, "stages": stages,
                    "layers_present": present}
    return {"run_id": run.id, "state": run.status, "stages": [],
            "layers_present": {}}


@router.get("/investigations/{investigation_id}/layers/{layer}")
def investigation_layer(investigation_id: str, layer: str,
                        db: Session = Depends(get_db)):
    """Serve a contract layer of the latest run, validated on the way out."""
    if layer not in LAYER_FILES:
        raise HTTPException(404, f"unknown layer '{layer}'")
    run = _latest_run(db, investigation_id)
    if not run:
        raise HTTPException(404, "no runs for this investigation")
    target = _run_dir(run.id) / LAYER_FILES[layer]
    if not target.exists():
        raise HTTPException(404, f"layer '{layer}' not yet produced")

    if target.suffix not in (".json", ".geojson"):
        return FileResponse(target)

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            422, f"malformed contract file {target.name}: "
                 f"{type(exc).__name__}: {str(exc)[:200]}")

    if layer in VALIDATED:
        from backend.services.pipeline.run import validate

        err = validate(layer, target)
        if err:
            raise HTTPException(
                422, f"contract validation failed for {target.name}: "
                     f"{str(err)[:400]}")
    return JSONResponse(payload)


@router.get("/investigations/{investigation_id}/suspects")
def investigation_suspects(investigation_id: str, db: Session = Depends(get_db)):
    return investigation_layer(investigation_id, "suspects", db)


@router.post("/investigations/{investigation_id}/replay")
def replay_run(investigation_id: str, db: Session = Depends(get_db)):
    """Replay mode: point the UI at the newest COMPLETE run's files on disk.

    Nothing executes; the same page renders from the contract files already
    produced, which is the offline last line of defence -- and the fast path
    for demos (target: fully rendered in under five seconds).
    """
    run = _latest_run(db, investigation_id, completed_only=True)
    if not run:
        # fall back to any complete run of the same scene
        inv = db.get(Investigation, investigation_id)
        if inv and inv.scene_id:
            run = (db.query(Run)
                   .filter(Run.scene_id == inv.scene_id,
                           Run.status == "complete")
                   .order_by(Run.started_utc.desc()).first())
    if not run:
        raise HTTPException(409, "nothing to replay: no complete run exists "
                                 "for this investigation yet")
    return {"run_id": run.id, "replay": True, "status": "complete"}
