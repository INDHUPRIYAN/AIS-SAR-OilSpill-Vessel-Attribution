"""Launching, tracking and cancelling pipeline runs.

A run used to be a bare `threading.Thread(target=_execute_run, daemon=True)`.
That works right up to the first time somebody launches one against the wrong
scene: nothing reports which stage is executing, and nothing can stop it. On the
mock raster that is a few seconds of annoyance. On a real Sentinel-1 frame it is
six minutes, and the only ways out were to wait or restart the server -- which
also kills every other run in flight.

Three things this adds, and one it deliberately does not.

**A job row.** `jobs` records what was launched, with which inputs, which stage
it is on, and how it ended. `current_stage` is read from the run's own
`status.json` rather than guessed, and there is no percentage: the five stages
differ by two orders of magnitude in duration (detect ~150 s, drift ~4 s), so a
percentage would be a confident-looking lie. A stage name and a count are true.

**Cooperative cancellation.** `request_cancel` sets a flag; the pipeline checks
it at each stage boundary and raises `RunCancelled`. Nothing is killed
mid-write. A cancelled run is left UNSEALED -- no `manifest.json` -- because
sealing is what makes a run quotable as evidence, and a run that stopped
half-way is not evidence of anything. `status.json` is rewritten to say
`cancelled` so the difference is visible rather than inferred from an absence.

**Reproducible inputs.** The launch inputs are stored on the job, so a rerun
repeats what actually ran instead of reconstructing it from the manifest of a
run that may have fallen back to a different scene.

What it does not add is a queue or a worker pool. One pipeline run saturates
this machine's CPU and GPU; a queue would only let an operator stack up work
that cannot start, and pretending otherwise would be a worse answer than a
plainly serial system.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.config import get_settings
from backend.models.db import Job, Run, SessionLocal, utcnow

# Terminal states. A job in one of these is never restarted or re-cancelled.
FINISHED = ("complete", "failed", "cancelled")

_lock = threading.Lock()
# Job ids whose cancel has been requested but not yet observed by the pipeline.
# Held in memory as well as the DB so the cancel check is a set lookup rather
# than a database round trip at every stage boundary.
_cancelling: set = set()


def _settings():
    return get_settings()


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def create(db, run_id: str, investigation_id: Optional[str],
           inputs: Dict[str, Any]) -> Job:
    """Record a job for a run that is about to start."""
    job = Job(id=f"job-{run_id}", run_id=run_id,
              investigation_id=investigation_id, status="pending",
              inputs_json=json.dumps(inputs, default=str))
    db.add(job)
    return job


def request_cancel(db, job_id: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Ask a running job to stop at its next stage boundary."""
    job = db.get(Job, job_id)
    if job is None:
        return {"ok": False, "reason": "no such job"}
    if job.status in FINISHED:
        # Not an error. Cancelling something that already finished is a race
        # the UI cannot avoid, and reporting it as a failure would teach
        # operators to ignore the message.
        return {"ok": False, "status": job.status,
                "reason": f"job already {job.status}"}

    job.status = "cancelling"
    job.cancel_requested_utc = utcnow()
    job.cancelled_by = user_id
    db.commit()
    with _lock:
        _cancelling.add(job.id)
    return {"ok": True, "status": "cancelling", "job_id": job.id,
            "detail": "the pipeline stops at the next stage boundary; "
                      "the run will not be sealed"}


def is_cancelling(job_id: str) -> bool:
    with _lock:
        return job_id in _cancelling


def _clear(job_id: str) -> None:
    with _lock:
        _cancelling.discard(job_id)


# --------------------------------------------------------------------------
# progress, read from the run's own artefacts
# --------------------------------------------------------------------------

def read_progress(run_id: str) -> Dict[str, Any]:
    """Current stage and counts, straight from status.json.

    Returns empty when the file is not there yet -- a run that has not written
    its first status is genuinely at an unknown stage, and saying "detect" by
    default would be a guess presented as a reading.
    """
    path = Path(_settings().runs_root) / run_id / "status.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    stages = payload.get("stages", [])
    running = next((s["stage"] for s in stages if s.get("status") == "running"), None)
    done = sum(1 for s in stages
               if s.get("status") in ("ok", "fallback", "mock", "failed"))
    return {"current_stage": running, "stages_done": done,
            "stages_total": len(stages) or 5, "stages": stages}


