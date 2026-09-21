"""Tell someone when a run ends.

A run executes on a worker thread and may take minutes; the analyst who
started it has usually left the page. Until now nothing told them it had
finished: the alert queue knew about new scenes and detections, and the
model's own comment listed `run_failed` as a kind that was never raised.

Two kinds, and a deliberately short list of when each fires:

``run_failed``    every run that fails, whoever or whatever started it. A
                  scheduled run dying at 03:00 is exactly what nobody is
                  watching for.
``run_complete``  a run a PERSON started (start / re-run), when it seals.
                  Not for scheduler-started runs: the watcher already raises
                  ``new_scene`` for those, and a completion row per polled
                  scene would bury the queue. Not when the run has just opened
                  an incident either -- that raised a ``detection`` alert about
                  the same run, and two rows for one event is noise.

A cancelled run raises nothing: the operator did that and knows.

Severity is derived, as everywhere else in the alert model: a failure or a
run that sealed with failed stages is a warning, a clean completion is info.

Raising an alert must never fail the run. Callers wrap this in try/except;
the run is the evidence and the alert is only the doorbell.
"""

from __future__ import annotations

import secrets
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.db import Alert, Run

RUN_COMPLETE = "run_complete"
RUN_FAILED = "run_failed"


def _existing(db: Session, run_id: str, kind: str) -> Optional[Alert]:
    return db.query(Alert).filter(Alert.run_id == run_id, Alert.kind == kind).first()


def _add(db: Session, run: Run, *, kind: str, severity: str, title: str, detail: str) -> Alert:
    alert = Alert(
        id=f"alert-{secrets.token_hex(5)}", kind=kind, severity=severity, status="open",
        title=title[:300], detail=detail, scene_id=run.scene_id, run_id=run.id,
        investigation_id=run.investigation_id,
    )
    db.add(alert)
    db.flush()
    return alert


def alert_run_outcome(db: Session, run_id: str, *, started_by_person: bool = True,
                      opened_incident: bool = False) -> Optional[Alert]:
    """Raise the alert a finished run deserves, or None. Idempotent per run and kind."""
    run = db.get(Run, run_id)
    if run is None:
        return None

    if run.status == "failed":
        if _existing(db, run_id, RUN_FAILED):
            return None
        return _add(
            db, run, kind=RUN_FAILED, severity="warning",
            title=f"Run failed · {run_id}",
            detail="\n".join(filter(None, [
                f"Scene: {run.scene_id or 'not recorded'}",
                f"Investigation: {run.investigation_id or 'unfiled run'}",
                f"Error: {run.error or 'no error text was recorded'}",
            ])),
        )

    if run.status != "complete" or not started_by_person or opened_incident:
        return None
    if _existing(db, run_id, RUN_COMPLETE):
        return None

    failed = int(run.stages_failed or 0)
    total = int(run.stages_total or 0)
    real = int(run.stages_real or 0)
    return _add(
        db, run, kind=RUN_COMPLETE,
        severity="warning" if failed else "info",
        title=(f"Run finished with {failed} failed stage(s) · {run_id}" if failed
               else f"Run complete · {run_id}"),
        detail="\n".join(filter(None, [
            f"Scene: {run.scene_id or 'not recorded'}",
            f"Investigation: {run.investigation_id or 'unfiled run'}",
            f"Stages: {real} of {total} produced output" + (f", {failed} failed" if failed else ""),
            f"Duration: {run.seconds:.1f} s" if run.seconds is not None else None,
        ])),
    )
