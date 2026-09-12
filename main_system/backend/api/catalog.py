"""Data catalogue, model registry and host health.

    GET /api/catalog        every provider with its honest state and coverage
    GET /api/models         what is deployed, what is experimental, and hashes
    GET /api/system/health  measured host and service state

Three things these endpoints refuse to do.

**They do not report REACHABLE as WORKING.** An unauthenticated 200 proves a
host answered. Whether it will serve *us* data is a different question, and the
catalogue answers it separately -- `status` for what was measured, `probe` for
how it was measured.

**They do not hide what is not deployed.** Sentinel-2 and live AIS have
adapters and no pipeline wiring. Omitting them would suggest optical and live
AIS were never considered; showing them beside working providers would suggest
they are available. Both are false, so they appear with `NOT_DEPLOYED` and the
reason.

**They do not invent tiles.** `/api/system/health` reports CPU, memory, disk,
the database, the model files and a provider summary -- because those are
measured here. There is no queue depth, no broker status and no cluster
health, because this system has no queue, no broker and no cluster. A
dashboard tile for a component that does not exist is not a placeholder; it is
a claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.config import (PROVIDER_BY_NAME, PROVIDER_COVERAGE, PROVIDERS,
                                 get_settings)
from backend.models.db import ApiProvider, Run, get_db
from backend.services.providers import health

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parents[3]
WEIGHTS_DIR = REPO_ROOT / "main_system" / "backend" / "services" / "detection" / "weights"
MODEL_CARD = WEIGHTS_DIR / "model_card.md"

_PROCESS_START = time.time()


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------

@router.get("/catalog")
def data_catalog(db: Session = Depends(get_db)):
    """Every provider: what it is for, whether it works, and what it covers."""
    rows = {r.name: r for r in db.query(ApiProvider).all()}
    out: List[Dict[str, Any]] = []

    for spec in PROVIDERS:
        row = rows.get(spec["name"])
        deployment = spec.get("deployment", "DEPLOYED")
        status = ("NOT_DEPLOYED" if deployment == "NOT_DEPLOYED"
                  else (row.status if row else "UNKNOWN"))
        entry = {
            "name": spec["name"],
            "purpose": spec["purpose"],
            "kind": spec["kind"],
            "owner": spec["owner"],
            "chain": spec["chain"],
            "deployment": deployment,
            "status": status,
            # How we know. A status without its probe kind is unfalsifiable:
            # WORKING from a functional probe and REACHABLE from a ping are
            # different claims and must not render identically.
            "probe": ("functional" if spec["name"] in health.FUNCTIONAL_PROBES
                      else "reachability" if spec["name"] in health.PROBES
                      else "none"),
            "probe_proves": (health.FUNCTIONAL_PROBES.get(spec["name"], {})
                             .get("proves")),
            "credentials": health.credential_state(db, spec["name"]),
            "coverage": PROVIDER_COVERAGE.get(spec["name"]),
            "last_success_utc": row.last_success_utc if row else None,
            "last_error_class": row.last_error_class if row else None,
            "latency_ms": row.last_latency_ms if row else None,
        }
        if deployment == "NOT_DEPLOYED":
            entry["reason"] = spec.get("not_deployed_reason")
        out.append(entry)

    return {
        "providers": out,
        "vocabulary": {
            "WORKING": "an authenticated or functional probe succeeded",
            "REACHABLE": "the host answered an unauthenticated request; this "
                         "does not prove it will serve us data",
            "DEGRADED": "reachable and refusing us, or failing intermittently",
            "UNCONFIGURED": "credentials are required and absent",
            "FAILED": "unreachable",
            "NOT_DEPLOYED": "the adapter exists but nothing consumes it",
        },
        "credentials_vocabulary": {
            "configured": "every field of an accepted credential set is present",
            "missing": "this provider needs credentials and some are absent",
            "n_a": "this provider takes no credentials -- there is nothing to "
                   "configure, so neither a tick nor a cross would be true",
        },
    }


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------

def _sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _onnx_metadata(path: Path) -> Dict[str, str]:
    try:
        import onnx

        model = onnx.load(str(path), load_external_data=False)
        return {p.key: p.value for p in model.metadata_props}
    except Exception:                                   # noqa: BLE001
        return {}


@router.get("/models")
def models(db: Session = Depends(get_db)):
    """What is deployed, what is experimental, and the hash of each file.

    The drift ML residual appears here as EXPERIMENTAL with its measured
    outcome. It was evaluated, it did not help, and it is disabled. Leaving it
    out would hide work that was done; listing it without the verdict would
    imply it is in use. Neither is the truth.
    """
    entries: List[Dict[str, Any]] = []

    for kind, filename in (("segment", "segment.onnx"), ("screen", "screen.onnx")):
        path = WEIGHTS_DIR / filename
        if not path.exists():
            entries.append({"kind": kind, "file": filename, "status": "MISSING",
                            "note": "the weights file is not in this checkout"})
            continue
        meta = _onnx_metadata(path)
        entries.append({
            "kind": kind,
            "file": filename,
            "status": "DEPLOYED",
            # `model_version` is the key the exporter embeds and the key the
            # sealed manifest reads (`run._model_records`). Guessing a
            # different one silently falls back to the filename, so this page
            # would name the model "segment" while every manifest named the
            # checkpoint -- two answers to one question.
            "name": meta.get("model_version") or path.stem,
            "config_fingerprint": meta.get("config_fingerprint"),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "metadata": meta,
            "model_card": "backend/services/detection/weights/model_card.md"
            if MODEL_CARD.exists() else None,
        })

    entries.append({
        "kind": "drift_residual",
        "file": None,
        "status": "EXPERIMENTAL",
        "name": "drift ML residual correction (v1)",
        "note": "evaluated negative; disabled",
        "detail": ("A learned residual on top of the physics hindcast was "
                   "trained and evaluated. It did not improve origin accuracy "
                   "over the physics alone, so it is not applied to any run. "
                   "The hindcast is physics: no machine learning contributes "
                   "to the origin estimate."),
        "applied": False,
    })

    # The v2 candidate. Listed with its REAL state, which is neither deployed
    # nor disabled-on-evidence: it is implemented and unmeasured. Collapsing
    # that into "experimental" alongside v1 would imply it had also been
    # evaluated and lost, which is a benchmark nobody ran.
    from backend.services import hindcast_benchmark

    _v2 = next(c for c in hindcast_benchmark.benchmark()["candidates"]
               if c["candidate"] == "ml_origin_correction")
    entries.append({
        "kind": "drift_origin_correction",
        "file": _v2["weights_path"] if _v2["weights_present"] else None,
        "status": _v2["status"],
        "name": _v2["name"],
        "note": _v2.get("note"),
        "detail": ("Targets the origin LOCATION -- the quantity the problem "
                   "statement asks for -- with one bounded correction to the "
                   "finished hindcast rather than one per step, which is the "
                   "fix the v1 root-cause analysis derived. See "
                   "/api/models/hindcast for the benchmark and the selection "
                   "basis."),
        "applied": _v2["applied_to_runs"],
        "sha256": _v2["sha256"],
    })

    runs_with_models = (db.query(Run)
                        .filter(Run.manifest_path.isnot(None))
                        .order_by(Run.started_utc.desc()).limit(5).all())
    recent = []
    for run in runs_with_models:
        manifest = Path(run.manifest_path) if run.manifest_path else None
        if manifest and manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                recent.append({"run_id": run.id,
                               "models": payload.get("models", []),
                               "code_git_sha": payload.get("code_git_sha")})
            except (json.JSONDecodeError, OSError):
                continue

    return {"models": entries, "recent_runs": recent,
            "note": "Deployed model identity comes from the ONNX metadata, so a "
                    "re-export cannot leave this page naming the previous "
                    "checkpoint."}


@router.get("/models/hindcast")
def hindcast_selection():
    """The ML-vs-physics hindcast benchmark, and why the primary is primary.

    Exposed as its own endpoint because the problem statement requires the
    selected model AND the benchmark result to be visible in the UI, and
    because the interesting field is `selection_basis`: physics being primary
    means something different when ML lost a benchmark than when ML was never
    measured, and a page that rendered those identically would be claiming a
    comparison nobody ran.
    """
    from backend.services import hindcast_benchmark

    return hindcast_benchmark.benchmark()


# --------------------------------------------------------------------------
# host health
# --------------------------------------------------------------------------

@router.get("/system/health")
def system_health(db: Session = Depends(get_db)):
    """Measured host and service state. No tiles for components we do not run."""
    settings = get_settings()

    host: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "uptime_seconds": round(time.time() - _PROCESS_START, 1),
    }
    try:
        import psutil

        host["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        host["memory_total_mb"] = round(memory.total / 1e6)
        host["memory_used_percent"] = memory.percent
        host["measured_by"] = "psutil"
    except Exception:                                   # noqa: BLE001
        # Absent, not zero. A CPU reading of 0% would be a measurement we did
        # not make.
        host["measured_by"] = None
        host["note"] = "psutil is not installed; CPU and memory were not measured"

    try:
        usage = shutil.disk_usage(str(settings.data_root))
        disk = {"total_gb": round(usage.total / 1e9, 1),
                "free_gb": round(usage.free / 1e9, 1),
                "path": str(settings.data_root)}
    except OSError as exc:
        disk = {"error": str(exc)}

    try:
        run_count = db.query(Run).count()
        database = {"ok": True, "runs": run_count,
                    "url_scheme": str(settings.database_url).split(":", 1)[0]}
    except Exception as exc:                            # noqa: BLE001
        database = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    model_files = []
    for filename in ("segment.onnx", "screen.onnx"):
        path = WEIGHTS_DIR / filename
        model_files.append({"file": filename, "present": path.exists(),
                            "bytes": path.stat().st_size if path.exists() else 0})

    rows = db.query(ApiProvider).all()
    summary: Dict[str, int] = {}
    for row in rows:
        key = row.status or "UNKNOWN"
        summary[key] = summary.get(key, 0) + 1

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": host,
        "disk": disk,
        "database": database,
        "models": model_files,
        "providers": summary,
        "not_reported": [
            "queue depth -- this system has no queue",
            "message broker -- there is none",
            "cluster health -- this runs as a single process",
            "object storage -- artefacts are on the local filesystem",
        ],
        "note": "Every field above is measured on this host at request time. "
                "Components this system does not run are listed under "
                "not_reported rather than shown as empty tiles.",
    }
