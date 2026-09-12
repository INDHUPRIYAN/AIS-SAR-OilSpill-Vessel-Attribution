"""System Logs, and the workers this process actually runs.

HONESTY CONSTRAINT ON THE WORKER MONITOR
----------------------------------------
The spec asks for a Machine / Worker Monitor with host, CPU, RAM, GPU, jobs,
health and last heartbeat. This deployment is **one process**. There is no
worker fleet, no queue broker and no second machine, so:

  * The "workers" reported are the **background threads this process really
    runs** -- the provider health sweep, the AOI scheduler, the live-AIS ingest
    worker -- each with its measured liveness. That is a true answer to "what
    is running".
  * A worker that is **not enabled** is listed as DISABLED with the setting
    that would enable it, rather than omitted. Omitting it would make a
    deliberately-off scheduler indistinguishable from one that crashed.
  * **GPU is reported as not measured**, because nothing here measures one.
    A row reading "GPU 0%" would be a measurement nobody took. Inference in
    this deployment is CPU onnxruntime.
  * There is no `last_heartbeat` field invented for threads that do not emit
    one. What is reported instead is `alive`, which is `Thread.is_alive()` --
    an actual fact about an actual thread.

Scaling to real workers on other hosts means those workers registering
themselves; this endpoint would then report registrations instead of threads.
Until they exist, reporting a fleet of one honestly beats reporting a fleet
that is not there.
"""
from __future__ import annotations

import os
import platform
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.authz import current_user, require_role
from backend.core.config import get_settings
from backend.models.db import Job, Run, User, get_db, utcnow
from backend.services import audit as audit_service
from backend.services import logbuffer

router = APIRouter(tags=["ops"])
settings = get_settings()

# Logs and worker state are operational diagnostics: a reviewer or auditor
# reading them is legitimate, an analyst debugging a failed run is legitimate,
# and none of it names a suspect. Readable by any authenticated role;
# CLEARING the buffer is not.
_ADMIN = require_role("admin")


