"""Alerts: the queue that turns a watcher into a notification.

    GET  /api/alerts                  the feed, newest first
    GET  /api/alerts/summary          counts for the top-bar bell
    POST /api/alerts/{id}/ack         acknowledge (audited)
    POST /api/alerts/{id}/assign      assign to a user (audited)
    POST /api/alerts/{id}/dismiss     dismiss WITH a reason (audited)
    GET  /api/events/alerts           SSE feed of new alerts

The watcher already opened investigations by itself. What it could not do was
tell anyone: an investigation appearing in a list is not a notification,
because nobody watches a list.

Two rules that shape the API.

**Dismissal requires a reason.** A queue that can be cleared with one
unexplained click becomes a queue people clear rather than read, and the record
of why nobody acted vanishes with it. `POST /dismiss` without a reason is a 422,
not a default.

**Assignment requires a real user.** Assigning to nobody is the same as leaving
it open, but it *looks* handled, which is worse than untouched.

SLA age is computed, never stored: an age column would be wrong the moment it
was written and would need a job to keep it wrong more slowly.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from sqlalchemy import or_

from backend.core.authz import current_user, require_role
from backend.models.db import Alert, SessionLocal, User, get_db, utcnow
from backend.services import audit as audit_service

router = APIRouter()

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
OPEN_STATES = ("open", "acknowledged", "assigned")


class AssignBody(BaseModel):
    user_id: int = Field(description="who is taking this")


class DismissBody(BaseModel):
    reason: str = Field(
        min_length=3,
        description="why this needs no action. Required: an alert cleared "
                    "without a reason erases the record of why nobody acted.")


def _age_seconds(alert: Alert) -> float:
    created = alert.created_utc
    if created is None:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds()


def _shape(alert: Alert) -> Dict[str, Any]:
    return {
        "id": alert.id, "kind": alert.kind, "severity": alert.severity,
        "status": alert.status, "title": alert.title, "detail": alert.detail,
        "aoi_id": alert.aoi_id, "scene_id": alert.scene_id,
        "run_id": alert.run_id, "investigation_id": alert.investigation_id,
        "incident_id": alert.incident_id,
        "created_utc": alert.created_utc,
        "acknowledged_utc": alert.acknowledged_utc,
        "assigned_to": alert.assigned_to, "assigned_utc": alert.assigned_utc,
        "dismissed_utc": alert.dismissed_utc,
        "dismiss_reason": alert.dismiss_reason,
        # --- zone routing (spec section 18) -----------------------------
        # `routed_to` is what the SYSTEM decided from the coordinates;
        # `assigned_to` above is what a HUMAN then did. They are separate
        # fields because collapsing them would erase the evidence that an
        # alert reached the correct desk before somebody reassigned it.
        "zone_id": alert.zone_id,
        "routed_to": alert.routed_to,
        # zone | escalated | unzoned | unassigned | null (predates routing).
        # `unzoned` and `unassigned` both mean nobody will see this, and they
        # are distinguished because the fixes differ: draw a zone, versus
        # assign an officer to one.
        "routing": alert.routing,
        "routing_detail": alert.routing_detail,
        # Computed on read. A stored age is wrong the moment it is written.
        "age_seconds": round(_age_seconds(alert), 1),
    }


# --------------------------------------------------------------------------
# creation, called by the watcher
# --------------------------------------------------------------------------

def raise_alert(db: Session, *, kind: str, title: str,
                severity: str = "info", detail: Optional[str] = None,
                aoi_id: Optional[str] = None, scene_id: Optional[str] = None,
                run_id: Optional[str] = None,
                investigation_id: Optional[str] = None,
                dedupe: bool = True) -> Optional[Alert]:
    """Record something worth a human's attention.

    Deduplicated on (kind, scene_id) while an earlier alert is still open --
    a watcher that polls every hour must not raise the same scene sixty times,
    because a queue full of duplicates is a queue nobody reads.
    """
    if dedupe and scene_id:
        existing = (db.query(Alert)
                    .filter(Alert.kind == kind, Alert.scene_id == scene_id,
                            Alert.status.in_(OPEN_STATES))
                    .first())
        if existing is not None:
            return None

    alert = Alert(id=f"alert-{secrets.token_hex(5)}", kind=kind, title=title,
                  severity=severity, detail=detail, aoi_id=aoi_id,
                  scene_id=scene_id, run_id=run_id,
                  investigation_id=investigation_id)
    db.add(alert)
    return alert


# --------------------------------------------------------------------------
# feed
# --------------------------------------------------------------------------

@router.get("/alerts")
def list_alerts(db: Session = Depends(get_db),
                user: User = Depends(current_user),
                status: Optional[str] = Query(
                    None, pattern="^(open|acknowledged|assigned|dismissed)$"),
                severity: Optional[str] = Query(
                    None, pattern="^(info|warning|critical)$"),
                zone_id: Optional[str] = None,
                routing: Optional[str] = Query(
                    None, pattern="^(zone|escalated|unzoned|unassigned)$"),
                mine: bool = Query(
                    False,
                    description="only alerts routed to you or to a zone you "
                                "are assigned to"),
                open_only: bool = True,
                limit: int = Query(100, ge=1, le=500)):
    """The alert feed.

    **Unfiltered by default, for every role.** An officer sees the whole
    queue, because an alert two zones away is situational awareness and
    hiding it makes them worse at the job (spec section 20). `mine=true` is
    what narrows it to their own desk -- an opt-in view, not a permission
    boundary. What IS scoped by role is report access, and that lives in
    `services.zones.may_read_zone_reports`.
    """
    query = db.query(Alert)
    if status:
        query = query.filter(Alert.status == status)
    elif open_only:
        query = query.filter(Alert.status.in_(OPEN_STATES))
    if severity:
        query = query.filter(Alert.severity == severity)
    if zone_id:
        query = query.filter(Alert.zone_id == zone_id)
    if routing:
        query = query.filter(Alert.routing == routing)
    if mine:
        from backend.services import zones as zsvc

        scope = zsvc.assigned_zone_ids(db, user)
        # Routed directly to this account, OR to any zone they cover. An
        # officer who was named personally must still see it even if the zone
        # was later reassigned.
        clauses = [Alert.routed_to == user.id, Alert.assigned_to == user.id]
        if scope:
            clauses.append(Alert.zone_id.in_(scope))
        query = query.filter(or_(*clauses))

    rows = query.order_by(Alert.created_utc.desc()).limit(limit).all()
    rows.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 3),
                             -_age_seconds(a)))
    return {"alerts": [_shape(a) for a in rows], "count": len(rows),
            "scoped_to_you": bool(mine), "your_role": user.role}


@router.get("/alerts/summary")
def alerts_summary(db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """Counts for the top-bar bell, and for the officer dashboard."""
    from backend.services import zones as zsvc

    rows = db.query(Alert).filter(Alert.status.in_(OPEN_STATES)).all()
    by_severity: Dict[str, int] = {}
    by_zone: Dict[str, int] = {}
    for row in rows:
        by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
        key = row.zone_id or "(outside all zones)"
        by_zone[key] = by_zone.get(key, 0) + 1

    scope = zsvc.assigned_zone_ids(db, user)
    mine = [r for r in rows
            if r.routed_to == user.id or r.assigned_to == user.id
            or (r.zone_id and r.zone_id in scope)]

    # Counted and surfaced rather than left to be noticed. An alert nobody is
    # responsible for is the failure mode zone routing exists to remove, and a
    # summary that omitted it would make the queue look handled.
    unrouted = [r for r in rows if r.routing in ("unzoned", "unassigned")]

    oldest = max((_age_seconds(r) for r in rows), default=0.0)
    return {"open": len(rows), "by_severity": by_severity,
            "by_zone": by_zone,
            "mine": len(mine),
            "unrouted": len(unrouted),
            "unrouted_reasons": sorted({r.routing for r in unrouted}),
            "oldest_age_seconds": round(oldest, 1),
            "oldest_mine_age_seconds": round(
                max((_age_seconds(r) for r in mine), default=0.0), 1)}


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def _get(db: Session, alert_id: str) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    return alert


@router.post("/alerts/{alert_id}/ack",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def acknowledge(request: Request, alert_id: str, db: Session = Depends(get_db)):
    """Say a human has seen it. Does not close it."""
    alert = _get(db, alert_id)
    if alert.status == "dismissed":
        raise HTTPException(409, "this alert was dismissed; it cannot be acknowledged")

    alert.status = "acknowledged"
    alert.acknowledged_utc = utcnow()
    alert.acknowledged_by = getattr(request.state, "user_id", None)
    audit_service.record(db, "alert.ack", request=request, resource=alert_id,
                         detail=json.dumps({"kind": alert.kind}), commit=False)
    db.commit()
    return _shape(alert)


@router.post("/alerts/{alert_id}/assign",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def assign(request: Request, alert_id: str, body: AssignBody,
           db: Session = Depends(get_db)):
    """Give it to someone. Assigning to nobody looks handled and is not."""
    alert = _get(db, alert_id)
    if alert.status == "dismissed":
        raise HTTPException(409, "this alert was dismissed; it cannot be assigned")

    user = db.get(User, body.user_id)
    if user is None:
        raise HTTPException(400, f"no user {body.user_id}")

    alert.status = "assigned"
    alert.assigned_to = user.id
    alert.assigned_utc = utcnow()
    audit_service.record(db, "alert.assign", request=request, resource=alert_id,
                         detail=json.dumps({"to": user.email}), commit=False)
    db.commit()
    return _shape(alert)


@router.post("/alerts/{alert_id}/dismiss",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def dismiss(request: Request, alert_id: str, body: DismissBody,
            db: Session = Depends(get_db)):
    """Close it, on the record. The reason is required and audited."""
    alert = _get(db, alert_id)
    if alert.status == "dismissed":
        raise HTTPException(409, "already dismissed")

    alert.status = "dismissed"
    alert.dismissed_utc = utcnow()
    alert.dismissed_by = getattr(request.state, "user_id", None)
    alert.dismiss_reason = body.reason
    audit_service.record(db, "alert.dismiss", request=request, resource=alert_id,
                         detail=json.dumps({"reason": body.reason,
                                            "kind": alert.kind}), commit=False)
    db.commit()
    return _shape(alert)


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------

POLL_SECONDS = 2.0
IDLE_TIMEOUT_SECONDS = 900.0


async def _alert_stream(request: Request) -> AsyncIterator[str]:
    seen: set = set()
    waited = 0.0

    # Send the current open queue first, so a browser that connects mid-shift
    # sees what is waiting rather than only what arrives next.
    with SessionLocal() as db:
        for alert in (db.query(Alert).filter(Alert.status.in_(OPEN_STATES))
                      .order_by(Alert.created_utc.desc()).limit(50).all()):
            seen.add(alert.id)
            yield (f"event: alert\ndata: "
                   f"{json.dumps(_shape(alert), default=str)}\n\n")

    while True:
        if await request.is_disconnected():
            return
        with SessionLocal() as db:
            fresh = (db.query(Alert).filter(Alert.status.in_(OPEN_STATES))
                     .order_by(Alert.created_utc.desc()).limit(50).all())
            new = [a for a in fresh if a.id not in seen]
            for alert in new:
                seen.add(alert.id)
                yield (f"event: alert\ndata: "
                       f"{json.dumps(_shape(alert), default=str)}\n\n")
        waited = 0.0 if new else waited + POLL_SECONDS
        if waited >= IDLE_TIMEOUT_SECONDS:
            yield ("event: end\ndata: "
                   + json.dumps({"reason": "idle timeout; the stream closed, "
                                           "the queue did not"}) + "\n\n")
            return
        await asyncio.sleep(POLL_SECONDS)


@router.get("/events/alerts")
async def alert_events(request: Request):
    return StreamingResponse(
        _alert_stream(request), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})
