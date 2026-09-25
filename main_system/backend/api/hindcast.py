"""BAYES-TRACK hindcast: jobs, the engine registry, and live engine status.

Reading is open to every authenticated role, like the rest of the analytical
record. Starting a hindcast is investigative work: investigator / analyst
(admin and super_admin implicitly), the same authority as starting a run.

The WebSocket cannot take the router-level `authenticated` dependency (that
one needs an HTTP `Request`), so it checks the session cookie itself, with the
same rules: a valid session, or the public evaluator when that mode is on.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from fastapi import (APIRouter, Depends, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.authz import _session_user, current_user, evaluator_user, require_role
from backend.core.config import get_settings
from backend.core.paths import host_path
from backend.models.db import SessionLocal, User, get_db
from backend.models.hindcast import (HindcastEngineRun, HindcastJob, HindcastParticleSnapshot)
from backend.services import audit as audit_service
from backend.services.hindcast import events
from backend.services.hindcast.config import PipelineConfig
from backend.services.hindcast.engines import ENGINE_ORDER, all_engines
from backend.services.hindcast.pipeline import create_job, start_pipeline
from backend.services.hindcast.synthetic import build_demo_request

settings = get_settings()
router = APIRouter(tags=["hindcast"])
ws_router = APIRouter()
_OPERATORS = require_role("investigator", "analyst")
RESYNC_S = 4.0   # a full snapshot this often: keep-alive, and a net under any dropped message


# ------------------------------------------------------------------ schemas --

class SarWind(BaseModel):
    speed_ms: float = Field(ge=0, le=60)
    dir_from_deg: float = Field(ge=0, le=360, description="meteorological: bearing the wind blows FROM")


class SceneMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    scene_id: Optional[str] = None
    acquired_utc: Optional[str] = Field(None, description="capture time T, ISO 8601")
    scene_path: Optional[str] = None
    metadata_path: Optional[str] = None
    footprint: Optional[Any] = None
    incidence_angle_deg: Optional[float] = None
    sar_wind: Optional[SarWind] = None


class ForcingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wind_path: Optional[str] = None
    current_path: Optional[str] = None
    stokes_path: Optional[str] = None
    oceantrace_run: Optional[str] = Field(None, description="resolve forcing from the metocean cache of this run")
    half_size_deg: Optional[float] = None
    grid_deg: Optional[float] = None
    synthetic: Optional[dict[str, Any]] = Field(None, description="analytic test field; demo and tests only")


class ArchiveSceneIn(BaseModel):
    aoi: dict[str, Any]
    scene_time: str
    slick_present: bool = False


class HindcastJobCreate(BaseModel):
    scene_meta: SceneMeta
    slick_polygon_geojson: dict[str, Any]
    oil_type: Optional[Literal["light", "medium", "heavy"]] = None
    config: Optional[dict[str, Any]] = Field(None, description="PipelineConfig overrides")
    forcing: Optional[ForcingSpec] = None
    label: Optional[str] = Field(None, max_length=120)
    archive: Optional[list[ArchiveSceneIn]] = None


class DemoRequest(BaseModel):
    tau_true_h: int = Field(18, ge=3, le=96)
    clean_scene_h: int = Field(36, ge=6, le=120)
    members: Optional[int] = Field(None, ge=4, le=500)
    seed: int = 7


class FromRunRequest(BaseModel):
    oil_type: Optional[Literal["light", "medium", "heavy"]] = None
    config: Optional[dict[str, Any]] = None


# ------------------------------------------------------------------ helpers --

def _summary(job: HindcastJob) -> dict[str, Any]:
    return {"id": job.id, "label": job.label, "status": job.status, "error": job.error,
            "created_by": job.created_by,
            "created_at": events.iso_utc(job.created_at), "scene_time": events.iso_utc(job.scene_time),
            "source": (job.request or {}).get("source") or "request"}


def _launch(payload: dict[str, Any], request: Request, user: User, db: Session, action: str) -> dict[str, Any]:
    try:
        PipelineConfig(**(payload.get("config") or {}))
    except ValidationError as exc:
        first = exc.errors()[0]
        raise HTTPException(422, f"invalid config override at {list(first['loc'])}: {first['msg']}") from exc
    job_id = create_job(payload, created_by=user.email)
    audit_service.record(db, action, request=request, resource=job_id,
                         detail=json.dumps({"label": payload.get("label"), "source": payload.get("source")}))
    start_pipeline(job_id)
    return {"job_id": job_id, "status": "pending"}


def status_payload(db: Session, job_id: Optional[str]) -> dict[str, Any]:
    if job_id:
        job = db.get(HindcastJob, job_id)
        if job is None:
            raise HTTPException(404, f"no hindcast job {job_id}")
    else:
        job = db.execute(select(HindcastJob).order_by(HindcastJob.created_at.desc()).limit(1)).scalar_one_or_none()
    if job is None:
        return {"job": None, "engines": []}
    return {"job": _summary(job), "engines": [events.run_to_dict(r) for r in job.engine_runs]}


# --------------------------------------------------------------------- jobs --

@router.post("/hindcast/jobs", status_code=202, dependencies=[Depends(_OPERATORS)])
def start_job(body: HindcastJobCreate, request: Request, db: Session = Depends(get_db),
              user: User = Depends(current_user)) -> dict[str, Any]:
    return _launch({**body.model_dump(exclude_none=True), "source": "request"}, request, user, db, "hindcast.start")


@router.post("/hindcast/demo", status_code=202, dependencies=[Depends(_OPERATORS)])
def start_demo_job(request: Request, body: Optional[DemoRequest] = None, db: Session = Depends(get_db),
                   user: User = Depends(current_user)) -> dict[str, Any]:
    """A SYNTHETIC spill with a known origin: proves origin recovery end to end."""
    body = body or DemoRequest()
    config = {"members": body.members} if body.members else None
    payload = build_demo_request(tau_true_h=body.tau_true_h, clean_scene_h=body.clean_scene_h,
                                 seed=body.seed, config=config)
    return _launch({**payload, "source": "synthetic_demo"}, request, user, db, "hindcast.demo")


@router.post("/hindcast/from_run/{run_id}", status_code=202, dependencies=[Depends(_OPERATORS)])
def start_from_run(run_id: str, request: Request, body: Optional[FromRunRequest] = None,
                   db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """Hindcast the slick an OceanTrace run segmented, with the forcing cached for that scene."""
    root = settings.runs_root.resolve()
    run_dir = (root / run_id).resolve()
    if not str(run_dir).startswith(str(root)) or not run_dir.is_dir():
        raise HTTPException(404, f"no run {run_id}")
    try:
        meta = json.loads((run_dir / "scene_meta.json").read_text(encoding="utf-8"))
        slick = json.loads((run_dir / "slick.geojson").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, f"run {run_id} has no readable scene_meta.json and slick.geojson: "
                                 f"there is nothing to hindcast") from exc
    status_file = run_dir / "status.json"
    if status_file.exists():
        stages = json.loads(status_file.read_text(encoding="utf-8")).get("stages", [])
        if any(s.get("stage") == "characterise" and s.get("status") == "mock" for s in stages):
            raise HTTPException(409, f"the slick in run {run_id} is a legacy MOCK file, not a segmentation of its scene")
    features = [f for f in slick.get("features", []) if f.get("geometry")]
    if not features:
        raise HTTPException(409, f"run {run_id} segmented no slick")
    if not meta.get("acquired_utc"):
        raise HTTPException(409, f"run {run_id} records no acquisition time")
    largest = max(features, key=lambda f: (f.get("properties") or {}).get("area_km2") or 0.0)

    from backend.api.sar_database import basis_for_meta

    basis = basis_for_meta(meta)
    body = body or FromRunRequest()
    payload = {
        "label": f"Run {run_id} · slick {(largest.get('properties') or {}).get('slick_id') or 'largest'}",
        "scene_meta": {"scene_id": meta.get("scene_id"), "acquired_utc": meta["acquired_utc"],
                       "footprint": meta.get("bbox"), "bbox": meta.get("bbox"),
                       "geo_basis": basis.get("geo_basis")},
        "slick_polygon_geojson": largest["geometry"],
        "oil_type": body.oil_type,
        "config": body.config,
        "forcing": {"oceantrace_run": run_id},
        "source": f"run:{run_id}",
    }
    return _launch(payload, request, user, db, "hindcast.from_run")


@router.get("/hindcast/jobs")
def list_jobs(limit: int = Query(25, ge=1, le=200), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    jobs = db.execute(select(HindcastJob).order_by(HindcastJob.created_at.desc()).limit(limit)).scalars().all()
    return [_summary(j) for j in jobs]


@router.get("/hindcast/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(HindcastJob, job_id)
    if job is None:
        raise HTTPException(404, f"no hindcast job {job_id}")
    return {**_summary(job), "config": job.config or {}, "result": job.result,
            "engines": [events.run_to_dict(r, log_tail=None) for r in job.engine_runs]}


@router.get("/hindcast/jobs/{job_id}/particles")
def job_particles(job_id: str, tau: int = Query(..., ge=0, description="hours before the scene time"),
                  max_points: int = Query(6000, ge=100, le=50000),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    """The backtracked particle cloud at one age, as GeoJSON (one MultiPoint per member)."""
    if db.get(HindcastJob, job_id) is None:
        raise HTTPException(404, f"no hindcast job {job_id}")
    rows = db.execute(select(HindcastParticleSnapshot)
                      .where(HindcastParticleSnapshot.job_id == job_id, HindcastParticleSnapshot.tau_hours == tau)
                      .order_by(HindcastParticleSnapshot.member)).scalars().all()
    if not rows:
        raise HTTPException(404, f"no particle snapshot for tau={tau} h: the drift stage has not finished, "
                                 f"or tau is outside the search window")
    per_member = max(1, max_points // len(rows))
    features = []
    for row in rows:
        path = host_path(row.parquet_path)
        if not path.exists():
            continue
        frame = pd.read_parquet(path, filters=[("tau_hours", "==", tau)])
        pts = frame[["lon", "lat"]].to_numpy()[:per_member]
        features.append({"type": "Feature", "properties": {"member": row.member, "tau_hours": tau},
                         "geometry": {"type": "MultiPoint", "coordinates": np.round(pts, 5).tolist()}})
    return {"type": "FeatureCollection", "features": features,
            "properties": {"job_id": job_id, "tau_hours": tau, "members": len(features)}}


# ------------------------------------------------------------------ engines --

@router.get("/engines")
def engine_registry() -> list[dict[str, Any]]:
    """The static pipeline: id, name, description and stage of each engine."""
    return [e.describe() for e in all_engines()]


@router.get("/engines/status")
def engine_status(job_id: Optional[str] = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every engine run of one job (the most recent job when `job_id` is omitted)."""
    return status_payload(db, job_id)


@router.get("/engines/{engine_id}/runs")
def engine_run_history(engine_id: str, limit: int = Query(20, ge=1, le=200),
                       db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if engine_id not in ENGINE_ORDER:
        raise HTTPException(404, f"unknown engine '{engine_id}'")
    rows = db.execute(select(HindcastEngineRun, HindcastJob.created_at, HindcastJob.label)
                      .join(HindcastJob, HindcastJob.id == HindcastEngineRun.job_id)
                      .where(HindcastEngineRun.engine_id == engine_id)
                      .order_by(HindcastJob.created_at.desc()).limit(limit)).all()
    return [{**events.run_to_dict(run, log_tail=0), "job_label": label,
             "job_created_at": events.iso_utc(created)} for run, created, label in rows]


# ---------------------------------------------------------------- websocket --

def _ws_user(ws: WebSocket) -> Optional[User]:
    with SessionLocal() as db:
        user = _session_user(ws, db)      # a WebSocket carries the same cookies a Request does
        if user is None and settings.public_evaluator:
            user = evaluator_user(db)
        return user


def _snapshot(job_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        return {"type": "snapshot", **status_payload(db, job_id)}


@ws_router.websocket("/ws/engines/{job_id}")
async def engine_updates(ws: WebSocket, job_id: str) -> None:
    """A snapshot on connect, then every engine update as it happens."""
    if await run_in_threadpool(_ws_user, ws) is None:
        await ws.close(code=4401)         # before accept: the handshake is refused
        return
    await ws.accept()
    try:
        first = await run_in_threadpool(_snapshot, job_id)
    except HTTPException:
        await ws.send_text(json.dumps({"type": "error", "detail": f"no hindcast job {job_id}"}))
        await ws.close(code=4404)
        return
    queue, unsubscribe = events.subscribe_local(job_id)
    try:
        await ws.send_text(json.dumps(first, default=str))
        while True:
            try:
                await ws.send_text(await asyncio.wait_for(queue.get(), timeout=RESYNC_S))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps(await run_in_threadpool(_snapshot, job_id), default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        with contextlib.suppress(Exception):
            unsubscribe()
