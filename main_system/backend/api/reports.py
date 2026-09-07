"""Report lifecycle: compose, review, publish, export.

    POST   /api/reports                       compose v1 for a run
    GET    /api/reports                       list
    GET    /api/reports/{id}                  one report, body included
    POST   /api/reports/{id}/submit           draft -> in_review
    POST   /api/reports/{id}/publish          in_review -> published (reviewer+)
    POST   /api/reports/{id}/revise           published -> a NEW draft, version+1
    GET    /api/reports/{id}/export.json
    GET    /api/reports/{id}/export.csv

The state machine is small and its one hard rule is that **published is
immutable**. There is no edit endpoint for a published report and no force
flag: revising one composes the next version as a draft, leaving the published
version exactly as approved. A document that can change after sign-off is not a
signed-off document, and an investigation report is precisely the artefact
where that distinction matters.

Publishing is reviewer-gated and audited. The reviewer roles are separate from
the author roles deliberately -- an analyst composing their own conclusions and
approving them is not review.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.authz import require_role
from backend.core.config import get_settings
from backend.models.db import Decision, Report, Run, get_db, utcnow
from backend.services import audit as audit_service
from backend.services import report_compose

router = APIRouter()

# Who may move a report from in_review to published. Deliberately excludes the
# analyst role: composing and approving your own conclusions is not review.
REVIEWER_ROLES = ("admin", "reviewer", "investigator")


class ReportCreate(BaseModel):
    run_id: str = Field(min_length=1)
    title: Optional[str] = None


class ReviewNote(BaseModel):
    note: Optional[str] = None


def _run_dir(run_id: str):
    return get_settings().runs_root / run_id


def _decisions_for(db: Session, run_id: str) -> List[Dict[str, Any]]:
    rows = db.query(Decision).filter(Decision.run_id == run_id).all()
    return [{"verdict": r.verdict, "rationale": r.rationale,
             "created_utc": r.created_utc,
             "mmsi": getattr(r, "mmsi", None)} for r in rows]


def _shape(report: Report, include_body: bool = True) -> Dict[str, Any]:
    payload = {
        "id": report.id, "run_id": report.run_id,
        "investigation_id": report.investigation_id,
        "version": report.version, "status": report.status,
        "title": report.title, "artefact_digest": report.artefact_digest,
        "created_utc": report.created_utc, "updated_utc": report.updated_utc,
        "submitted_utc": report.submitted_utc,
        "published_utc": report.published_utc,
        "review_note": report.review_note,
        "immutable": report.status == "published",
    }
    if include_body:
        payload["body"] = json.loads(report.body_json)
    return payload


def _compose_for(db: Session, run_id: str) -> Dict[str, Any]:
    try:
        return report_compose.compose(_run_dir(run_id), _decisions_for(db, run_id))
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))


# --------------------------------------------------------------------------
# compose and read
# --------------------------------------------------------------------------

@router.post("/reports", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
def create_report(request: Request, body: ReportCreate,
                  db: Session = Depends(get_db)):
    """Compose version 1 of a report for a sealed run."""
    run = db.get(Run, body.run_id)
    if run is None:
        raise HTTPException(404, f"no run {body.run_id}")

    existing = (db.query(Report).filter(Report.run_id == body.run_id)
                .order_by(Report.version.desc()).first())
    if existing is not None:
        raise HTTPException(
            409, f"report {existing.id} already exists for this run at version "
                 f"{existing.version} ({existing.status}). Use /revise to make "
                 "the next version.")

    composed = _compose_for(db, body.run_id)
    report = Report(
        id=f"rep-{secrets.token_hex(5)}", run_id=body.run_id,
        investigation_id=run.investigation_id, version=1, status="draft",
        body_json=json.dumps(composed, default=str),
        artefact_digest=composed.get("artefact_digest"),
        title=body.title or f"Investigation report — {composed.get('scene_id')}")
    db.add(report)
    audit_service.record(db, "report.create", request=request, resource=report.id,
                         detail=json.dumps({"run": body.run_id, "version": 1}),
                         commit=False)
    db.commit()
    return _shape(report)


@router.get("/reports")
def list_reports(db: Session = Depends(get_db),
                 run: Optional[str] = None,
                 status: Optional[str] = Query(None,
                                               pattern="^(draft|in_review|published)$")):
    query = db.query(Report)
    if run:
        query = query.filter(Report.run_id == run)
    if status:
        query = query.filter(Report.status == status)
    rows = query.order_by(Report.created_utc.desc()).all()
    return [_shape(r, include_body=False) for r in rows]


@router.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    payload = _shape(report)
    # A report is composed from a specific set of bytes. If the run has been
    # re-sealed since, say so rather than letting a stale document look current.
    run_manifest = _run_dir(report.run_id) / "manifest.json"
    if run_manifest.exists():
        try:
            current = json.loads(run_manifest.read_text(encoding="utf-8"))
            payload["digest_matches_run"] = (
                current.get("artefact_digest") == report.artefact_digest)
        except (json.JSONDecodeError, OSError):
            payload["digest_matches_run"] = None
    else:
        payload["digest_matches_run"] = None
    return payload


# --------------------------------------------------------------------------
# review state machine
# --------------------------------------------------------------------------

@router.post("/reports/{report_id}/submit",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def submit_report(request: Request, report_id: str, body: ReviewNote,
                  db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    if report.status != "draft":
        raise HTTPException(409, f"only a draft can be submitted; this is {report.status}")

    report.status = "in_review"
    report.submitted_utc = utcnow()
    if body.note:
        report.review_note = body.note
    audit_service.record(db, "report.submit", request=request, resource=report_id,
                         detail=json.dumps({"version": report.version}),
                         commit=False)
    db.commit()
    return _shape(report, include_body=False)


@router.post("/reports/{report_id}/publish",
             dependencies=[Depends(require_role(*REVIEWER_ROLES))])
def publish_report(request: Request, report_id: str, body: ReviewNote,
                   db: Session = Depends(get_db)):
    """Approve a report. After this it cannot be edited, only superseded."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    if report.status == "published":
        raise HTTPException(409, "already published; published reports are immutable")
    if report.status != "in_review":
        raise HTTPException(
            409, f"only a report in review can be published; this is {report.status}")

    report.status = "published"
    report.published_utc = utcnow()
    report.reviewed_by = getattr(request.state, "user_id", None)
    if body.note:
        report.review_note = body.note
    audit_service.record(db, "report.publish", request=request, resource=report_id,
                         detail=json.dumps({"version": report.version,
                                            "digest": report.artefact_digest}),
                         commit=False)
    db.commit()
    return _shape(report, include_body=False)


