"""REST API for the OceanTrace main system.

    /api/investigations          create, list, run
    /api/runs/{id}               status, manifest, provenance
    /api/runs/{id}/verify        re-hash artefacts against the manifest (§12)
    /api/runs/{id}/decisions     Stage 9: what a human concluded (§14)
    /api/layers/{run}/{name}     serve a contract file to the UI
    /api/apis/...                monitoring page: status, history, test-now
    /api/keys/...                admin only: masked list, set, test

Two invariants worth stating because they are easy to break later:

  * layer files are served only from inside the run directory, resolved and
    checked -- a run id is user input and `../` must not escape;
  * key endpoints never return a plaintext credential, only `••••1234`.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.core.authz import require_role
from backend.core.config import PROVIDER_BY_NAME, PROVIDERS, get_settings
from backend.core.security import (encryption_available, encrypt, is_encrypted,
                                   last_four, mask, resolve_credential,
                                   verify_admin)
from backend.models.db import (IMPLICIT_ROLES, VERDICTS, ApiCall, ApiKey,
                               ApiProvider, AuditLog, Decision, Investigation,
                               Job, Run, get_db, utcnow)
from backend.services import audit as audit_service
from backend.services import jobs as jobs_service
from backend.services.pipeline import provenance
from backend.services.providers import health

router = APIRouter()
settings = get_settings()

# Relative scene paths are resolved against the repo, not the server's CWD,
# so a path means the same thing from the API, the CLI and a test.
REPO_ROOT = settings.data_root.parent

RUN_SORTABLE = {
    "started_utc": Run.started_utc,
    "finished_utc": Run.finished_utc,
    "seconds": Run.seconds,
    "status": Run.status,
    "top_score": Run.top_score,
    "slick_area_km2": Run.slick_area_km2,
}

# Contract file -> what the UI calls the layer.
LAYER_FILES = {
    "scene_meta": "scene_meta.json",
    "detect": "detect_response.json",
    "slick": "slick.geojson",
    "origin_cloud": "origin_cloud.geojson",
    "forecast": "forecast.geojson",
    "suspects": "suspects.json",
    "vessels": "vessels.parquet",
    "manifest": "manifest.json",
    "mask": "raw_mask.tif",
}


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------


class InvestigationCreate(BaseModel):
    """What the New Investigation wizard sends.

    The scene can be named three ways and they are checked in this order:
    an explicit `scene_meta_path`, a `scene_product_id` the server resolves
    against the local catalogue, or an `aoi_id` + window for a search-first
    flow. Only the first two produce a runnable investigation today -- a
    product that has never been downloaded has no raster, and the response says
    so rather than creating something that fails at run time.
    """

    name: str = Field(min_length=1, max_length=200)
    scene_path: Optional[str] = None
    scene_meta_path: Optional[str] = None
    notes: Optional[str] = None
    incident_id: Optional[str] = None

    # PROMPT 13 additions
    aoi_id: Optional[str] = Field(
        default=None, description="a registered AOI this investigation covers")
    aoi: Optional[dict] = Field(
        default=None, description="an ad-hoc GeoJSON Polygon, validated the "
                                  "same way a registered AOI is")
    window_start_utc: Optional[datetime] = None
    window_end_utc: Optional[datetime] = None
    scene_product_id: Optional[str] = Field(
        default=None, description="a product id from /api/scenes/search; "
                                  "resolved to a cached scene on the server")

    @field_validator("window_end_utc")
    @classmethod
    def _window_ordered(cls, end, info):
        start = info.data.get("window_start_utc")
        if start is not None and end is not None and end <= start:
            raise ValueError(
                "window_end_utc must be after window_start_utc; an empty or "
                "reversed window would search nothing and report it as no data")
        return end


class RunRequest(BaseModel):
    engine: str = Field(default="auto", pattern="^(auto|ml|threshold_fallback)$")
    scene_path: Optional[str] = None
    scene_meta_path: Optional[str] = None


class DecisionCreate(BaseModel):
    """Stage 9. Note what the verdicts mean and what they deliberately do not.

    §2: the system "does not accuse". A verdict here judges whether the ranking
    was sound and worth pursuing -- never whether a named operator is guilty.
    """

    verdict: str = Field(pattern=f"^({'|'.join(VERDICTS)})$")
    mmsi: Optional[int] = Field(default=None, ge=100_000_000, le=999_999_999,
                                description="null = a verdict on the run itself")
    note: Optional[str] = Field(default=None, max_length=4000)
    actor: str = Field(default="analyst", max_length=64)


class KeyUpdate(BaseModel):
    provider: str
    field: str
    value: str = Field(min_length=1)


def require_admin(request: Request,
                  x_admin_token: Optional[str] = Header(default=None),
                  db: Session = Depends(get_db)) -> str:
    """Admin authority, from the session first and the legacy header second.

    The shared `X-Admin-Token` is being retired: it identifies nobody, so an
    audit row it produces can only ever say "admin", and the frontend had to
    keep it in `localStorage` where any scripting bug could read it (AD-06).
    A signed-in admin now satisfies this guard and the audit gets a real name.

    The header still works for one release, and only when
    `OT_ALLOW_LEGACY_ADMIN_TOKEN=true` says so explicitly -- a deprecation
    that cannot be turned off is not a deprecation. Returns the actor's email
    so callers record who acted, not what role acted.
    """
    from backend.core.authz import optional_user

    user = optional_user(request, db)
    if user is not None:
        # IMPLICIT_ROLES, not a literal "admin". This guard is hand-written
        # rather than built by `require_role`, so when `super_admin` was added
        # it did not inherit the implicit grant and a super administrator was
        # locked out of credential management -- which the production spec
        # explicitly gives them. Sharing the tuple is what keeps the two
        # guards from drifting again.
        if user.role not in IMPLICIT_ROLES:
            raise HTTPException(403, f"role '{user.role}' may not manage credentials")
        return user.email

    if settings.allow_legacy_admin_token and verify_admin(x_admin_token):
        return "legacy-admin-token"

    raise HTTPException(status_code=401, detail="admin session required")


# Same marker `require_role` sets, so the route-table audit sees this guard
# too. Without it these routes read as "no role guard declared", which is
# indistinguishable from having forgotten one.
require_admin.allowed_roles = frozenset(IMPLICIT_ROLES)


# --------------------------------------------------------------------------
# investigations + runs
# --------------------------------------------------------------------------


def _resolve_product(product_id: str) -> Optional[str]:
    """Find a downloaded scene's scene_meta.json by product id.

    Looks in the curated catalogue first, then at scene directories on disk.
    Returns None when the product is not held locally -- the caller turns that
    into a 404 that says a catalogue hit is not a downloaded file.
    """
    catalogue = REPO_ROOT / "main_system" / "config" / "scene_catalog.json"
    if catalogue.exists():
        try:
            entries = json.loads(catalogue.read_text(encoding="utf-8"))
            for entry in (entries if isinstance(entries, list)
                          else entries.get("scenes", [])):
                meta_rel = entry.get("scene_meta_path")
                if not meta_rel:
                    continue
                meta_file = REPO_ROOT / meta_rel
                if not meta_file.exists():
                    continue
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if product_id in (entry.get("id"), meta.get("scene_id")):
                    return str(meta_file)
        except (json.JSONDecodeError, OSError):
            pass

    scenes_root = REPO_ROOT / "data" / "scenes"
    if scenes_root.is_dir():
        for meta_file in sorted(scenes_root.glob("*/scene_meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if meta.get("scene_id") == product_id:
                return str(meta_file)
    return None


@router.post("/investigations",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def create_investigation(request: Request, body: InvestigationCreate,
                         db: Session = Depends(get_db)):
    if body.incident_id:
        from backend.models.db import Incident

        if db.get(Incident, body.incident_id) is None:
            raise HTTPException(400, f"no incident {body.incident_id}")
    # An AOI, if named, must exist and must be watchable. Accepting an unknown
    # id would produce an investigation whose footprint is a dangling reference.
    aoi_bbox = None
    if body.aoi_id:
        from backend.services.scheduler import registry as aoi_registry

        aoi_row = aoi_registry.get_row(db, body.aoi_id)
        if aoi_row is None:
            raise HTTPException(400, f"no AOI '{body.aoi_id}' in the registry")
        aoi_bbox = json.loads(aoi_row.bbox_json)
    elif body.aoi is not None:
        from backend.services.scheduler.geometry import (AoiGeometryError,
                                                         validate_aoi_geometry)
        try:
            aoi_bbox = validate_aoi_geometry(body.aoi)["bbox"]
        except AoiGeometryError as exc:
            raise HTTPException(422, str(exc))

    scene_meta_path = body.scene_meta_path
    if not scene_meta_path and body.scene_product_id:
        # Resolve a catalogue product to a scene the server actually holds. A
        # product id is a claim that a scene exists somewhere, not that it has
        # been downloaded -- conflating the two is how a UI offers "run this"
        # for bytes nobody has.
        scene_meta_path = _resolve_product(body.scene_product_id)
        if scene_meta_path is None:
            raise HTTPException(
                404,
                f"product '{body.scene_product_id}' is not in the local scene "
                "cache. A catalogue hit is not a downloaded scene: fetch it "
                "first, then create the investigation.")

    inv = Investigation(
        id=f"inv-{uuid.uuid4().hex[:10]}", name=body.name,
        scene_path=body.scene_path, notes=body.notes,
        incident_id=body.incident_id)
    if aoi_bbox is not None:
        inv.bbox = json.dumps(aoi_bbox)
    if scene_meta_path:
        meta_path = Path(scene_meta_path)
        if not meta_path.is_absolute():
            meta_path = REPO_ROOT / meta_path
        if not meta_path.exists():
            raise HTTPException(400, f"scene_meta not found: {scene_meta_path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        inv.scene_id = meta.get("scene_id")
        inv.bbox = json.dumps(meta.get("bbox"))
        # Keep the path: runs need it. It used to be read for scene_id and then
        # dropped, so every run fell back to the demo scene regardless of what
        # was selected -- three different scenes produced one identical answer.
        inv.scene_meta_path = str(meta_path)
        # The raster the metadata points at is the scene. Resolving it here
        # means the caller does not have to pass the same location twice.
        if not inv.scene_path and meta.get("file_path"):
            raster = Path(meta["file_path"])
            if not raster.is_absolute():
                raster = REPO_ROOT / raster
            if not raster.exists():
                raise HTTPException(
                    400, f"scene raster referenced by scene_meta is missing: "
                         f"{meta['file_path']}")
            inv.scene_path = str(raster)
    db.add(inv)
    audit_service.record(db, "investigation.create", request=request,
                         resource=inv.id,
                         detail=json.dumps({"name": inv.name,
                                            "scene_id": inv.scene_id}),
                         commit=False)
    db.commit()
    return {"id": inv.id, "name": inv.name, "scene_id": inv.scene_id,
            "created_utc": inv.created_utc}


@router.get("/investigations")
def list_investigations(db: Session = Depends(get_db)):
    rows = db.query(Investigation).order_by(Investigation.created_utc.desc()).all()
    return [{"id": r.id, "name": r.name, "scene_id": r.scene_id,
             "created_utc": r.created_utc, "runs": len(r.runs)} for r in rows]


_run_lock = threading.Lock()
_running: set = set()

# How many pipelines may execute at once in this process. One, by default:
# during P20 acceptance an AOI poll fanned out three auto_run pipelines on top
# of a fourth already executing, and the process died in HDF5 with a
# segmentation fault -- the metocean readers are not thread-safe. Every launch
# path (start, rerun, the AOI watcher) goes through _execute_run, so this gate
# serialises all of them. A queued run keeps its `pending` status until it
# actually starts, which is what the word means.
_pipeline_gate = threading.BoundedSemaphore(
    max(1, int(os.getenv("OT_MAX_CONCURRENT_RUNS", "1"))))


def _execute_run(run_id: str, investigation_id: Optional[str],
                 scene: Optional[str], scene_meta: Optional[str], engine: str,
                 started_by_person: bool = True):
    """Run the pipeline in a worker thread and record the outcome.

    `started_by_person` decides whether a clean completion is worth an alert:
    somebody pressed Run and has probably navigated away, whereas the AOI
    watcher already raises `new_scene` for what it starts (services/run_alerts).
    """
    with _pipeline_gate:
        _execute_run_now(run_id, investigation_id, scene, scene_meta, engine,
                         started_by_person=started_by_person)


def _execute_run_now(run_id: str, investigation_id: Optional[str],
                     scene: Optional[str], scene_meta: Optional[str], engine: str,
                     started_by_person: bool = True):
    from backend.models.db import SessionLocal
    from backend.services.pipeline.run import MOCKS, run_pipeline

    with SessionLocal() as db:
        row = db.get(Run, run_id)
        row.status = "running"
        db.commit()

    from backend.services import jobs as jobs_service
    from backend.services.pipeline.run import (RunCancelled, clear_cancel_check,
                                               set_cancel_check)

    job_id = f"job-{run_id}"
    set_cancel_check(run_id, lambda: jobs_service.is_cancelling(job_id))
    with SessionLocal() as db:
        jobs_service.start(db, job_id)

    try:
        meta_path = Path(scene_meta) if scene_meta else MOCKS / "scene_meta.json"
        if scene:
            scene_path = Path(scene)
        else:
            # Never pair a caller-supplied scene_meta with the demo raster:
            # that combination reports one scene's coordinates over another
            # scene's pixels, which is worse than failing.
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            raster = Path(meta.get("file_path") or (MOCKS / "scene_sigma0_db.tif"))
            if not raster.is_absolute():
                raster = REPO_ROOT / raster
            scene_path = raster
        manifest = run_pipeline(
            scene_path, meta_path,
            run_id, None, None if engine == "auto" else engine)
        stages = manifest.get("stages", [])
        with SessionLocal() as db:
            row = db.get(Run, run_id)
            row.status = "complete"
            row.finished_utc = utcnow()
            row.seconds = manifest.get("total_seconds", 0.0)
            row.scene_id = manifest.get("scene_id")
            row.stages_total = len(stages)
            row.stages_real = sum(s["status"] in ("ok", "fallback") for s in stages)
            row.stages_mock = sum(s["status"] == "mock" for s in stages)
            row.stages_failed = sum(s["status"] == "failed" for s in stages)
            detect = next((s for s in stages if s["stage"] == "detect"), {})
            row.detect_engine = (detect.get("detail") or "").split("engine=")[-1].split(",")[0]
            row.manifest_path = str(settings.runs_root / run_id / "manifest.json")
            summarise_outcome(row, settings.runs_root / run_id)
            db.commit()
            # Index the vessels this run considered, so the dossier can answer
            # "what else do we know about this MMSI" without rescanning every
            # artefact. Failure here must not fail the run: the index is
            # derived data and can be rebuilt by the backfill.
            try:
                from backend.services import vessel_index

                vessel_index.index_run(db, run_id, settings.runs_root / run_id)
            except Exception as exc:               # noqa: BLE001
                print(f"[vessel_index] {run_id}: {type(exc).__name__}: {exc}")

            # Automatic incident creation (spec section 17). The run has just
            # sealed, so its artefacts are final and the validation gate is
            # reading evidence rather than work in progress.
            #
            # Failure here must never fail the run, and must never be silent:
            # the run is the evidence and the incident is an index entry over
            # it, so an un-opened case is recoverable (promote the run by hand)
            # while a lost run is not. The refusal reason is printed either
            # way, because "the pipeline found oil and nobody was told" is the
            # exact failure this feature exists to prevent.
            outcome: dict = {}
            try:
                from backend.services import incident_auto

                outcome = incident_auto.create_incident_from_run(db, run_id)
                if outcome.get("created"):
                    print(f"[incident] {run_id} -> {outcome['incident_id']} "
                          f"({outcome['routing']['routing']}; alert "
                          f"{outcome.get('alert_id')})")
                else:
                    print(f"[incident] {run_id}: no case opened -- "
                          f"{outcome.get('reason')}")
                    for reason in (outcome.get("verdict") or {}).get("reasons", []):
                        print(f"[incident]   {reason}")
            except Exception as exc:               # noqa: BLE001
                print(f"[incident] {run_id}: {type(exc).__name__}: {exc}")

            # The run is over and whoever started it is probably on another
            # screen. An alert is the only thing that reaches them; a row
            # appearing in a list is not a notification. Never fails the run.
            try:
                from backend.services import run_alerts

                alert = run_alerts.alert_run_outcome(
                    db, run_id, started_by_person=started_by_person,
                    opened_incident=bool((outcome or {}).get("created")))
                if alert is not None:
                    db.commit()
            except Exception as exc:               # noqa: BLE001
                print(f"[run_alerts] {run_id}: {type(exc).__name__}: {exc}")

            jobs_service.sync_progress(db, db.get(Job, job_id))                 if db.get(Job, job_id) else None
            jobs_service.finish(db, job_id, "complete")
    except RunCancelled as exc:
        # The operator asked for this. Not a failure, and deliberately not
        # sealed: `run_pipeline` writes the manifest last, so stopping between
        # stages leaves the run unsealed by construction.
        jobs_service.mark_cancelled(run_id)
        with SessionLocal() as db:
            row = db.get(Run, run_id)
            row.status = "cancelled"
            row.finished_utc = utcnow()
            row.error = str(exc)[:1000]
            db.commit()
            jobs_service.finish(db, job_id, "cancelled")
    except Exception as exc:
        with SessionLocal() as db:
            row = db.get(Run, run_id)
            row.status = "failed"
            row.finished_utc = utcnow()
            row.error = f"{type(exc).__name__}: {exc}"[:1000]
            db.commit()
            jobs_service.finish(db, job_id, "failed",
                                f"{type(exc).__name__}: {exc}")
            # A failure is raised whoever started it: a scheduled run dying at
            # 03:00 is precisely what nobody is watching for.
            try:
                from backend.services import run_alerts

                if run_alerts.alert_run_outcome(db, run_id) is not None:
                    db.commit()
            except Exception as alert_exc:         # noqa: BLE001
                print(f"[run_alerts] {run_id}: {type(alert_exc).__name__}: {alert_exc}")
    finally:
        clear_cancel_check(run_id)
        with _run_lock:
            _running.discard(run_id)


@router.post("/investigations/{investigation_id}/run",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def start_run(request: Request, investigation_id: str, body: RunRequest,
              db: Session = Depends(get_db)):
    inv = db.get(Investigation, investigation_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")

    run_id = f"{investigation_id}-{datetime.now(timezone.utc):%H%M%S}"
    # Stamped now, not joined later: re-filing the investigation under a
    # different case must not rewrite what a sealed run was evidence for.
    db.add(Run(id=run_id, investigation_id=investigation_id, status="pending",
               scene_id=inv.scene_id, incident_id=inv.incident_id,
               registry_source="api"))
    scene_path = body.scene_path or inv.scene_path
    scene_meta_path = body.scene_meta_path or inv.scene_meta_path
    jobs_service.create(db, run_id, investigation_id,
                        {"scene_path": scene_path,
                         "scene_meta_path": scene_meta_path,
                         "engine": body.engine})
    audit_service.record(db, "run.start", request=request, resource=run_id,
                         detail=json.dumps({"investigation": investigation_id,
                                            "engine": body.engine}),
                         commit=False)
    db.commit()

    with _run_lock:
        _running.add(run_id)
    threading.Thread(
        target=_execute_run,
        args=(run_id, investigation_id, scene_path, scene_meta_path, body.engine),
        daemon=True).start()

    return {"run_id": run_id, "job_id": f"job-{run_id}", "status": "pending"}


# --------------------------------------------------------------------------
# jobs: progress and cancellation
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in ("running", "cancelling"):
        # Progress is read from the run's own status.json rather than stored on
        # a timer: the file is the truth, and a cached copy would go stale
        # exactly when someone is watching it.
        jobs_service.sync_progress(db, job)
        db.commit()
    return {"id": job.id, "run_id": job.run_id,
            "investigation_id": job.investigation_id, "status": job.status,
            "current_stage": job.current_stage,
            "stages_done": job.stages_done, "stages_total": job.stages_total,
            "created_utc": job.created_utc, "started_utc": job.started_utc,
            "finished_utc": job.finished_utc,
            "cancel_requested_utc": job.cancel_requested_utc,
            "error": job.error,
            "inputs": json.loads(job.inputs_json) if job.inputs_json else None}


@router.post("/jobs/{job_id}/cancel",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def cancel_job(request: Request, job_id: str, db: Session = Depends(get_db)):
    """Ask a run to stop at its next stage boundary.

    Cooperative by design. The pipeline finishes the stage it is in, writes that
    stage's status, and then stops -- so nothing is left half-written, and the
    run is never sealed.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    result = jobs_service.request_cancel(db, job_id, getattr(request.state, "user_id", None))
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "cannot cancel"))

    audit_service.record(db, "run.cancel", request=request, resource=job.run_id,
                         detail=json.dumps({"job": job_id}))
    return result


@router.post("/runs/{run_id}/rerun",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def rerun(request: Request, run_id: str, db: Session = Depends(get_db)):
    """Launch a NEW run with the inputs the old one was given.

    A new id, always. Re-running into the same id would overwrite the artefacts
    an investigation was concluded from and rewrite their hashes to match --
    which `provenance.assert_writable` refuses anyway (SS12).
    """
    old = db.get(Run, run_id)
    if old is None:
        raise HTTPException(404, "run not found")

    inputs = jobs_service.inputs_for_rerun(db, run_id)
    new_id = f"{old.investigation_id or 'rerun'}-{datetime.now(timezone.utc):%H%M%S}"
    if db.get(Run, new_id) is not None:
        new_id = f"{new_id}-{secrets.token_hex(2)}"

    db.add(Run(id=new_id, investigation_id=old.investigation_id, status="pending",
               scene_id=old.scene_id, incident_id=old.incident_id,
               registry_source="api"))
    jobs_service.create(db, new_id, old.investigation_id, inputs)
    audit_service.record(db, "run.rerun", request=request, resource=new_id,
                         detail=json.dumps({"reran": run_id,
                                            "inputs_from": inputs.get("source")}),
                         commit=False)
    db.commit()

    with _run_lock:
        _running.add(new_id)
    threading.Thread(
        target=_execute_run,
        args=(new_id, old.investigation_id, inputs.get("scene_path"),
              inputs.get("scene_meta_path"), inputs.get("engine", "auto")),
        daemon=True).start()

    return {"run_id": new_id, "job_id": f"job-{new_id}", "status": "pending",
            "reran": run_id, "inputs": inputs}


@router.get("/runs")
def list_runs(db: Session = Depends(get_db),
              q: Optional[str] = None,
              status: Optional[str] = None,
              incident: Optional[str] = None,
              region: Optional[str] = None,
              since: Optional[datetime] = Query(None, alias="from"),
              until: Optional[datetime] = Query(None, alias="to"),
              archived: bool = False,
              sort: str = "started_utc",
              order: str = Query("desc", pattern="^(asc|desc)$"),
              offset: int = Query(0, ge=0),
              limit: int = Query(50, ge=1, le=200)):
    """Searchable, sortable, paginated run history.

    Returns `{total, items}` rather than a bare list: without the total a UI
    cannot page, and the previous endpoint silently truncated at 50 with no
    way to tell a short page from the end of the data (audit RH-01..05).

    Archived runs are excluded by default and never deleted -- a run is
    evidence, so hiding it is a listing preference, not a lifecycle.
    """
    query = db.query(Run)
    if not archived:
        # NULL-safe: a row that predates the column has never been archived.
        # init_db() backfills the default, but the filter must not depend on it.
        query = query.filter(or_(Run.archived.is_(False), Run.archived.is_(None)))
    if status:
        query = query.filter(Run.status == status)
    if incident:
        query = query.filter(Run.incident_id == incident)
    if region:
        query = query.filter(Run.region == region)
    if since:
        query = query.filter(Run.started_utc >= since)
    if until:
        query = query.filter(Run.started_utc <= until)
    if q:
        like = f"%{q}%"
        query = query.filter(Run.id.ilike(like) | Run.scene_id.ilike(like)
                             | Run.investigation_id.ilike(like))

    total = query.count()
    column = RUN_SORTABLE.get(sort, Run.started_utc)
    query = query.order_by(column.desc() if order == "desc" else column.asc())
    rows = query.offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit,
            "items": [_run_dict(r) for r in rows]}


