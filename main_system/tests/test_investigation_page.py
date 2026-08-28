"""End-to-end contract tests for the Investigation page backend.

Every test runs against contracts/mocks/ with no network access: a fixture
"run" is assembled from the mock contract files, registered in the DB, and
exercised through the same endpoints the page uses. The page is considered
done only when this suite (plus the Playwright UI suite) passes.

Checklist coverage (from the build spec):
  2   spill numbers come verbatim from slick.geojson
  3   suspects order and scores come verbatim from suspects.json
  5   missing forecast -> layer 404 "not yet produced", others fine
  6   corrupt origin_cloud -> 422 with validation summary, others fine
  7   engine_used=fallback surfaces in /status
  8   synthetic source flags surface for every stage
  10  replay resolves in well under five seconds
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import get_settings
from backend.main import app
from backend.models.db import Investigation, Run, SessionLocal, init_db, utcnow

settings = get_settings()
REPO = settings.data_root.parent
MOCKS = REPO / "contracts" / "mocks"

MOCK_FILES = ["scene_meta.json", "detect_response.json", "slick.geojson",
              "origin_cloud.geojson", "forecast.geojson", "suspects.json",
              "vessels.parquet", "raw_mask.tif"]

STAGES = ["detect", "characterise", "drift_hindcast", "drift_forecast",
          "attribution"]


def make_status(sources=None, engines=None):
    sources = sources or {}
    engines = engines or {}
    return {
        "run_id": "x", "scene_id": "x", "state": "complete",
        "stages": [{
            "stage": s, "status": "ok", "ok": True,
            "engine_used": engines.get(s, "primary"),
            "source": sources.get(s, "real"),
            "detail": "", "warnings": [], "error_class": None,
            "output": "x", "seconds": 0.1,
        } for s in STAGES],
    }


@pytest.fixture()
def client():
    init_db()
    return TestClient(app)


@pytest.fixture()
def fixture_run():
    """A complete run assembled from the mock contract files + DB rows."""
    rid = f"test-invpage-{uuid.uuid4().hex[:8]}"
    iid = f"inv-testpage-{uuid.uuid4().hex[:8]}"
    d = settings.runs_root / rid
    d.mkdir(parents=True, exist_ok=True)
    for f in MOCK_FILES:
        src = MOCKS / f
        if src.exists():
            shutil.copy(src, d / f)
    (d / "status.json").write_text(json.dumps(make_status()), encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(
        {"run_id": rid, "scene_id": "S1A_TESTPAGE", "stages": []}),
        encoding="utf-8")

    with SessionLocal() as db:
        db.add(Investigation(id=iid, name="fixture", scene_id="S1A_TESTPAGE"))
        db.add(Run(id=rid, investigation_id=iid, scene_id="S1A_TESTPAGE",
                   status="complete", started_utc=utcnow()))
        db.commit()

    yield iid, rid, d

    shutil.rmtree(d, ignore_errors=True)
    with SessionLocal() as db:
        r = db.get(Run, rid)
        i = db.get(Investigation, iid)
        if r:
            db.delete(r)
        if i:
            db.delete(i)
        db.commit()


# ----------------------------------------------------------------- tests --


def test_status_shape_and_layer_presence(client, fixture_run):
    iid, rid, d = fixture_run
    r = client.get(f"/api/investigations/{iid}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == rid
    assert body["state"] == "complete"
    assert [s["stage"] for s in body["stages"]] == STAGES
    assert all(s["ok"] for s in body["stages"])
    lp = body["layers_present"]
    assert lp["slick"] and lp["forecast"] and lp["suspects"] and lp["vessels"]


def test_spill_numbers_verbatim(client, fixture_run):
    """Checklist 2: the layer endpoint returns slick.geojson byte-for-value."""
    iid, _, _ = fixture_run
    served = client.get(f"/api/investigations/{iid}/layers/slick").json()
    on_disk = json.loads((MOCKS / "slick.geojson").read_text(encoding="utf-8"))
    assert served == on_disk
    p = served["features"][0]["properties"]
    for key in ("area_km2", "perimeter_km", "centroid", "major_axis_m",
                "minor_axis_m", "orientation_deg", "confidence"):
        assert key in p, f"spill panel field {key} missing from contract"


def test_suspects_order_and_scores_verbatim(client, fixture_run):
    """Checklist 3: rank order and scores exactly as in suspects.json."""
    iid, _, _ = fixture_run
    served = client.get(f"/api/investigations/{iid}/suspects").json()
    on_disk = json.loads((MOCKS / "suspects.json").read_text(encoding="utf-8"))
    got = [(s["rank"], s["mmsi"], s["total_score"]) for s in served["suspects"]]
    want = [(s["rank"], s["mmsi"], s["total_score"]) for s in on_disk["suspects"]]
    assert got == want
    assert got == sorted(got, key=lambda x: x[0])
    assert served["suspects"][0]["reason"], "rank 1 must carry its reason text"
    assert served["suspects"][0]["sub_scores"], "factor breakdown required"


def test_missing_forecast_disables_only_that_layer(client, fixture_run):
    """Checklist 5."""
    iid, rid, d = fixture_run
    (d / "forecast.geojson").unlink()
    r = client.get(f"/api/investigations/{iid}/layers/forecast")
    assert r.status_code == 404
    assert "not yet produced" in r.json()["detail"]
    assert client.get(f"/api/investigations/{iid}/layers/slick").status_code == 200
    assert client.get(f"/api/investigations/{iid}/suspects").status_code == 200
    lp = client.get(f"/api/investigations/{iid}/status").json()["layers_present"]
    assert lp["forecast"] is False and lp["slick"] is True


def test_corrupt_origin_cloud_survives(client, fixture_run):
    """Checklist 6: 422 with a validation summary; everything else intact."""
    iid, rid, d = fixture_run
    (d / "origin_cloud.geojson").write_text('{"type": "FeatureCollection", '
                                            '"features": "NOT A LIST"}',
                                            encoding="utf-8")
    r = client.get(f"/api/investigations/{iid}/layers/origin_cloud")
    assert r.status_code == 422
    assert "validation" in r.json()["detail"].lower() or \
           "malformed" in r.json()["detail"].lower()
    assert client.get(f"/api/investigations/{iid}/layers/slick").status_code == 200
    assert client.get(f"/api/investigations/{iid}/suspects").status_code == 200


def test_truly_malformed_json_survives(client, fixture_run):
    iid, rid, d = fixture_run
    (d / "origin_cloud.geojson").write_text("{ this is not json",
                                            encoding="utf-8")
    r = client.get(f"/api/investigations/{iid}/layers/origin_cloud")
    assert r.status_code == 422
    assert "malformed" in r.json()["detail"]


def test_fallback_badge_flag(client, fixture_run):
    """Checklist 7: engine_used=fallback is surfaced, never hidden."""
    iid, rid, d = fixture_run
    (d / "status.json").write_text(json.dumps(
        make_status(engines={"detect": "fallback"})), encoding="utf-8")
    stages = client.get(f"/api/investigations/{iid}/status").json()["stages"]
    detect = next(s for s in stages if s["stage"] == "detect")
    assert detect["engine_used"] == "fallback"


def test_synthetic_source_flags(client, fixture_run):
    """Checklist 8: every stage flagged synthetic -> badges read SYNTHETIC."""
    iid, rid, d = fixture_run
    (d / "status.json").write_text(json.dumps(
        make_status(sources={s: "synthetic" for s in STAGES})),
        encoding="utf-8")
    stages = client.get(f"/api/investigations/{iid}/status").json()["stages"]
    assert all(s["source"] == "synthetic" for s in stages)


def test_replay_is_fast(client, fixture_run):
    """Checklist 10 (backend half): replay resolves instantly."""
    iid, rid, _ = fixture_run
    t0 = time.time()
    r = client.post(f"/api/investigations/{iid}/replay")
    dt = time.time() - t0
    assert r.status_code == 200
    assert r.json() == {"run_id": rid, "replay": True, "status": "complete"}
    assert dt < 1.0, f"replay took {dt:.2f}s"


def test_replay_without_runs_is_409(client):
    iid = f"inv-empty-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(Investigation(id=iid, name="empty", scene_id="S1A_NOPE"))
        db.commit()
    try:
        r = client.post(f"/api/investigations/{iid}/replay")
        assert r.status_code == 409
    finally:
        with SessionLocal() as db:
            i = db.get(Investigation, iid)
            if i:
                db.delete(i)
            db.commit()


def test_unknown_layer_and_investigation(client, fixture_run):
    iid, _, _ = fixture_run
    assert client.get(f"/api/investigations/{iid}/layers/nope").status_code == 404
    assert client.get("/api/investigations/inv-does-not-exist").status_code == 404
