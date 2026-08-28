"""REST API for the OceanTrace main system.

    /api/investigations          create, list, run
    /api/runs/{id}               status, manifest, provenance
    /api/layers/{run}/{name}     serve a contract file to the UI
    /api/apis/...                monitoring page: status, history, test-now
    /api/keys/...                admin only: masked list, set, test

Two invariants worth stating because they are easy to break later:

  * layer files are served only from inside the run directory, resolved and
    checked -- a run id is user input and `../` must not escape;
  * key endpoints never return a plaintext credential, only `••••1234`.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.config import PROVIDER_BY_NAME, PROVIDERS, get_settings
from backend.core.security import (encryption_available, encrypt, is_encrypted,
                                   last_four, mask, resolve_credential,
                                   verify_admin)
from backend.models.db import (ApiCall, ApiKey, ApiProvider, AuditLog,
                               Investigation, Run, get_db, utcnow)
from backend.services.providers import health

router = APIRouter()
settings = get_settings()

# Relative scene paths are resolved against the repo, not the server's CWD,
# so a path means the same thing from the API, the CLI and a test.
REPO_ROOT = settings.data_root.parent

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
    name: str = Field(min_length=1, max_length=200)
    scene_path: Optional[str] = None
    scene_meta_path: Optional[str] = None
    notes: Optional[str] = None


class RunRequest(BaseModel):
    engine: str = Field(default="auto", pattern="^(auto|ml|threshold_fallback)$")
    scene_path: Optional[str] = None
    scene_meta_path: Optional[str] = None


class KeyUpdate(BaseModel):
    provider: str
    field: str
    value: str = Field(min_length=1)


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> str:
    if not verify_admin(x_admin_token):
        raise HTTPException(status_code=401, detail="admin token required")
    return "admin"


# --------------------------------------------------------------------------
# investigations + runs
# --------------------------------------------------------------------------


@router.post("/investigations")
def create_investigation(body: InvestigationCreate, db: Session = Depends(get_db)):
    inv = Investigation(
        id=f"inv-{uuid.uuid4().hex[:10]}", name=body.name,
        scene_path=body.scene_path, notes=body.notes)
    if body.scene_meta_path:
        meta_path = Path(body.scene_meta_path)
        if not meta_path.is_absolute():
            meta_path = REPO_ROOT / meta_path
        if not meta_path.exists():
            raise HTTPException(400, f"scene_meta not found: {body.scene_meta_path}")
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


def _execute_run(run_id: str, investigation_id: Optional[str],
                 scene: Optional[str], scene_meta: Optional[str], engine: str):
    """Run the pipeline in a worker thread and record the outcome."""
    from backend.models.db import SessionLocal
    from backend.services.pipeline.run import MOCKS, run_pipeline

    with SessionLocal() as db:
        row = db.get(Run, run_id)
        row.status = "running"
        db.commit()

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
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            row = db.get(Run, run_id)
            row.status = "failed"
            row.finished_utc = utcnow()
            row.error = f"{type(exc).__name__}: {exc}"[:1000]
            db.commit()
    finally:
        with _run_lock:
            _running.discard(run_id)


@router.post("/investigations/{investigation_id}/run")
def start_run(investigation_id: str, body: RunRequest, db: Session = Depends(get_db)):
    inv = db.get(Investigation, investigation_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")

    run_id = f"{investigation_id}-{datetime.now(timezone.utc):%H%M%S}"
    db.add(Run(id=run_id, investigation_id=investigation_id, status="pending",
               scene_id=inv.scene_id))
    db.commit()

    with _run_lock:
        _running.add(run_id)
    threading.Thread(
        target=_execute_run,
        args=(run_id, investigation_id, body.scene_path or inv.scene_path,
              body.scene_meta_path or inv.scene_meta_path, body.engine),
        daemon=True).start()

    return {"run_id": run_id, "status": "pending"}


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), limit: int = Query(50, le=200)):
    rows = db.query(Run).order_by(Run.started_utc.desc()).limit(limit).all()
    return [_run_dict(r) for r in rows]


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


def _run_dict(r: Run) -> dict:
    return {"run_id": r.id, "investigation_id": r.investigation_id,
            "scene_id": r.scene_id, "status": r.status,
            "started_utc": r.started_utc, "finished_utc": r.finished_utc,
            "seconds": r.seconds, "detect_engine": r.detect_engine,
            "stages_total": r.stages_total, "stages_real": r.stages_real,
            "stages_mock": r.stages_mock, "stages_failed": r.stages_failed,
            "error": r.error}


def _lite_origin(payload: dict, max_particles: int = 1800) -> dict:
    """Subsample the hindcast particle cloud for map display.

    7,500 particle features are ~1.7 MB of JSON and a main-thread parse that
    visibly delays first render. Every ellipse and the metadata stay intact;
    particles are evenly subsampled PER TIMESTEP so the animation density is
    uniform. The contract file on disk is untouched -- this trims the wire
    format for a browser, nothing else.
    """
    feats = payload.get("features", [])
    parts = [f for f in feats
             if (f.get("properties", {}).get("feature_type")
                 or f.get("properties", {}).get("kind")) != "ellipse"]
    others = [f for f in feats if f not in parts]
    if len(parts) <= max_particles:
        return payload
    stride = max(1, len(parts) // max_particles)
    return {**payload, "features": others + parts[::stride],
            "metadata": {**payload.get("metadata", {}),
                         "lite_subsampled": True,
                         "particles_full": len(parts)}}


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
            return JSONResponse(payload)
        except Exception as exc:
            raise HTTPException(
                422, f"malformed contract file {target.name}: "
                     f"{type(exc).__name__}: {str(exc)[:200]}")
    return FileResponse(target)


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


@router.post("/apis/{provider}/test")
def test_provider(provider: str, db: Session = Depends(get_db)):
    if provider not in PROVIDER_BY_NAME:
        raise HTTPException(404, "unknown provider")
    return health.probe(db, provider)


@router.post("/apis/test-all")
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
def set_key(body: KeyUpdate, db: Session = Depends(get_db),
            actor: str = Depends(require_admin)):
    if body.provider not in health.CREDENTIAL_FIELDS:
        raise HTTPException(404, "unknown provider")
    if body.field not in health.CREDENTIAL_FIELDS[body.provider]:
        raise HTTPException(400, f"unknown field for {body.provider}")

    db.add(ApiKey(provider=body.provider, field=body.field,
                  ciphertext=encrypt(body.value),
                  last_four=last_four(body.value), updated_by=actor))
    # The audit log records THAT a field changed, never the value.
    db.add(AuditLog(action="key.set", provider=body.provider, field=body.field,
                    actor=actor, detail="credential updated"))
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
