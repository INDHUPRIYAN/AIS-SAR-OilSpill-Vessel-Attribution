"""Recording what happened, in a way that survives being questioned.

Two properties do the work here.

**The actor comes from the session.** `record()` takes a `Request` and resolves
the account itself; it will not accept an actor name from a caller. Before this
the only emit sites passed a free-text string, so the log could say whatever
the request body said -- which is no evidence at all in a system that names
vessels as suspects.

**Rows are chained.** `row_hash = sha256(prev_hash || canonical(row))`, so
altering or removing any row breaks every hash after it. This is tamper
*evident*, not tamper proof: anyone who can write the database file can also
rewrite the chain. What they cannot do is change one row and leave the rest
consistent without noticing. `verify_chain()` reports the first break.

The canonical form deliberately excludes `id` (assigned by the database) and
includes everything else that matters, serialised with sorted keys so the same
row always hashes the same way.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Any, Dict, List, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from backend.models.db import AuditLog, utcnow

# The ten event types the production spec asks for. Kept as a tuple so a typo
# in a call site fails a test rather than quietly creating an eleventh
# category that no filter will ever show.
EVENT_TYPES = (
    "auth.login",
    "auth.logout",
    "user.change",
    "incident.create",
    "incident.status",
    "investigation.create",
    "run.start",
    "run.complete",
    "run.cancel",
    "decision",
    "report.submit",
    "report.approve",
    "key.set",
    "model.change",
    "data.export",
    # --- operational zones -------------------------------------------------
    # A boundary edit is the one action here that changes who *receives* future
    # work, so it is audited separately from the record edit that renames a
    # zone. "zone.geometry" is the row an audit looks for when asking who moved
    # a line; "zone.change" covers the rest.
    "zone.create",
    "zone.geometry",
    "zone.change",
    "zone.delete",
    "zone.assign",
    "zone.unassign",
    # --- identity ----------------------------------------------------------
    # Split from the pre-existing "user.change" because granting a role is a
    # privilege escalation and deactivating an account is a lockout, and an
    # auditor filtering for either should not have to read every profile edit.
    "user.create",
    "role.change",
    "user.deactivate",
    # --- live AIS ----------------------------------------------------------
    "ais.stream.start",
    "ais.stream.stop",
    # --- alert routing -----------------------------------------------------
    "alert.route",
    "alertrule.change",
)

GENESIS = "0" * 64


def _timestamp(value) -> Optional[str]:
    """Timestamp in a form that survives a database round-trip.

    SQLite has no native timestamp type, so `DateTime(timezone=True)` writes a
    string and reads back a NAIVE datetime -- `isoformat()` therefore yields
    `...+00:00` when hashing at write time and `...` without the offset when
    re-hashing at verify time, and every chain "breaks" on the first check.
    Normalising to UTC and formatting explicitly makes both sides agree. A
    naive value is treated as UTC, which is what the writer stored.
    """
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _canonical(row: AuditLog) -> str:
    """Stable serialisation of the fields the chain covers."""
    payload = {
        "action": row.action,
        "provider": row.provider,
        "field": row.field,
        "actor": row.actor,
        "actor_user_id": row.actor_user_id,
        "resource": row.resource,
        "ip": row.ip,
        "detail": row.detail,
        "occurred_utc": _timestamp(row.occurred_utc),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: Optional[str], row: AuditLog) -> str:
    return hashlib.sha256(
        f"{prev_hash or GENESIS}{_canonical(row)}".encode("utf-8")).hexdigest()


def _client_ip(request: Optional[Request]) -> Optional[str]:
    """Best-effort caller address.

    `X-Forwarded-For` is honoured because the API is expected to sit behind a
    proxy, but only its first hop -- and it is worth being clear that a client
    can set that header, so the value is an indication, not an identity. The
    account is the identity.
    """
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def record(db: Session, action: str, *, request: Optional[Request] = None,
           resource: Optional[str] = None, detail: Optional[str] = None,
           provider: Optional[str] = None, field: Optional[str] = None,
           actor: Optional[str] = None, actor_user_id: Optional[int] = None,
           commit: bool = True) -> AuditLog:
    """Append one audited event.

    `actor`/`actor_user_id` are resolved from the session when a `request` is
    given; the explicit arguments exist only for callers with no request at
    all (the pipeline CLI, background jobs), and are ignored when a session is
    present. That ordering is the point: a request can never override who the
    session says it is.
    """
    if request is not None:
        try:
            from backend.core.authz import optional_user

            user = optional_user(request, db)
        except Exception:                          # noqa: BLE001 - never lose the row
            user = None
        if user is not None:
            actor, actor_user_id = user.email, user.id
        elif actor is None:
            # An authenticated route cannot reach here; an unauthenticated one
            # (a failed login) legitimately has no account to name.
            actor = "anonymous"

    row = AuditLog(
        action=action, provider=provider, field=field,
        actor=(actor or "system")[:64], actor_user_id=actor_user_id,
        resource=(resource or None), ip=_client_ip(request),
        detail=detail, occurred_utc=utcnow(),
    )

    # `or GENESIS` matters: the previous row may be a legacy row written before
    # chaining existed, which has no hash. Storing None there would make this
    # row's prev_hash disagree with what verification expects for the first
    # chained row, and the chain would read as broken from birth.
    last = (db.query(AuditLog).order_by(AuditLog.id.desc()).first())
    row.prev_hash = (last.row_hash if last is not None else None) or GENESIS
    row.row_hash = compute_hash(row.prev_hash, row)

    db.add(row)
    if commit:
        db.commit()
    else:
        # MUST flush, and this is not an optimisation.
        #
        # `SessionLocal` is configured `autoflush=False`, so a pending row is
        # invisible to a later query on the same session. Two `record(...,
        # commit=False)` calls inside one transaction therefore both read the
        # same "last" row and both stored `prev_hash = GENESIS` -- and the
        # chain read as BROKEN AT ROW 2 from the moment anything audited two
        # events atomically.
        #
        # Found when automatic incident creation began emitting
        # `incident.create` and `alert.route` together: `/api/audit/verify`
        # went to ok=False, which for a tamper-evidence chain is the worst
        # possible failure -- it makes a working system indistinguishable from
        # an altered one. Flushing makes the row visible to the next query
        # without committing the transaction, so the caller keeps its
        # all-or-nothing write and the chain stays intact.
        db.flush()
    return row


def verify_chain(db: Session) -> Dict[str, Any]:
    """Recompute every hash and report the first row that does not match.

    Rows written before chaining existed carry no `row_hash`. They are counted
    and reported as unchained rather than treated as valid or as failures --
    claiming to have verified them would be the dishonest option.
    """
    rows: List[AuditLog] = db.query(AuditLog).order_by(AuditLog.id.asc()).all()

    unchained = [r.id for r in rows if not r.row_hash]
    chained = [r for r in rows if r.row_hash]

    prev: Optional[str] = None
    for row in chained:
        # The first chained row has no predecessor hash to point at. Rows
        # written before the `or GENESIS` fix stored NULL there, and rows
        # written after store GENESIS; both mean "the chain starts here", so
        # both are accepted -- but only for the first row. Anywhere else a
        # NULL prev_hash is a break, which is the case that matters.
        if prev is None:
            acceptable = (None, GENESIS)
            ok_link = row.prev_hash in acceptable
        else:
            ok_link = row.prev_hash == prev
        if not ok_link:
            return {
                "ok": False, "checked": len(chained), "total": len(rows),
                "unchained": len(unchained),
                "broken_at": row.id,
                "reason": "prev_hash does not match the previous row's hash "
                          "-- a row was removed, reordered or inserted",
            }
        if compute_hash(row.prev_hash, row) != row.row_hash:
            return {
                "ok": False, "checked": len(chained), "total": len(rows),
                "unchained": len(unchained),
                "broken_at": row.id,
                "reason": "row_hash does not match the row's contents "
                          "-- this row was edited after it was written",
            }
        prev = row.row_hash

    return {
        "ok": True, "checked": len(chained), "total": len(rows),
        "unchained": len(unchained),
        "broken_at": None,
        "note": (f"{len(unchained)} legacy row(s) predate hash chaining and are "
                 f"not covered" if unchained else "every row is chained"),
    }