@router.get("/logs")
def list_logs(level: Optional[str] = Query(
                  None, description="minimum level: INFO|WARN|ERROR|CRITICAL"),
              logger: Optional[str] = None,
              q: Optional[str] = Query(None, description="substring match"),
              run_id: Optional[str] = None,
              job_id: Optional[str] = None,
              incident_id: Optional[str] = None,
              since: Optional[datetime] = None,
              until: Optional[datetime] = None,
              offset: int = Query(0, ge=0),
              limit: int = Query(200, ge=1, le=1000),
              _user: User = Depends(current_user)):
    """Searchable application log.

    `level` is a MINIMUM: asking for WARN returns warnings, errors and
    criticals. An exact-match filter would hide a CRITICAL from an operator
    filtering for "problems".

    This is a live buffer, not an audit trail, and the response says so. The
    durable record of who did what is `/api/audit`, which is hash-chained.
    """
    try:
        return logbuffer.query(
            level=level, logger=logger, contains=q, run_id=run_id,
            job_id=job_id, incident_id=incident_id, since=since, until=until,
            limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/logs/clear", dependencies=[Depends(_ADMIN)])
def clear_logs(request: Request, db: Session = Depends(get_db),
               _user: User = Depends(current_user)):
    """Empty the buffer.

    Audited, because discarding diagnostics during an incident is an action
    somebody may later need to account for -- and because the audit trail is a
    different store, clearing this cannot erase the record of the clearing.
    """
    handler = logbuffer.handler()
    removed = handler.clear() if handler else 0
    audit_service.record(
        db, "data.export", request=request, resource="logs",
        detail=f"cleared the in-memory log buffer ({removed} records)")
    return {"cleared": removed,
            "note": ("the audit trail is a separate, hash-chained store and "
                     "was not affected")}


# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------

def _thread_by_name(fragment: str) -> Optional[threading.Thread]:
    for thread in threading.enumerate():
        if fragment in (thread.name or ""):
            return thread
    return None


def _worker_rows() -> list[dict]:
    """The background workers this process runs, with measured liveness."""
    rows: list[dict] = []

    # --- provider health sweep ---------------------------------------
    health_thread = _thread_by_name("Thread-")     # unnamed in main.py
    rows.append({
        "name": "provider-health-sweep",
        "purpose": "probes every external provider on an interval so the "
                   "monitoring page reports measured history, not a live ping",
        "enabled": bool(settings.health_enabled),
        "interval_seconds": settings.health_interval_seconds,
        # Cannot be identified by name (it is started unnamed), so liveness is
        # reported as unknown rather than guessed from an arbitrary thread.
        "alive": None,
        "alive_note": ("this thread is started without a name in main.py, so "
                       "its liveness cannot be attributed here; the provider "
                       "rows at /api/apis/status carry last_success_utc, "
                       "which is the measured evidence that it ran"),
        "disabled_reason": (None if settings.health_enabled
                            else "HEALTH_ENABLED is false"),
    })

    # --- AOI scheduler ------------------------------------------------
    scheduler = _thread_by_name("aoi")
    rows.append({
        "name": "aoi-scheduler",
        "purpose": "watches registered AOIs for new Sentinel-1 passes and can "
                   "open investigations unattended",
        "enabled": bool(settings.scheduler_enabled),
        "interval_seconds": settings.scheduler_interval_seconds,
        "alive": (scheduler.is_alive() if scheduler is not None
                  else (False if settings.scheduler_enabled else None)),
        # Off by default and listed anyway: a deliberately-disabled scheduler
        # must not look like a crashed one.
        "disabled_reason": (None if settings.scheduler_enabled
                            else "SCHEDULER_ENABLED is false (off by default: "
                                 "it makes outbound provider calls and can "
                                 "start pipeline runs on its own)"),
    })

    # --- live AIS ingest ----------------------------------------------
    ais_thread = _thread_by_name("ais-live")
    try:
        from backend.services import ais_live

        status = ais_live.get_worker().status()
    except Exception as exc:                       # noqa: BLE001
        status = {"state": "unavailable", "last_error": f"{type(exc).__name__}: {exc}"}
    rows.append({
        "name": "ais-live-ingest",
        "purpose": "holds the AISStream subscription, writes live vessel state "
                   "and appends to the day-partitioned AIS archive",
        "enabled": status.get("state") not in ("stopped", "not_configured",
                                               "unavailable"),
        "alive": (ais_thread.is_alive() if ais_thread is not None else False),
        "state": status.get("state"),
        # Connected and working are different, and this is where that matters
        # most: a monitor that showed a green row for a stream ingesting
        # nothing is the failure standing rule 8 is about.
        "functionally_working": status.get("functionally_working"),
        "counters": status.get("counters"),
        "last_message_utc": status.get("last_message_utc"),
        "reconnects": status.get("reconnects"),
        "queue_depth": status.get("queue_depth"),
        "queue_capacity": status.get("queue_capacity"),
        "note": status.get("note"),
        "disabled_reason": (
            "no AISSTREAM_API_KEY" if status.get("state") == "not_configured"
            else "not started" if status.get("state") == "stopped" else None),
    })

    return rows


@router.get("/workers")
def workers(db: Session = Depends(get_db),
            _user: User = Depends(current_user)):
    """What is running in this process, and what work is in flight.

    See the module docstring on why this reports threads rather than a fleet,
    and why GPU is reported as not measured rather than as zero.
    """
    now = utcnow()
    in_flight = (db.query(Job)
                 .filter(Job.status.in_(("pending", "running")))
                 .order_by(Job.created_utc.desc()).all())
    recent = (db.query(Job)
              .order_by(Job.created_utc.desc()).limit(20).all())

    def job_row(job: Job) -> dict:
        started = job.started_utc or job.created_utc
        elapsed = None
        if started is not None:
            start = (started if started.tzinfo
                     else started.replace(tzinfo=timezone.utc))
            end = job.finished_utc or now
            end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
            elapsed = round((end - start).total_seconds(), 1)
        return {
            "job_id": job.id,
            "run_id": job.run_id,
            "investigation_id": job.investigation_id,
            "status": job.status,
            "current_stage": job.current_stage,
            "stages_done": job.stages_done,
            "stages_total": job.stages_total,
            "created_utc": job.created_utc,
            "started_utc": job.started_utc,
            "finished_utc": job.finished_utc,
            "elapsed_seconds": elapsed,
            "cancel_requested": job.cancel_requested_utc is not None,
            "error": job.error,
        }

    # Processing times from jobs that actually finished. Absent rather than
    # zero when none have: a "0.0 s mean" would be a statistic over nothing.
    durations = []
    for job in db.query(Job).filter(Job.finished_utc.isnot(None)).limit(200):
        if job.started_utc is None:
            continue
        start = (job.started_utc if job.started_utc.tzinfo
                 else job.started_utc.replace(tzinfo=timezone.utc))
        end = (job.finished_utc if job.finished_utc.tzinfo
               else job.finished_utc.replace(tzinfo=timezone.utc))
        durations.append((end - start).total_seconds())
    durations.sort()

    def percentile(p: float) -> Optional[float]:
        if not durations:
            return None
        idx = min(len(durations) - 1, max(0, int(round(p * (len(durations) - 1)))))
        return round(durations[idx], 1)

    host: dict[str, Any] = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
        "threads_total": threading.active_count(),
    }
    try:
        import psutil

        process = psutil.Process()
        host["cpu_percent_host"] = psutil.cpu_percent(interval=0.1)
        host["cpu_count"] = psutil.cpu_count()
        host["process_rss_mb"] = round(process.memory_info().rss / 1e6, 1)
        host["process_cpu_percent"] = process.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        host["memory_total_mb"] = round(memory.total / 1e6)
        host["memory_used_percent"] = memory.percent
        host["measured_by"] = "psutil"
    except Exception:                              # noqa: BLE001
        host["measured_by"] = None
        host["note"] = ("psutil is not installed, so CPU and memory were NOT "
                        "measured. These fields are absent rather than zero.")

    return {
        "model": "single-process",
        "model_note": (
            "This deployment runs one process. The rows below are the "
            "background threads it actually runs, not a worker fleet -- there "
            "is no queue broker and no second machine. A worker that is "
            "switched off is listed as disabled with the setting that would "
            "enable it, because a deliberately-off scheduler must not look "
            "like a crashed one."),
        "workers": _worker_rows(),
        "host": host,
        "gpu": {
            "measured": False,
            # Never a zero. See the module docstring.
            "note": ("no GPU is measured by this deployment. Inference runs on "
                     "CPU onnxruntime; training happens off-host and ships "
                     "ONNX. A '0%' reading here would be a measurement nobody "
                     "took."),
        },
        "jobs": {
            "in_flight": [job_row(j) for j in in_flight],
            "in_flight_count": len(in_flight),
            "recent": [job_row(j) for j in recent],
            "finished_sampled": len(durations),
            "duration_seconds": {
                "p50": percentile(0.5),
                "p90": percentile(0.9),
                "max": round(durations[-1], 1) if durations else None,
            } if durations else None,
            "duration_note": (None if durations else
                              "no job has finished on this deployment yet, so "
                              "there are no processing times to report"),
        },
        "logs": {
            "capturing": logbuffer.handler() is not None,
            "buffered": (len(logbuffer.handler().snapshot())
                         if logbuffer.handler() else 0),
            "dropped": (logbuffer.handler().dropped
                        if logbuffer.handler() else 0),
        },
        "as_of_utc": now,
    }
