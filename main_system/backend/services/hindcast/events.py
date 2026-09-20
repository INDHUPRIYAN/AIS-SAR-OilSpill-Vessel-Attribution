"""Progress persistence and fan-out.

`DbEmitter` writes every engine update to its `hindcast_engine_runs` row and
publishes the row to the WebSocket handlers subscribed to that job. The
pipeline runs in a thread of this process, so the bus is an in-process registry
of asyncio queues -- no broker.

DB writes are DELTAS: new log lines are appended, changed metrics merged,
counters added. The drift engine reports from several chunks into one row, and
a whole-row overwrite would lose updates the moment those chunks run in
parallel.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import select

from backend.models.db import utcnow
from backend.models.hindcast import HindcastEngineRun as EngineRun
from backend.models.hindcast import HindcastJob as Job
from backend.models.hindcast import session_scope
from backend.services.hindcast.engines.base import Emitter


def iso_utc(value: Any) -> Optional[str]:
    """SQLite returns naive datetimes; every stored time is UTC, so say so. A
    naive ISO string would be read by a browser as LOCAL time."""
    if value is None:
        return None
    return value.replace(tzinfo=None).isoformat(timespec="seconds") + "Z" if value.tzinfo is None         else value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def run_to_dict(run: EngineRun, log_tail: Optional[int] = 5) -> dict[str, Any]:
    logs = list(run.logs or [])
    return {
        "job_id": run.job_id, "engine_id": run.engine_id, "status": run.status,
        "percent": round(run.percent or 0.0, 1), "current_step": run.current_step,
        "metrics": run.metrics or {}, "error": run.error,
        "started_at": iso_utc(run.started_at), "finished_at": iso_utc(run.finished_at),
        "duration_ms": run.duration_ms,
        "log_count": len(logs), "logs": logs if log_tail is None else logs[-log_tail:],
    }


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

_local_lock = threading.Lock()
_local_subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]]] = {}


def publish(job_id: str, payload: dict[str, Any]) -> None:
    message = json.dumps(payload, default=str)
    with _local_lock:
        subs = list(_local_subs.get(job_id, ()))
    for loop, queue in subs:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        except RuntimeError:      # the subscriber's loop has gone away; its handler unsubscribes
            pass


def subscribe_local(job_id: str) -> tuple[asyncio.Queue[str], Any]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
    entry = (asyncio.get_running_loop(), queue)
    with _local_lock:
        _local_subs.setdefault(job_id, set()).add(entry)

    def unsubscribe() -> None:
        with _local_lock:
            _local_subs.get(job_id, set()).discard(entry)

    return queue, unsubscribe


def publish_job(job: Job) -> None:
    publish(job.id, {"type": "job", "job_id": job.id, "status": job.status, "error": job.error})


# --------------------------------------------------------------------------
# emitter
# --------------------------------------------------------------------------

class DbEmitter(Emitter):
    def __init__(self, job_id: str, engine_id: str) -> None:
        super().__init__()
        self.job_id, self.engine_id = job_id, engine_id
        self._logs_written = 0
        self._dirty_metrics: dict[str, Any] = {}
        self._counter_deltas: dict[str, float] = {}
        self._progress_keys: Optional[tuple[str, str]] = None
        self._lifecycle: Optional[str] = None
        self.owns_percent = True   # False for drift member workers: counters own the percent

    def metric(self, **values: Any) -> None:
        self._dirty_metrics.update(values)
        super().metric(**values)

    def increment(self, deltas: dict[str, float], progress: Optional[tuple[str, str]] = None) -> None:
        for key, delta in deltas.items():
            self._counter_deltas[key] = self._counter_deltas.get(key, 0) + delta
        self._progress_keys = progress
        self._flush(force=True)

    def started(self) -> None:
        self._lifecycle = "started"
        super().started()

    def succeeded(self) -> None:
        self._lifecycle = "succeeded"
        super().succeeded()

    def failed(self, error: str) -> None:
        self._lifecycle = "failed"
        super().failed(error)

    def _write(self) -> None:
        with session_scope() as db:
            run = db.execute(select(EngineRun).where(EngineRun.job_id == self.job_id,
                                                     EngineRun.engine_id == self.engine_id)
                             ).scalar_one()
            now = utcnow()
            if self._lifecycle == "started":
                run.status, run.started_at, run.percent = "running", now, 0.0
                run.finished_at, run.duration_ms, run.error = None, None, None
            elif self._lifecycle in ("succeeded", "failed"):
                run.status, run.finished_at = self._lifecycle, now
                if run.started_at:
                    started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=now.tzinfo)
                    run.duration_ms = int((now - started).total_seconds() * 1000)
                if self._lifecycle == "succeeded":
                    run.percent = 100.0
                else:
                    run.error = self.error
            self._lifecycle = None

            metrics = dict(run.metrics or {})
            metrics.update(self._dirty_metrics)
            for key, delta in self._counter_deltas.items():
                metrics[key] = metrics.get(key, 0) + delta
            if self._progress_keys and metrics.get(self._progress_keys[1]):
                done, total = metrics[self._progress_keys[0]], metrics[self._progress_keys[1]]
                run.percent = max(run.percent or 0.0, 3.0 + 92.0 * done / total)
            elif self.owns_percent and run.status == "running":
                run.percent = max(run.percent or 0.0, self.percent)
            run.metrics = metrics
            self._dirty_metrics, self._counter_deltas, self._progress_keys = {}, {}, None

            if self.current_step is not None:
                run.current_step = self.current_step
            if len(self.logs) > self._logs_written:
                run.logs = list(run.logs or []) + self.logs[self._logs_written:]
                self._logs_written = len(self.logs)
            payload = {"type": "engine", **run_to_dict(run)}
        publish(self.job_id, payload)
