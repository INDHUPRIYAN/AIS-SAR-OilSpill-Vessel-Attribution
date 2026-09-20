"""Orchestration: create a job, run its engines in order, contain failures.

`execute_engine` is the single unit of work; `run_inline` calls it seven times
from a background thread. The rules:

* an engine that raises marks itself `failed`, fails the job, and every engine
  still `pending` becomes `skipped`;
* nothing downstream of a failure runs.
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from backend.models.hindcast import HindcastArchiveScene as ArchiveScene
from backend.models.hindcast import HindcastEngineRun as EngineRun
from backend.models.hindcast import HindcastJob as Job
from backend.models.hindcast import session_scope
from backend.services.hindcast import events
from backend.services.hindcast.config import PipelineConfig, job_dir
from backend.services.hindcast.engines import ENGINE_ORDER, get_engine
from backend.services.hindcast.engines.base import EngineError, EngineResult, PipelineContext
from backend.services.hindcast.engines.drift import DriftEngine
from backend.services.hindcast.engines.ingest import parse_time
from backend.services.hindcast.geo import geojson_of, load_geometry

DRIFT_ID = "drift_engine"


def create_job(payload: dict[str, Any], created_by: Optional[str] = None) -> str:
    """Persist the job and its seven pending engine runs. Returns the job id."""
    config = PipelineConfig(**(payload.get("config") or {}))
    meta = payload.get("scene_meta") or {}
    scene_time: Optional[datetime] = None
    if meta.get("acquired_utc"):
        try:
            scene_time = parse_time(meta["acquired_utc"])
        except ValueError:
            scene_time = None      # the ingest engine reports this properly
    aoi: Optional[str] = None
    try:
        source = meta.get("footprint") if isinstance(meta.get("footprint"), dict) else None
        geom = load_geometry(source or payload["slick_polygon_geojson"])
        aoi = json.dumps(geojson_of(geom.envelope))
    except Exception:  # noqa: BLE001 -- likewise: a bad polygon is the ingest engine's error to report
        aoi = None

    request = {k: payload.get(k) for k in ("scene_meta", "slick_polygon_geojson", "oil_type", "forcing", "truth", "source")}
    with session_scope() as db:
        job = Job(status="pending", scene_time=scene_time, aoi=aoi, label=payload.get("label"),
                  created_by=created_by,
                  request=request, config=config.model_dump())
        db.add(job)
        db.flush()
        for order, engine_id in enumerate(ENGINE_ORDER, start=1):
            db.add(EngineRun(job_id=job.id, engine_id=engine_id, stage_order=order, status="pending",
                             metrics={}, logs=[]))
        for scene in payload.get("archive") or []:
            db.add(ArchiveScene(aoi=json.dumps(scene["aoi"]), scene_time=parse_time(scene["scene_time"]),
                                slick_present=bool(scene.get("slick_present", False))))
        job_dir(job.id)
        return job.id


def _fail_job(job_id: str, message: str) -> None:
    payloads: list[dict[str, Any]] = []
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status, job.error = "failed", message
        for run in db.execute(select(EngineRun).where(EngineRun.job_id == job_id,
                                                      EngineRun.status == "pending")).scalars():
            run.status = "skipped"
            run.current_step = "skipped: an upstream engine failed"
            payloads.append({"type": "engine", **events.run_to_dict(run)})
        payloads.append({"type": "job", "job_id": job_id, "status": "failed", "error": message})
    for p in payloads:
        events.publish(job_id, p)


def execute_engine(job_id: str, engine_id: str, phase: Optional[str] = None,
                   start: Optional[int] = None, stop: Optional[int] = None) -> bool:
    """Run one engine (or one phase of the drift engine). Returns False if it did not succeed."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return False
        if job.status == "failed":
            return False
        first_touch = phase in (None, "prepare")
        if job.status == "pending":
            job.status = "running"
            events.publish_job(job)
        request, config = dict(job.request or {}), PipelineConfig(**(job.config or {}))

    emitter = events.DbEmitter(job_id, engine_id)
    if phase == "members":
        emitter.owns_percent = False
    if first_touch:
        emitter.started()
    ctx = PipelineContext.load(job_id, job_dir(job_id), config, request, emitter)
    engine = get_engine(engine_id)
    try:
        result: Optional[EngineResult] = None
        if phase is None:
            result = engine.run(ctx)
        elif not isinstance(engine, DriftEngine):
            raise EngineError(f"engine '{engine_id}' has no phase '{phase}'")
        elif phase == "prepare":
            engine.prepare(ctx)
        elif phase == "members":
            engine.run_members(ctx, int(start or 0), int(stop or 0))
        elif phase == "finalize":
            result = engine.finalize(ctx)
        if phase != "members":
            ctx.save()      # member workers run in parallel and do not touch shared state
        if result is not None:
            emitter.metric(**result.metrics)
            if result.summary:
                emitter.log(result.summary)
                emitter.current_step = result.summary     # a finished tile shows its outcome, not its last step
            emitter.succeeded()
            if engine_id == ENGINE_ORDER[-1]:
                with session_scope() as db:
                    job = db.get(Job, job_id)
                    if job is not None:
                        job.result, job.status = ctx.state.get("result"), "succeeded"
                        events.publish_job(job)
        return True
    except Exception as exc:  # noqa: BLE001 -- the boundary where any engine error becomes a failed run
        message = str(exc) if isinstance(exc, EngineError) else f"{type(exc).__name__}: {exc}"
        if not isinstance(exc, EngineError):
            emitter.log(traceback.format_exc(limit=6))
        emitter.failed(message)
        _fail_job(job_id, f"{engine_id}: {message}")
        return False


def run_inline(job_id: str) -> None:
    for engine_id in ENGINE_ORDER:
        if not execute_engine(job_id, engine_id):
            break


def start_pipeline(job_id: str) -> None:
    threading.Thread(target=run_inline, args=(job_id,), name=f"hindcast-{job_id}", daemon=True).start()
