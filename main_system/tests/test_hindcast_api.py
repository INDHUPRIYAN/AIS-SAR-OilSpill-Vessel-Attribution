"""BAYES-TRACK hindcast through the OceanTrace API: the whole chain on a spill
whose origin is known, plus the things that only exist at this level -- sessions,
roles, the WebSocket, failure containment, and hindcasting a real run's slick."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from shapely.geometry import Point, box, mapping, shape

SCENE_TIME = datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)
FAST = {"members": 24, "particles": 600, "member_chunk": 8, "verify_particles": 250}
PASSWORD = "suite-fixture-password"
PUBLIC = ("scene_meta", "slick_polygon_geojson", "oil_type", "forcing", "config", "archive", "label")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("hindcast_api_root")
    saved = {k: os.environ.get(k) for k in ("DATA_ROOT", "DATABASE_URL", "SECRET_KEY", "HINDCAST_DRIFT_BACKEND",
                                            "OT_ADMIN_EMAIL", "OT_ADMIN_PASSWORD")}
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'hindcast_api.db').as_posix()}"
    os.environ["SECRET_KEY"] = "a" * 64
    os.environ["HINDCAST_DRIFT_BACKEND"] = "numpy"
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid", password_hash=security.hash_password(PASSWORD),
                        role=role, active=True))
        db.commit()
    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]


def _as(client, role):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": f"{role}@example.invalid", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def _wait(client, job_id, timeout_s=300.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = client.get(f"/api/hindcast/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"hindcast job {job_id} did not finish in {timeout_s} s")


def _demo_request(**kw):
    from backend.services.hindcast.synthetic import build_demo_request

    return build_demo_request(scene_time=SCENE_TIME, **kw)


@pytest.fixture(scope="module")
def finished(env):
    client, _ = env
    _as(client, "investigator")
    request = _demo_request(tau_true_h=18, clean_scene_h=36, config=FAST)
    created = client.post("/api/hindcast/jobs", json={k: request[k] for k in PUBLIC})
    assert created.status_code == 202, created.text
    return _wait(client, created.json()["job_id"]), request["truth"]


# ------------------------------------------------------------ the whole chain --

def test_the_true_origin_lies_inside_the_90_percent_region(finished):
    job, truth = finished
    assert job["status"] == "succeeded", job["error"]
    result = job["result"]
    assert shape(result["hdr90"]).covers(Point(truth["lon"], truth["lat"]))
    window = result["release_window"]
    assert window["tau_lo_h"] <= truth["tau_h"] <= window["tau_hi_h"]
    assert shape(result["hdr90"]).buffer(1e-4).contains(shape(result["hdr50"]))
    assert job["created_by"] == "investigator@example.invalid"


def test_every_engine_succeeded_in_order_with_its_headline_metrics(finished):
    job, _ = finished
    runs = job["engines"]
    assert [r["engine_id"] for r in runs] == ["ingest_engine", "bounds_engine", "forcing_engine", "drift_engine",
                                              "shape_engine", "verify_engine", "posterior_engine"]
    assert all(r["status"] == "succeeded" and r["percent"] == 100.0 and r["duration_ms"] is not None for r in runs)
    by_id = {r["engine_id"]: r["metrics"] for r in runs}
    assert by_id["bounds_engine"]["tau_max_h"] == 36                                  # the clean archive scene
    assert by_id["forcing_engine"]["wind_scale"] == pytest.approx(1.08, abs=0.01)     # the planted wind bias
    assert by_id["forcing_engine"]["d_dir_deg"] == pytest.approx(6.0, abs=0.3)
    assert by_id["drift_engine"]["members_done"] == 24
    assert by_id["drift_engine"]["particles_simulated"] == 24 * 600
    assert by_id["drift_engine"]["snapshots_saved"] == 24 * 36
    assert "map_position" in by_id["posterior_engine"] and all(r["logs"] for r in runs)


def test_job_files_live_under_the_data_root(env, finished):
    _, root = env
    job, _ = finished
    folder = root / "hindcast" / job["id"]
    assert (folder / "l_drift.tif").exists() and (folder / "forcing.npz").exists()
    assert not list(folder.glob("*.nc")), "engine hand-off must not be NetCDF (HDF5 is not thread-safe here)"


def test_registry_status_history_and_particles(env, finished):
    client, _ = env
    job, _ = finished
    registry = client.get("/api/engines").json()
    assert [e["stage"] for e in registry] == ["Ingest", "Stage 0", "Stage 1", "Stage 2", "Stage 3", "Stage 4", "Stage 5"]
    assert all(2 <= len(e["metric_keys"]) <= 4 for e in registry)

    status = client.get("/api/engines/status", params={"job_id": job["id"]}).json()
    assert status["job"]["status"] == "succeeded" and len(status["engines"]) == 7
    assert client.get("/api/engines/status", params={"job_id": "nope"}).status_code == 404
    assert client.get("/api/engines/drift_engine/runs").json()[0]["job_id"] == job["id"]
    assert client.get("/api/engines/warp_engine/runs").status_code == 404

    cloud = client.get(f"/api/hindcast/jobs/{job['id']}/particles", params={"tau": 12, "max_points": 900}).json()
    assert cloud["properties"]["members"] == 24
    assert all(f["geometry"]["type"] == "MultiPoint" for f in cloud["features"])
    assert client.get(f"/api/hindcast/jobs/{job['id']}/particles", params={"tau": 99}).status_code == 404


# --------------------------------------------------------- sessions and roles --

def test_reading_needs_a_session_and_starting_needs_an_operator_role(env, finished):
    client, _ = env
    job, _ = finished
    request = _demo_request(tau_true_h=6, clean_scene_h=10, config={"members": 4, "particles": 100})
    body = {k: request[k] for k in PUBLIC}

    client.cookies.clear()
    assert client.get("/api/engines").status_code == 401
    assert client.get(f"/api/hindcast/jobs/{job['id']}").status_code == 401
    assert client.post("/api/hindcast/demo").status_code == 401

    for role in ("auditor", "reviewer", "zone_officer"):
        _as(client, role)
        assert client.get("/api/engines/status").status_code == 200, f"{role} could not read engine status"
        for path, payload in (("/api/hindcast/jobs", body), ("/api/hindcast/demo", {}),
                              ("/api/hindcast/from_run/anything", {})):
            assert client.post(path, json=payload).status_code == 403, f"{role} started a hindcast via {path}"


def test_starting_a_job_is_audited(env, finished):
    client, _ = env
    job, _ = finished
    _as(client, "admin")
    rows = client.get("/api/audit", params={"action": "hindcast.start"}).json()
    rows = rows.get("items") or rows.get("rows") or rows
    assert any(r.get("resource") == job["id"] and "investigator" in (r.get("actor") or "") for r in rows)


# ------------------------------------------------------------------ websocket --

def test_websocket_refuses_a_caller_without_a_session(env, finished):
    from starlette.websockets import WebSocketDisconnect

    client, _ = env
    job, _ = finished
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/engines/{job['id']}"):
            pass
    assert exc.value.code == 4401


def test_websocket_streams_a_job_from_snapshot_to_success(env):
    client, _ = env
    _as(client, "analyst")
    request = _demo_request(tau_true_h=6, clean_scene_h=10, config={"members": 4, "particles": 200, "member_chunk": 2})
    job_id = client.post("/api/hindcast/jobs", json={k: request[k] for k in PUBLIC}).json()["job_id"]
    seen, first = set(), None
    # wss: the session cookie is Secure, and the test client defaults to ws://
    with client.websocket_connect(f"wss://testserver/ws/engines/{job_id}") as ws:
        deadline = time.time() + 180
        while time.time() < deadline:
            msg = json.loads(ws.receive_text())
            first = first or msg
            if msg["type"] == "engine":
                seen.add((msg["engine_id"], msg["status"]))
            if msg["type"] == "job" and msg["status"] in ("succeeded", "failed"):
                break
    assert first["type"] == "snapshot" and len(first["engines"]) == 7
    assert ("posterior_engine", "succeeded") in seen and any(s == "running" for _, s in seen)


# -------------------------------------------------------------------- failure --

def test_a_failing_engine_fails_the_job_and_skips_everything_downstream(env):
    client, _ = env
    _as(client, "investigator")
    body = {"scene_meta": {"scene_id": "BROKEN", "acquired_utc": SCENE_TIME.isoformat()},
            "slick_polygon_geojson": mapping(box(88.0, 14.0, 88.05, 14.05)), "oil_type": "light"}
    job = _wait(client, client.post("/api/hindcast/jobs", json=body).json()["job_id"])
    assert job["status"] == "failed" and job["error"].startswith("forcing_engine:")
    assert {r["engine_id"]: r["status"] for r in job["engines"]} == {
        "ingest_engine": "succeeded", "bounds_engine": "succeeded", "forcing_engine": "failed",
        "drift_engine": "skipped", "shape_engine": "skipped", "verify_engine": "skipped",
        "posterior_engine": "skipped"}
    assert job["result"] is None


def test_request_validation(env):
    client, _ = env
    _as(client, "investigator")
    meta, poly = {"acquired_utc": SCENE_TIME.isoformat()}, mapping(box(0, 0, 1, 1))
    assert client.post("/api/hindcast/jobs", json={"scene_meta": meta}).status_code == 422
    bad = client.post("/api/hindcast/jobs", json={"scene_meta": meta, "slick_polygon_geojson": poly,
                                                  "config": {"members": 0}})
    assert bad.status_code == 422 and "members" in bad.text
    assert client.post("/api/hindcast/jobs", json={"scene_meta": meta, "slick_polygon_geojson": poly,
                                                   "oil_type": "custard"}).status_code == 422


def test_jobs_left_running_by_a_dead_process_are_swept_at_boot(env):
    from backend.models.hindcast import HindcastEngineRun, HindcastJob, session_scope, sweep_interrupted

    with session_scope() as db:
        db.add(HindcastJob(id="ghost", status="running"))
        db.add(HindcastEngineRun(job_id="ghost", engine_id="drift_engine", stage_order=4, status="running"))
        db.add(HindcastEngineRun(job_id="ghost", engine_id="shape_engine", stage_order=5, status="pending"))
    assert sweep_interrupted() >= 1
    with session_scope() as db:
        ghost = db.get(HindcastJob, "ghost")
        assert ghost.status == "failed" and "restart" in ghost.error
        assert [r.status for r in ghost.engine_runs] == ["failed", "skipped"]


# ------------------------------------------------- hindcasting an OceanTrace run --

def _stage_run(root, run_id, forcing_hours_before):
    """A run folder as the OceanTrace pipeline leaves it: scene_meta, a slick, and
    (in the run folder, which the resolver searches first) its forcing grids."""
    from backend.services.hindcast.forcing import ForcingField

    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    centre = (-91.0, 28.0)
    (run_dir / "scene_meta.json").write_text(json.dumps({
        "scene_id": f"S1A_TEST_{run_id}", "acquired_utc": SCENE_TIME.isoformat(),
        "bbox": [centre[0] - 1.0, centre[1] - 1.0, centre[0] + 1.0, centre[1] + 1.0], "crs": "EPSG:4326"}))
    small, large = box(-91.30, 28.20, -91.29, 28.21), box(-91.03, 27.98, -90.98, 28.02)
    (run_dir / "slick.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(small), "properties": {"slick_id": "S-2", "area_km2": 1.2}},
        {"type": "Feature", "geometry": mapping(large), "properties": {"slick_id": "S-1", "area_km2": 21.0}}]}))

    hours = forcing_hours_before + 6
    start = SCENE_TIME - timedelta(hours=forcing_hours_before)
    lons, lats = np.arange(centre[0] - 1.2, centre[0] + 1.21, 0.1), np.arange(centre[1] - 1.2, centre[1] + 1.21, 0.1)
    times = np.datetime64(start.replace(tzinfo=None), "s") + np.arange(hours) * np.timedelta64(3600, "s")
    data = np.zeros((hours, 6, lats.size, lons.size), dtype=np.float32)
    data[:, 0], data[:, 1], data[:, 2], data[:, 3] = 0.15, 0.05, 6.0, -2.0
    ds = ForcingField(times, lats, lons, data).to_dataset()
    ds[["u_wind", "v_wind"]].rename({"u_wind": "u10", "v_wind": "v10"}).to_netcdf(run_dir / "wind.nc")
    ds[["u_curr", "v_curr"]].rename({"u_curr": "uo", "v_curr": "vo"}).to_netcdf(run_dir / "currents.nc")
    return large


def test_a_real_runs_largest_slick_is_hindcast_with_its_cached_forcing(env):
    client, root = env
    largest = _stage_run(root, "inv-hc-real", forcing_hours_before=20)
    _as(client, "investigator")
    created = client.post("/api/hindcast/from_run/inv-hc-real", json={"oil_type": "medium", "config": FAST})
    assert created.status_code == 202, created.text
    job = _wait(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["source"] == "run:inv-hc-real" and "S-1" in job["label"]
    assert shape(job["result"]["slick"]).equals_exact(largest, 1e-9)

    forcing = next(r for r in job["engines"] if r["engine_id"] == "forcing_engine")
    assert "OceanTrace metocean cache" in forcing["metrics"]["source"]
    assert "wind + currents" in forcing["metrics"]["source"]
    # Medium oil allows 120 h, but the forcing on disk starts 20 h before the pass.
    assert forcing["metrics"]["tau_clipped_to_h"] == 19
    assert any("SEARCH WINDOW CLIPPED from 120 h to 19 h" in line for line in forcing["logs"])
    assert job["result"]["release_window"]["tau_hi_h"] <= 19
    # uniform flow to the ENE at ~0.3 m/s: the origin is up-stream, to the WSW of the slick
    assert job["result"]["map"]["lon"] < largest.centroid.x and job["result"]["map"]["lat"] < largest.centroid.y + 0.05
    assert "truth_check" not in job["result"]


def test_runs_that_cannot_be_hindcast_are_refused_with_the_reason(env):
    client, root = env
    _as(client, "investigator")
    assert client.post("/api/hindcast/from_run/no-such-run").status_code == 404
    assert client.post("/api/hindcast/from_run/..%2F..%2Fetc").status_code in (404, 405)

    clean = root / "runs" / "inv-hc-clean"
    clean.mkdir(parents=True)
    (clean / "scene_meta.json").write_text(json.dumps({"scene_id": "X", "acquired_utc": SCENE_TIME.isoformat()}))
    r = client.post("/api/hindcast/from_run/inv-hc-clean")
    assert r.status_code == 409 and "nothing to hindcast" in r.text          # the run found no oil

    _stage_run(root, "inv-hc-mock", forcing_hours_before=20)
    (root / "runs" / "inv-hc-mock" / "status.json").write_text(json.dumps(
        {"stages": [{"stage": "characterise", "status": "mock"}]}))
    r = client.post("/api/hindcast/from_run/inv-hc-mock")
    assert r.status_code == 409 and "MOCK" in r.text                         # a pre-2026-09-20 fabricated slick