def sync_progress(db, job: Job) -> None:
    snapshot = read_progress(job.run_id)
    if not snapshot:
        return
    job.current_stage = snapshot.get("current_stage")
    job.stages_done = snapshot.get("stages_done", 0)
    job.stages_total = snapshot.get("stages_total", 5)


def mark_cancelled(run_id: str) -> None:
    """Rewrite status.json so a cancelled run says so.

    Without this, a cancelled run is only distinguishable from a crashed one by
    the absence of a manifest, and absence is not a statement.
    """
    path = Path(_settings().runs_root) / run_id / "status.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    payload["run_status"] = "cancelled"
    payload["cancelled_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["note"] = ("Cancelled by an operator between stages. This run is "
                       "deliberately NOT sealed: there is no manifest, and it "
                       "must not be quoted as evidence.")
    for stage in payload.get("stages", []):
        if stage.get("status") in (None, "pending", "running"):
            stage["status"] = "cancelled"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def finish(db, job_id: str, status: str, error: Optional[str] = None) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = status
    job.finished_utc = utcnow()
    if error:
        job.error = error[:1000]
    db.commit()
    _clear(job_id)


RESTART_REASON = ("server restarted while this run was in flight; it was left "
                  "unsealed (no manifest) and did not complete")


def sweep_dead_runs(db) -> Dict[str, Any]:
    """At boot, stop dead runs from claiming to be alive.

    A pipeline runs in a thread of this process. If the process dies -- P20
    acceptance produced exactly that, a segmentation fault under concurrent
    runs -- every row that said `running` or `pending` keeps saying so forever,
    and the UI shows work in progress that no longer exists. At startup nothing
    is running by definition, so any such row is dead. It becomes `failed` with
    a reason that says what happened, and its job with it.

    Rows that already have a sealed manifest are left alone and reported: the
    run finished and the process died before the row was updated, and
    `backfill_runs --refresh` is the tool that reconciles that case.
    """
    from backend.models.db import Run

    root = Path(_settings().runs_root)
    swept, sealed_but_unmarked = [], []
    for row in db.query(Run).filter(Run.status.in_(("running", "pending"))).all():
        if (root / row.id / "manifest.json").is_file():
            sealed_but_unmarked.append(row.id)
            continue
        row.status = "failed"
        row.finished_utc = row.finished_utc or utcnow()
        row.error = RESTART_REASON
        job = db.get(Job, f"job-{row.id}")
        if job is not None and job.status not in FINISHED:
            job.status = "failed"
            job.error = RESTART_REASON
            job.finished_utc = job.finished_utc or utcnow()
        swept.append(row.id)
    db.commit()
    return {"swept": swept, "sealed_but_unmarked": sealed_but_unmarked}


def start(db, job_id: str) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.status = "running"
    job.started_utc = utcnow()
    db.commit()


# --------------------------------------------------------------------------
# rerun
# --------------------------------------------------------------------------

def inputs_for_rerun(db, run_id: str) -> Dict[str, Any]:
    """What a run was launched with, preferring the job record.

    The job holds what was ASKED for; the manifest holds what was USED. They
    differ whenever the pipeline resolved or substituted a path, and a rerun
    should repeat the request -- otherwise "rerun" quietly means "run the thing
    the fallback picked last time".
    """
    job = db.query(Job).filter(Job.run_id == run_id).first()
    if job is not None and job.inputs_json:
        try:
            recorded = json.loads(job.inputs_json)
            recorded["source"] = "job record"
            return recorded
        except json.JSONDecodeError:
            pass

    manifest = Path(_settings().runs_root) / run_id / "manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            return {"scene_path": payload.get("scene_path"),
                    "scene_meta_path": str(Path(_settings().runs_root) / run_id
                                           / "scene_meta.json"),
                    "engine": "auto",
                    "source": "manifest (no job record: this run predates jobs)"}
        except json.JSONDecodeError:
            pass

    run = db.get(Run, run_id)
    if run is None:
        return {}
    return {"scene_path": None, "scene_meta_path": None, "engine": "auto",
            "source": "runs table only -- the original inputs were not recorded"}
