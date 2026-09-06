"""STAGE 9 -- the human decision endpoints (design doc v2 §4 Stage 9, §14).

Standing Rule 8: "The system ranks candidates; a human decides." The pipeline
half of that was already built. These tests cover the other half -- that what
the human decided is actually recorded, with enough context to reconstruct the
screen they decided from.

The API is exercised through a temporary DATA_ROOT so the test never touches
the real runs directory or the real sqlite file.
"""

import json
import os
import sys

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Settings are built at module import, so DATA_ROOT has to be redirected
    # and any already-imported `backend` module dropped, before the app is
    # constructed -- otherwise the test writes into the real data/ directory.
    root = tmp_path_factory.mktemp("data_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["ADMIN_TOKEN"] = "test-token"
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.api.routes import router
    from backend.models.db import init_db
    from backend.services.pipeline import provenance

    init_db()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api")

    # A completed run on disk, sealed with artefact hashes, plus its DB row.
    run_dir = root / "runs" / "inv-t1"
    run_dir.mkdir(parents=True)
    (run_dir / "suspects.json").write_text(json.dumps({
        "scene_id": "S1", "run_id": "inv-t1",
        "generated_utc": "2026-02-01T00:00:00Z",
        "weights": {"proximity": 0.3, "temporal": 0.2, "trajectory": 0.2,
                    "behaviour": 0.15, "ais_gap": 0.1, "vessel_prior": 0.05},
        "suspects": [{"rank": 1, "mmsi": 219000001, "total_score": 0.87}],
        "total_vessels_considered": 12,
    }), encoding="utf-8")
    manifest = {"run_id": "inv-t1", "stages": []}
    provenance.seal(run_dir, manifest)
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    from backend.models.db import Investigation, Run, SessionLocal

    with SessionLocal() as db:
        db.add(Investigation(id="inv-t", name="test"))
        db.add(Run(id="inv-t1", investigation_id="inv-t", status="complete"))
        db.commit()

    yield TestClient(app), run_dir, manifest


# --------------------------------------------------------------------------
# verification endpoint
# --------------------------------------------------------------------------


def test_verify_endpoint_reports_a_clean_run(client):
    c, _, _ = client
    body = c.get("/api/runs/inv-t1/verify").json()
    assert body["ok"] and body["checked"] == 1


def test_verify_endpoint_reports_tampering_as_200_not_500(client):
    """The UI has to render 'this run changed'; an exception would hide it."""
    c, run_dir, _ = client
    original = (run_dir / "suspects.json").read_text(encoding="utf-8")
    (run_dir / "suspects.json").write_text(original.replace("0.87", "0.12"),
                                           encoding="utf-8")
    try:
        resp = c.get("/api/runs/inv-t1/verify")
        assert resp.status_code == 200
        assert not resp.json()["ok"]
    finally:
        (run_dir / "suspects.json").write_text(original, encoding="utf-8")


def test_verify_rejects_a_traversing_run_id(client):
    c, _, _ = client
    assert c.get("/api/runs/..%2F..%2Fetc/verify").status_code in (400, 404)


# --------------------------------------------------------------------------
# recording a decision
# --------------------------------------------------------------------------


def test_record_a_verdict_on_a_ranked_suspect(client):
    c, _, manifest = client
    body = c.post("/api/runs/inv-t1/decisions", json={
        "verdict": "accepted", "mmsi": 219000001,
        "note": "Course change and 40-minute gap line up with the origin window.",
        "actor": "duty-officer-2"}).json()

    assert body["verdict"] == "accepted"
    assert body["suspect_rank"] == 1
    assert body["total_score_at_decision"] == pytest.approx(0.87)
    assert body["actor"] == "duty-officer-2"
    # The decision is pinned to the exact artefacts it was made from, so a
    # later re-run cannot silently change the evidence behind it.
    assert body["artefact_digest"] == manifest["artefact_digest"]
    assert body["weights_used"]["proximity"] == pytest.approx(0.3)


def test_record_a_verdict_on_the_run_itself(client):
    """mmsi is null when the analyst is judging the run, not a candidate."""
    c, _, _ = client
    body = c.post("/api/runs/inv-t1/decisions", json={
        "verdict": "inconclusive", "note": "Slick is real but no traffic in window."
    }).json()
    assert body["mmsi"] is None and body["suspect_rank"] is None


def test_a_vessel_the_ranking_missed_is_still_recorded(client):
    """Feedback about a vessel the system did NOT rank is the most valuable kind."""
    c, _, _ = client
    resp = c.post("/api/runs/inv-t1/decisions", json={
        "verdict": "rejected", "mmsi": 219999999,
        "note": "Analyst flagged a vessel absent from the ranking."})
    assert resp.status_code == 201
    assert resp.json()["suspect_rank"] is None


def test_guilty_is_not_a_verdict(client):
    """§2: the system does not accuse. The vocabulary enforces it."""
    c, _, _ = client
    assert c.post("/api/runs/inv-t1/decisions",
                  json={"verdict": "guilty", "mmsi": 219000001}
                  ).status_code == 422


def test_decision_on_an_unknown_run_is_404(client):
    c, _, _ = client
    assert c.post("/api/runs/nope/decisions",
                  json={"verdict": "accepted"}).status_code == 404


# --------------------------------------------------------------------------
# reading decisions back
# --------------------------------------------------------------------------


def test_decisions_are_append_only_and_ordered(client):
    """A changed mind is a new row: the sequence is part of the audit trail."""
    c, _, _ = client
    rows = c.get("/api/runs/inv-t1/decisions").json()
    assert len(rows) >= 3
    assert [r["verdict"] for r in rows][:1] == ["accepted"]
    assert rows == sorted(rows, key=lambda r: r["decided_utc"])


def test_decision_writes_an_audit_log_entry(client):
    """§14 wants one answer to 'who ran what, and what they concluded'."""
    c, _, _ = client
    rows = c.get("/api/keys/audit", headers={"X-Admin-Token": "test-token"}).json()
    actions = {r.get("action") for r in rows}
    assert any(a and a.startswith("decision.") for a in actions)


def test_feedback_corpus_endpoint(client):
    c, _, _ = client
    rows = c.get("/api/decisions").json()
    assert len(rows) >= 3
    assert rows == sorted(rows, key=lambda r: r["decided_utc"], reverse=True)