@router.post("/reports/{report_id}/revise", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
def revise_report(request: Request, report_id: str, db: Session = Depends(get_db)):
    """Compose the next version as a draft, leaving the published one intact."""
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")

    latest = (db.query(Report).filter(Report.run_id == report.run_id)
              .order_by(Report.version.desc()).first())
    if latest.status != "published":
        raise HTTPException(
            409, f"version {latest.version} is still {latest.status}; finish or "
                 "publish it before starting the next version")

    composed = _compose_for(db, report.run_id)
    fresh = Report(
        id=f"rep-{secrets.token_hex(5)}", run_id=report.run_id,
        investigation_id=report.investigation_id, version=latest.version + 1,
        status="draft", body_json=json.dumps(composed, default=str),
        artefact_digest=composed.get("artefact_digest"), title=report.title)
    db.add(fresh)
    audit_service.record(db, "report.revise", request=request, resource=fresh.id,
                         detail=json.dumps({"supersedes": latest.id,
                                            "version": fresh.version}),
                         commit=False)
    db.commit()
    return _shape(fresh)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

@router.get("/reports/{report_id}/export.json")
def export_json(report_id: str, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    return _shape(report)


@router.get("/reports/{report_id}/export.csv")
def export_csv(report_id: str, db: Session = Depends(get_db)):
    """Suspects and their evidence, one row per ranked vessel.

    CSV because this is the format an analyst pastes into a case file. The
    column set is fixed and golden-file tested: a silently reordered column is
    how a spreadsheet ends up attributing one vessel's evidence to another.
    """
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "report not found")

    rows = report_compose.flatten_suspects_csv(json.loads(report.body_json))
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return Response(
        buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{report.run_id}-suspects.csv"'})