@router.post("/runs/{run_id}/archive",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def archive_run(request: Request, run_id: str, archived: bool = True,
                db: Session = Depends(get_db)):
    """Hide a run from the default listing. It is never deleted."""
    row = db.get(Run, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    row.archived = bool(archived)
    audit_service.record(db, "run.archive", request=request, resource=run_id,
                         detail=json.dumps({"archived": row.archived}), commit=False)
    db.commit()
    return _run_dict(row)


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    row = db.get(Run, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    payload = _run_dict(row)
    manifest = settings.runs_root / run_id / "manifest.json"
    if manifest.exists():
        payload["manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
    return payload


def _resolve_run_dir(run_id: str) -> Path:
    """Resolve a run directory from a user-supplied id.

    `run_id` reaches this from a URL path, so `../` must not be able to climb
    out of the runs directory -- resolve first, then check containment.
    """
    root = settings.runs_root.resolve()
    run_dir = (root / run_id).resolve()
    if not str(run_dir).startswith(str(root)):
        raise HTTPException(400, "invalid run id")
    if not run_dir.is_dir():
        raise HTTPException(404, "run not found")
    return run_dir


def summarise_outcome(row: Run, run_dir: Path) -> None:
    """Copy the run's headline result onto its DB row, once, at seal time.

    Denormalised so the history page can show what a run found without opening
    every artefact bundle. Read from the sealed files rather than recomputed,
    and silent on failure -- a summary that cannot be read is left null, never
    guessed, because a wrong top suspect on a listing is worse than a blank.
    """
    try:
        suspects_path = run_dir / "suspects.json"
        if suspects_path.exists():
            payload = json.loads(suspects_path.read_text(encoding="utf-8"))
            top = (payload.get("suspects") or [None])[0]
            if top:
                row.top_suspect_mmsi = int(top["mmsi"])
                row.top_score = float(top.get("total_score") or 0.0)
    except Exception:                              # noqa: BLE001
        pass
    try:
        slick_path = run_dir / "slick.geojson"
        if slick_path.exists():
            slick = json.loads(slick_path.read_text(encoding="utf-8"))
            areas = [f["properties"].get("area_km2") or 0.0
                     for f in slick.get("features", [])]
            if areas:
                row.slick_area_km2 = round(float(sum(areas)), 4)
    except Exception:                              # noqa: BLE001
        pass


def _run_dict(r: Run) -> dict:
    return {"run_id": r.id, "investigation_id": r.investigation_id,
            "incident_id": r.incident_id,
            "scene_id": r.scene_id, "status": r.status,
            "started_utc": r.started_utc, "finished_utc": r.finished_utc,
            "seconds": r.seconds, "detect_engine": r.detect_engine,
            "stages_total": r.stages_total, "stages_real": r.stages_real,
            "stages_mock": r.stages_mock, "stages_failed": r.stages_failed,
            # Denormalised outcome, so a 90-row history page does not open 90
            # artefact bundles to say what each run found.
            "top_suspect_mmsi": r.top_suspect_mmsi,
            "top_score": r.top_score,
            "slick_area_km2": r.slick_area_km2,
            "region": r.region,
            # Whether this row was observed by the API or rebuilt from the
            # sealed manifest afterwards. Published so a reader never has to
            # assume which -- see Run.registry_source.
            "registry_source": r.registry_source,
            "archived": bool(r.archived),
            "error": r.error}


def _lite_origin(payload: dict, max_particles: int = 1800) -> dict:
    """Subsample the hindcast particle cloud for map display.

    7,500 particle features are ~1.7 MB of JSON and a main-thread parse that
    visibly delays first render. Every ellipse and the metadata stay intact;
    particles are evenly subsampled PER TIMESTEP so the animation density is
    uniform. The contract file on disk is untouched -- this trims the wire
    format for a browser, nothing else.
    """
    # One pass, two buckets. This used to build `parts` and then derive
    # `others` with `f not in parts`, an O(n*m) scan comparing whole feature
    # dicts -- ~56M comparisons on a 7,500-particle cloud, which made
    # `?lite=true` roughly 20x SLOWER than serving the full file it was meant
    # to shrink (audit X-05).
    parts, others = [], []
    for f in payload.get("features", []):
        props = f.get("properties", {})
        kind = props.get("feature_type") or props.get("kind")
        (others if kind == "ellipse" else parts).append(f)

    if len(parts) <= max_particles:
        return payload
    stride = max(1, len(parts) // max_particles)
    return {**payload, "features": others + parts[::stride],
            "metadata": {**payload.get("metadata", {}),
                         "lite_subsampled": True,
                         "particles_full": len(parts)}}


ATTRIBUTION_WEIGHTS = (REPO_ROOT / "analysis_engines" / "config"
                       / "attribution_weights.yaml")

# The six scoring factors, in the engine's own vocabulary. `suspects.json`
# publishes two of them under different names, so the mapping is stated here
# rather than left for the UI to guess.
WEIGHT_FACTORS = ("proximity", "temporal", "trajectory", "anomaly", "ais_gap", "prior")
WEIGHT_PUBLISHED_AS = {"anomaly": "behaviour", "prior": "vessel_prior"}


def weights_profile() -> dict:
    """The attribution weights as written on disk, with an integrity verdict.

    Only the `weights:` block is summed. The same file also carries `gates:`,
    `priors:` and `scoring:` thresholds -- summing the document would always
    fail. `profile_hash` covers just the weights block, so a run can record
    which profile scored it without the hash moving when an unrelated
    threshold is tuned.
    """
    import hashlib

    import yaml

    if not ATTRIBUTION_WEIGHTS.exists():
        raise HTTPException(503, f"attribution weights not found at "
                                 f"{ATTRIBUTION_WEIGHTS.name}")
    doc = yaml.safe_load(ATTRIBUTION_WEIGHTS.read_text(encoding="utf-8")) or {}
    block = doc.get("weights") or {}

    values = {f: float(block[f]) for f in WEIGHT_FACTORS if f in block}
    unknown = sorted(set(block) - set(WEIGHT_FACTORS))
    missing = [f for f in WEIGHT_FACTORS if f not in block]
    total = round(sum(values.values()), 12)
    valid = not missing and not unknown and abs(total - 1.0) <= 1e-6

    canonical = json.dumps({k: block[k] for k in sorted(values)}, sort_keys=True)
    return {
        "profile": "default-v1",
        "profile_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "source": ATTRIBUTION_WEIGHTS.relative_to(REPO_ROOT).as_posix(),
        "weights": values,
        "published_as": WEIGHT_PUBLISHED_AS,
        "sum": total,
        "validated": valid,
        "problems": (
            ([f"missing factor(s): {missing}"] if missing else [])
            + ([f"unknown weight(s): {unknown}"] if unknown else [])
            + ([f"weights sum to {total}, not 1.0"] if abs(total - 1.0) > 1e-6 else [])
        ),
        # Stated rather than silently relied upon: if the file ever stops
        # summing to 1.0, Engine C renormalises and warns instead of refusing,
        # so scores stay comparable but no longer match the file as written.
        "on_invalid": "Engine C renormalises and records a warning in the run; "
                      "this endpoint reports the file as written.",
    }


# The three gates, in the order the UI narrates them. These strings are the
# engine's own `filter_reason` values (analysis_engines/.../gates.py), not
# prose -- matching on the humanised sentence would break the moment someone
# improved the wording.
GATE_ORDER = (
    ("after_spatial", "outside origin region"),
    ("after_temporal", "outside time window"),
    ("after_trajectory", "course incompatible with slick axis"),
)


@router.get("/runs/{run_id}/funnel")
def run_funnel(run_id: str):
    """How many vessels survived each stage, and why the rest did not.

    Six counts rather than the four the plan first sketched, because the AIS
    index and the three gates are different things and collapsing them hides
    where the population actually fell away.

    One honesty note that the UI must not lose: the gates are evaluated
    TOGETHER, not in sequence. A vessel can fail several at once, and
    `failed_gates` lists all of them. The cumulative counts below therefore
    answer "how many would remain if the gates were applied in this order",
    which is a presentation of one evaluation -- not a record of three passes.
    `exclusive` reports each gate's own toll independent of order.
    """
    run_dir = _resolve_run_dir(run_id)
    suspects_path = run_dir / "suspects.json"
    if not suspects_path.exists():
        raise HTTPException(404, f"run {run_id} has no suspects.json")

    try:
        payload = json.loads(suspects_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"malformed suspects.json: {exc}")

    suspects = payload.get("suspects") or []
    filtered = payload.get("filtered_out") or []
    considered = int(payload.get("total_vessels_considered",
                                 len(suspects) + len(filtered)))

    # `found` is what AIS supplied before the spatial index pruned it. The
    # index writes it into the manifest; absent that we say so rather than
    # guessing, because a fabricated top-of-funnel would overstate the
    # filtering the system actually did.
    found = None
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for stage in manifest.get("stages", []):
                for warning in stage.get("warnings", []) or []:
                    match = re.search(r"AIS index: (\d+) vessels", str(warning))
                    if match:
                        found = int(match.group(1))
        except (json.JSONDecodeError, OSError):
            found = None

    gates_of = {}
    for row in filtered:
        gates = row.get("failed_gates")
        if not gates and row.get("filter_reason"):
            gates = [row["filter_reason"]]
        gates_of[row.get("mmsi")] = set(gates or [])

    unknown_gate = sum(1 for g in gates_of.values() if not g)

    steps, survivors = [], set(gates_of) | {s.get("mmsi") for s in suspects}
    exclusive = {}
    for key, reason in GATE_ORDER:
        removed = {m for m in survivors if reason in gates_of.get(m, ())}
        survivors -= removed
        steps.append((key, len(survivors)))
        exclusive[key] = sum(1 for g in gates_of.values() if reason in g)

    histogram = {}
    for row in filtered:
        label = row.get("filter_reason") or "unclassified"
        histogram[label] = histogram.get(label, 0) + 1

    return {
        "run_id": run_id,
        "found": found,
        "found_note": (None if found is not None else
                       "not recorded by this run; the AIS index writes it into "
                       "the manifest warnings"),
        "indexed": considered,
        **dict(steps),
        "candidates": len(suspects),
        "filtered": len(filtered),
        "reasons_histogram": histogram,
        "exclusive_by_gate": exclusive,
        "unclassified": unknown_gate,
        "gates_are_sequential": False,
        "note": "Gates are evaluated together; a vessel may fail several. The "
                "cumulative counts show one ordering of a single evaluation.",
        "source": payload.get("source"),
    }


@router.get("/attribution/weights")
def attribution_weights():
    """The weight profile scoring uses, read-only.

    Editing weights is deliberately not offered: the same file is a frozen CLI
    input, and a mid-flight change would silently alter what previously sealed
    runs mean. Versioned profiles are post-SIH work.
    """
    return weights_profile()


@router.get("/layers/{run_id}/{layer}")
def get_layer(run_id: str, layer: str, lite: bool = False):
    """Serve one contract file from a run directory."""
    if layer not in LAYER_FILES:
        raise HTTPException(404, f"unknown layer '{layer}'")

    root = settings.runs_root.resolve()
    target = (root / run_id / LAYER_FILES[layer]).resolve()
    # run_id is user input; make sure it cannot climb out of the runs directory.
    if not str(target).startswith(str(root)):
        raise HTTPException(400, "invalid run id")
    if not target.exists():
        raise HTTPException(404, f"layer '{layer}' not present in this run")

    if target.suffix in (".json", ".geojson"):
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            if lite and layer == "origin_cloud":
                payload = _lite_origin(payload)
            if layer == "scene_meta" and isinstance(payload, dict):
                # Additive, on the response only: the sealed file is untouched.
                from backend.api.sar_database import basis_for_meta
                payload["basis"] = basis_for_meta(payload)
            return JSONResponse(payload)
        except Exception as exc:
            raise HTTPException(
                422, f"malformed contract file {target.name}: "
                     f"{type(exc).__name__}: {str(exc)[:200]}")
    return FileResponse(target)


# Contract artefacts included in an investigation export. Deliberately NOT
# "everything in the run dir": raw rasters (sigma0 GeoTIFF, mask, PNGs) and
# engine-native scratch files stay out — the bundle is the eight-contract
# story of the run, small enough to email to an investigator.
EXPORT_FILES = [
    "manifest.json",
    "status.json",
    "scene_meta.json",
    "detect_response.json",
    "slick.geojson",
    "origin_cloud.geojson",
    "forecast.geojson",
    "suspects.json",
    "vessels.parquet",
]


@router.get("/runs/{run_id}/export")
def export_run(request: Request, run_id: str, db: Session = Depends(get_db)):
    """Stream a zip of the run's contract artefacts (GeoJSON bundle).

    Adds a derived vessels.geojson (same conversion the map uses) so the
    bundle opens directly in QGIS/kepler without a parquet reader; the
    contract-canonical vessels.parquet is still included untouched.
    """
    run_dir = _resolve_run_dir(run_id)
    # Leaving with a copy of the artefacts is exactly the kind of action the
    # record should contain.
    audit_service.record(db, "data.export", request=request, resource=run_id,
                         detail="run artefact bundle downloaded")

    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in EXPORT_FILES:
            f = run_dir / name
            if f.exists():
                zf.write(f, arcname=f"{run_id}/{name}")
                added += 1
        try:
            from backend.api.analytics import vessels_geojson

            payload = vessels_geojson(run_id, 2000)
            zf.writestr(f"{run_id}/vessels.geojson",
                        json.dumps(payload, default=str))
        except Exception:
            pass  # no vessels in this run; the bundle still ships without them

    if not added:
        raise HTTPException(404, "no contract artefacts in this run yet")

    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="oceantrace_{run_id}.zip"'})


# --------------------------------------------------------------------------
# provenance and the human decision (design doc v2 §12, §14, Stage 9)
# --------------------------------------------------------------------------


@router.get("/runs/{run_id}/verify")
def verify_run_artefacts(run_id: str):
    """Re-hash this run's artefacts and compare them against its manifest.

    §12's immutability rule is only worth anything if somebody can check it.
    This is that check, exposed so the UI can show a "verified" badge next to a
    run and an operator can spot-check an old investigation from a browser.

    Always 200: "three artefacts changed" is an answer, not a server error, and
    the UI needs to render it rather than swallow an exception.
    """
    return provenance.verify_run(_resolve_run_dir(run_id))


@router.post("/runs/{run_id}/decisions", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
def record_decision(request: Request, run_id: str, body: DecisionCreate,
                    db: Session = Depends(get_db)):
    """STAGE 9 -- record what a human concluded. Standing Rule 8.

    The pipeline ranks candidates and stops. Everything after that -- accept,
    reject, annotate -- happens here, and is stored with the run so the
    investigation can be reconstructed and so a feedback corpus accumulates.

    Two things are captured beyond the verdict itself, both because §14 demands
    reproducibility rather than just a timestamp:

      * `artefact_digest` -- WHICH artefacts the analyst was looking at, so a
        later re-run cannot silently change the evidence behind a past decision;
      * the suspect's rank, score and the weights as displayed, for the same
        reason. Weights live in a config file that will change again before the
        finale; a decision made under the old ones must still read correctly.
    """
    row = db.get(Run, run_id)
    if row is None:
        raise HTTPException(404, "run not found")

    run_dir = _resolve_run_dir(run_id)
    digest, rank, score, weights = None, None, None, None

    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        digest = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "artefact_digest")

    suspects_path = run_dir / "suspects.json"
    if suspects_path.is_file():
        report = json.loads(suspects_path.read_text(encoding="utf-8"))
        weights = json.dumps(report.get("weights")) if report.get("weights") else None
        if body.mmsi is not None:
            match = next((sp for sp in report.get("suspects", [])
                          if sp.get("mmsi") == body.mmsi), None)
            if match is None:
                # Not a 404: an analyst rejecting a vessel the ranking MISSED is
                # exactly the feedback worth keeping. Record it, unranked.
                pass
            else:
                rank = match.get("rank")
                score = match.get("total_score")

    decision = Decision(
        run_id=run_id, investigation_id=row.investigation_id, mmsi=body.mmsi,
        suspect_rank=rank, total_score_at_decision=score,
        verdict=body.verdict, note=body.note, actor=body.actor,
        artefact_digest=digest, weights_used=weights)
    db.add(decision)
    # The credential audit log is also the investigation audit log: §14 wants
    # one answer to "who ran what, when, and what they concluded".
    # `body.actor` is no longer trusted for the audit row: an actor a client
    # can choose is not evidence. It stays on the Decision itself as the
    # analyst's own label, while the audit names the authenticated account.
    audit_service.record(
        db, f"decision.{body.verdict}", request=request, resource=run_id,
        provider="pipeline", field=run_id,
        detail=json.dumps({"mmsi": body.mmsi, "rank": rank,
                           "claimed_actor": body.actor,
                           "note": (body.note or "")[:200]}),
        commit=False)
    db.commit()
    return _decision_dict(decision)


@router.get("/runs/{run_id}/decisions")
def list_decisions(run_id: str, db: Session = Depends(get_db)):
    """Every decision recorded against this run, oldest first.

    Append-only by convention: a changed mind is a new row, so this list is the
    sequence of what the analysts concluded and when, not just the latest view.
    """
    rows = (db.query(Decision).filter(Decision.run_id == run_id)
            .order_by(Decision.decided_utc.asc()).all())
    return [_decision_dict(d) for d in rows]


@router.get("/decisions")
def all_decisions(db: Session = Depends(get_db), limit: int = Query(100, le=500)):
    """The feedback corpus, newest first (§4 Stage 9)."""
    rows = (db.query(Decision).order_by(Decision.decided_utc.desc())
            .limit(limit).all())
    return [_decision_dict(d) for d in rows]


def _decision_dict(d: Decision) -> dict:
    return {"id": d.id, "run_id": d.run_id,
            "investigation_id": d.investigation_id, "mmsi": d.mmsi,
            "suspect_rank": d.suspect_rank,
            "total_score_at_decision": d.total_score_at_decision,
            "verdict": d.verdict, "note": d.note, "actor": d.actor,
            "decided_utc": d.decided_utc,
            "artefact_digest": d.artefact_digest,
            "weights_used": json.loads(d.weights_used) if d.weights_used else None}


# --------------------------------------------------------------------------
# monitoring
# --------------------------------------------------------------------------


@router.get("/apis/status")
def api_status(db: Session = Depends(get_db)):
    """Everything the monitoring page renders, in one call."""
    out = []
    for spec in PROVIDERS:
        row = db.get(ApiProvider, spec["name"])
        if row is None:
            continue
        chain = (row.chain or "").split(",") if row.chain else spec["chain"]
        recent = (db.query(ApiCall)
                    .filter(ApiCall.provider == spec["name"])
                    .order_by(ApiCall.occurred_utc.desc()).limit(20).all())
        ok = sum(c.status == "ok" for c in recent)
        out.append({
            "provider": row.name, "purpose": row.purpose, "owner": row.owner,
            "kind": row.kind, "status": row.status,
            "last_code": row.last_code, "last_latency_ms": row.last_latency_ms,
            "last_success_utc": row.last_success_utc,
            "last_failure_utc": row.last_failure_utc,
            "last_error_class": row.last_error_class,
            "chain": chain,
            "active_provider": health.active_member(db, chain),
            "needs_credentials": row.needs_credentials,
            "has_credentials": row.has_credentials,
            "circuit_open": health.circuit_is_open(row),
            "recent_calls": len(recent),
            "recent_success_rate": (ok / len(recent)) if recent else None,
        })
    return {"generated_utc": utcnow(), "providers": out}


@router.post("/apis/{provider}/test",
             dependencies=[Depends(require_role("analyst"))])
def test_provider(provider: str, db: Session = Depends(get_db)):
    if provider not in PROVIDER_BY_NAME:
        raise HTTPException(404, "unknown provider")
    return health.probe(db, provider)


@router.post("/apis/test-all",
             dependencies=[Depends(require_role("analyst"))])
def test_all(db: Session = Depends(get_db)):
    return {"results": health.probe_all(db)}


@router.get("/apis/{provider}/calls")
def provider_calls(provider: str, db: Session = Depends(get_db),
                   limit: int = Query(50, le=500)):
    rows = (db.query(ApiCall).filter(ApiCall.provider == provider)
              .order_by(ApiCall.occurred_utc.desc()).limit(limit).all())
    return [{"occurred_utc": c.occurred_utc, "endpoint": c.endpoint,
             "status": c.status, "http_code": c.http_code,
             "latency_ms": c.latency_ms, "error_class": c.error_class,
             "error_detail": c.error_detail} for c in rows]


# --------------------------------------------------------------------------
# key management (admin)
# --------------------------------------------------------------------------


@router.get("/keys")
def list_keys(db: Session = Depends(get_db), actor: str = Depends(require_admin)):
    """Masked view of every credential field the system knows about."""
    available, how = encryption_available()
    out = []
    for provider, fields in health.CREDENTIAL_FIELDS.items():
        for field in fields:
            row = (db.query(ApiKey)
                     .filter(ApiKey.provider == provider, ApiKey.field == field)
                     .order_by(ApiKey.updated_utc.desc()).first())
            env_value = resolve_credential(db, provider, field)
            out.append({
                "provider": provider, "field": field,
                "configured": bool(env_value),
                "masked": mask(env_value) if env_value else None,
                "source": "database" if row is not None else
                          ("environment" if env_value else "unset"),
                "encrypted": is_encrypted(row.ciphertext) if row else None,
                "updated_utc": row.updated_utc if row else None,
            })
    return {"encryption": {"available": available, "method": how},
            "keys": out}


@router.put("/keys")
def set_key(request: Request, body: KeyUpdate, db: Session = Depends(get_db),
            actor: str = Depends(require_admin)):
    if body.provider not in health.CREDENTIAL_FIELDS:
        raise HTTPException(404, "unknown provider")
    if body.field not in health.CREDENTIAL_FIELDS[body.provider]:
        raise HTTPException(400, f"unknown field for {body.provider}")

    db.add(ApiKey(provider=body.provider, field=body.field,
                  ciphertext=encrypt(body.value),
                  last_four=last_four(body.value), updated_by=actor))
    # The audit log records THAT a field changed, never the value.
    audit_service.record(db, "key.set", request=request, provider=body.provider,
                         field=body.field, resource=body.provider,
                         actor=actor, detail="credential updated", commit=False)
    row = db.get(ApiProvider, body.provider)
    if row is not None:
        row.has_credentials = health.has_credentials(db, body.provider)
    db.commit()

    available, how = encryption_available()
    return {"provider": body.provider, "field": body.field,
            "masked": mask(body.value), "encrypted_at_rest": available,
            "warning": None if available else
                       f"stored WITHOUT encryption: {how}"}


@router.post("/keys/{provider}/test")
def test_key(provider: str, db: Session = Depends(get_db),
             actor: str = Depends(require_admin)):
    """Real authenticated probe, reporting the exact failure class."""
    if provider not in PROVIDER_BY_NAME:
        raise HTTPException(404, "unknown provider")
    if not health.has_credentials(db, provider):
        missing = health.missing_credentials(db, provider)
        return {"provider": provider, "ok": False, "error_class": "AUTH_FAILED",
                "detail": f"missing credential field(s): {missing}"}
    result = health.probe(db, provider)
    return {"provider": provider, "ok": result.get("status") == "WORKING",
            **result}


@router.get("/keys/audit")
def key_audit(db: Session = Depends(get_db), actor: str = Depends(require_admin),
              limit: int = Query(100, le=500)):
    rows = (db.query(AuditLog).order_by(AuditLog.occurred_utc.desc())
              .limit(limit).all())
    return [{"occurred_utc": r.occurred_utc, "action": r.action,
             "provider": r.provider, "field": r.field, "actor": r.actor,
             "detail": r.detail} for r in rows]
