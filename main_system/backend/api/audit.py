"""Reading the audit log.

Previously the only way in was `GET /api/keys/audit`, which is both the wrong
place (the log is no longer about credentials) and the wrong permission (an
auditor should not need admin rights over the credential vault to read
history). This gives the log its own routes and its own role gate.

There is deliberately no delete and no update. Append-only is the whole claim.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.core.authz import require_role
from backend.models.db import AuditLog, get_db
from backend.services import audit as audit_service

router = APIRouter()

# Reviewers and auditors read the log; admin inherits via require_role. An
# investigator cannot read it, so the record of their own actions is not
# theirs to inspect or dispute quietly.
_can_read = require_role("reviewer", "auditor")


def _filtered(db: Session, actor: Optional[str], action: Optional[str],
              resource: Optional[str], since: Optional[datetime],
              until: Optional[datetime]):
    q = db.query(AuditLog)
    if actor:
        q = q.filter(AuditLog.actor.ilike(f"%{actor}%"))
    if action:
        q = q.filter(AuditLog.action.ilike(f"{action}%"))
    if resource:
        q = q.filter(AuditLog.resource.ilike(f"%{resource}%"))
    if since:
        q = q.filter(AuditLog.occurred_utc >= since)
    if until:
        q = q.filter(AuditLog.occurred_utc <= until)
    return q


def _as_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "occurred_utc": row.occurred_utc,
        "action": row.action,
        "actor": row.actor,
        "actor_user_id": row.actor_user_id,
        "resource": row.resource,
        "ip": row.ip,
        "provider": row.provider,
        "field": row.field,
        "detail": row.detail,
        "row_hash": row.row_hash,
    }


@router.get("/audit", dependencies=[Depends(_can_read)])
def list_audit(db: Session = Depends(get_db),
               actor: Optional[str] = None,
               action: Optional[str] = None,
               resource: Optional[str] = None,
               since: Optional[datetime] = None,
               until: Optional[datetime] = None,
               offset: int = Query(0, ge=0),
               limit: int = Query(100, ge=1, le=1000)):
    """Filtered, paginated history. Newest first."""
    q = _filtered(db, actor, action, resource, since, until)
    total = q.count()
    rows = (q.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all())
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [_as_dict(r) for r in rows],
        "event_types": list(audit_service.EVENT_TYPES),
    }


@router.get("/audit/verify", dependencies=[Depends(_can_read)])
def verify_audit(db: Session = Depends(get_db)):
    """Recompute the hash chain and report the first break, if any.

    Mirrors the artefact `/verify` route the repo already uses for run files,
    so "prove this has not been altered" means the same thing for evidence and
    for the record of who touched it.
    """
    return audit_service.verify_chain(db)


@router.get("/audit/export", dependencies=[Depends(_can_read)])
def export_audit(db: Session = Depends(get_db),
                 actor: Optional[str] = None,
                 action: Optional[str] = None,
                 resource: Optional[str] = None,
                 since: Optional[datetime] = None,
                 until: Optional[datetime] = None):
    """CSV of the filtered history, including each row's hash.

    Exporting is itself an audited event -- taking a copy of the record of who
    did what is exactly the kind of action the record should contain.
    """
    rows = _filtered(db, actor, action, resource, since, until) \
        .order_by(AuditLog.id.asc()).all()

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_as_dict(AuditLog()).keys()),
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(_as_dict(row))
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
