"""Server-sent events, so the UI stops polling a file it can be told about.

The run page used to poll `status.json` on a timer. That is a fine fallback and
a poor default: at a 2 s interval a stage that takes 4 s looks like it finished
instantly, and a stage that takes 150 s costs 75 pointless requests. Neither is
wrong, exactly -- they are just a worse account of what happened than the file
itself already contains.

This tails the same `status.json` the pipeline writes and emits one event per
observed change. Deliberate properties:

* **the file is still the source of truth.** Nothing here holds pipeline state
  in memory, so a browser that connects halfway through a run gets the current
  status immediately rather than only future transitions;
* **it ends by itself.** The stream closes when the run reaches a terminal
  state, so a forgotten tab does not hold a worker open indefinitely;
* **polling still works.** `GET /api/investigations/{id}/status` is unchanged.
  If SSE is blocked by a proxy the UI degrades to the old path rather than
  showing nothing, which is why this endpoint adds a capability instead of
  replacing one.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from backend.core.authz import authenticated
from backend.core.config import get_settings
from backend.models.db import Job, Run, get_db

router = APIRouter(dependencies=[Depends(authenticated)])

# How often the tail re-reads the file. Fast enough that a 4 s stage is not
# missed, slow enough that it is not a busy loop.
POLL_SECONDS = 0.5
# A run that produces no change for this long ends the stream. The pipeline's
# longest stage is detection at ~150 s on a full Sentinel-1 frame, so this is
# generous by design: cutting a live run off would be worse than a stale socket.
IDLE_TIMEOUT_SECONDS = 900.0

TERMINAL = ("complete", "failed", "cancelled")


def _status_path(run_id: str) -> Path:
    return Path(get_settings().runs_root) / run_id / "status.json"


def _read(run_id: str) -> Optional[Dict[str, Any]]:
    path = _status_path(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A partial read of a file being replaced is expected, not an error.
        # The next tick sees the finished write.
        return None


def _fingerprint(payload: Dict[str, Any]) -> str:
    """What counts as a change worth sending."""
    stages = payload.get("stages", [])
    return json.dumps([[s.get("stage"), s.get("status"), s.get("engine_used")]
                       for s in stages] + [payload.get("run_status")])


def _event(name: str, data: Dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


async def _stream(request: Request, run_id: str,
                  terminal_status: Optional[str]) -> AsyncIterator[str]:
    last = None
    waited = 0.0

    payload = _read(run_id)
    if payload is None:
        # Say so rather than sending nothing: "the run has not written a status
        # yet" and "the connection is broken" look identical to a client that
        # receives silence.
        yield _event("waiting", {"run_id": run_id,
                                 "detail": "no status.json yet; the run has "
                                           "not reached its first stage"})

    while True:
        if await request.is_disconnected():
            return

        payload = _read(run_id)
        if payload is not None:
            current = _fingerprint(payload)
            if current != last:
                last = current
                waited = 0.0
                for stage in payload.get("stages", []):
                    yield _event("stage", {"run_id": run_id, **stage})
                yield _event("status", {
                    "run_id": run_id,
                    "run_status": payload.get("run_status"),
                    "stages_done": sum(
                        1 for s in payload.get("stages", [])
                        if s.get("status") in ("ok", "fallback", "mock",
                                               "failed", "cancelled")),
                    "stages_total": len(payload.get("stages", [])),
                })
            if payload.get("run_status") in TERMINAL:
                yield _event("end", {"run_id": run_id,
                                     "run_status": payload.get("run_status")})
                return

        if terminal_status is not None and (payload or {}).get("run_status") is None:
            # The run finished before this connection opened and status.json
            # carries no run_status (older runs). Emit what the DB knows and
            # close, instead of tailing a file that will never change again.
            yield _event("end", {"run_id": run_id, "run_status": terminal_status})
            return

        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        if waited >= IDLE_TIMEOUT_SECONDS:
            yield _event("end", {"run_id": run_id, "run_status": "idle_timeout",
                                 "detail": f"no change for {IDLE_TIMEOUT_SECONDS:.0f}s; "
                                           "the stream closed, the run did not"})
            return


@router.get("/events/runs/{run_id}")
async def run_events(request: Request, run_id: str,
                     db: Session = Depends(get_db)):
    """Stream this run's stage transitions as they are written."""
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")

    terminal = run.status if run.status in TERMINAL else None
    return StreamingResponse(
        _stream(request, run_id, terminal),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this, nginx buffers the whole stream and delivers it at
            # the end -- which looks exactly like SSE not working.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/events/jobs/{job_id}")
async def job_events(request: Request, job_id: str,
                     db: Session = Depends(get_db)):
    """The same stream, addressed by job instead of run."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    terminal = job.status if job.status in TERMINAL else None
    return StreamingResponse(
        _stream(request, job.run_id, terminal),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )
