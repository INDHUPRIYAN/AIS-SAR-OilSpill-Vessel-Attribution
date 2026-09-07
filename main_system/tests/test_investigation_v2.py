"""Investigation v2: AOI registration, jobs, cancellation, rerun and SSE.

PROMPT 13. What each group is defending:

* **AOI validation** -- an AOI drawn in the wrong place produces a watch that
  polls forever and sees nothing, which is indistinguishable from a working one
  until somebody notices `scenes_seen` stuck at zero. Land areas, swapped
  coordinate order and absurd spans are refused at registration with a message
  that says which.
* **Cancellation** -- cooperative and checked between stages. A cancelled run
  must be left UNSEALED: sealing is what makes a run quotable as evidence, and
  a run that stopped half way is not evidence of anything.
* **Rerun** -- a NEW run id, always, carrying the inputs the original was
  given. Re-running into the same id would overwrite the artefacts an
  investigation was concluded from.
* **SSE** -- one event per observed stage change, ending by itself.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

GULF = {"type": "Polygon", "coordinates": [[
    [-92.9, 27.3], [-90.1, 27.3], [-90.1, 29.1], [-92.9, 29.1], [-92.9, 27.3]]]}


@pytest.fixture(scope="module")
def client(sign_in_helper, tmp_path_factory):
    root = tmp_path_factory.mktemp("inv2")
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'i.db').as_posix()}"
    os.environ["DATA_ROOT"] = str(root)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        yield c
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATA_ROOT", None)


# --------------------------------------------------------------------------
# AOI registration
# --------------------------------------------------------------------------

def test_a_drawn_polygon_is_registered_with_its_bbox(client):
    r = client.post("/api/aois", json={
        "id": "gulf-test", "name": "Gulf test box", "geometry": GULF,
        "ais_region": "us-gulf"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["bbox"] == [-92.9, 27.3, -90.1, 29.1]
    assert body["geometry"]["type"] == "Polygon", "the drawn shape is kept, not just its bbox"
    assert body["source"] == "api"
    assert body["sea_check"]["tested"] is True
    assert body["sea_check"]["sea_fraction"] > 0


def test_an_aoi_over_land_is_refused(client):
    """An oil slick cannot appear there, so the operator drew in the wrong place."""
    kansas = {"type": "Polygon", "coordinates": [[
        [-100.0, 38.0], [-99.0, 38.0], [-99.0, 39.0], [-100.0, 39.0], [-100.0, 38.0]]]}
    r = client.post("/api/aois", json={"id": "kansas", "name": "Kansas", "geometry": kansas})
    assert r.status_code == 422
    assert "land" in r.json()["detail"].lower()


def test_swapped_coordinate_order_is_refused(client):
    """[lat, lon] instead of [lon, lat] is the classic paste error."""
    swapped = {"type": "Polygon", "coordinates": [[
        [27.3, -92.9], [29.1, -92.9], [29.1, -90.1], [27.3, -90.1], [27.3, -92.9]]]}
    r = client.post("/api/aois", json={"id": "swapped", "name": "Swapped", "geometry": swapped})
    assert r.status_code == 422
    assert "latitude" in r.json()["detail"].lower()


@pytest.mark.parametrize("geometry,why", [
    ({"type": "Point", "coordinates": [-91.0, 28.0]}, "not a polygon"),
    ({"type": "Polygon", "coordinates": [[[-92.9, 27.3], [-90.1, 27.3], [-90.1, 29.1]]]},
     "ring not closed"),
    ({"type": "Polygon", "coordinates": [[
        [-100.0, 10.0], [-60.0, 10.0], [-60.0, 40.0], [-100.0, 40.0], [-100.0, 10.0]]]},
     "far too large"),
])
def test_unusable_geometry_is_refused(client, geometry, why):
    r = client.post("/api/aois", json={"id": "bad-shape", "name": "Bad", "geometry": geometry})
    assert r.status_code == 422, f"accepted a geometry that is {why}"


def test_an_aoi_needs_a_shape(client):
    r = client.post("/api/aois", json={"id": "shapeless", "name": "No shape"})
    assert r.status_code == 422


def test_duplicate_ids_are_refused(client):
    body = {"id": "dupe", "name": "First", "geometry": GULF}
    assert client.post("/api/aois", json=body).status_code == 201
    again = client.post("/api/aois", json={**body, "name": "Second"})
    assert again.status_code == 409


def test_an_aoi_can_be_edited_and_retired(client):
    client.post("/api/aois", json={"id": "editable", "name": "Before", "geometry": GULF})

    patched = client.patch("/api/aois/editable", json={"name": "After", "enabled": False})
    assert patched.status_code == 200
    assert patched.json()["name"] == "After"
    assert patched.json()["enabled"] is False

    deleted = client.delete("/api/aois/editable")
    assert deleted.status_code == 200
    # The watch high-water mark survives, or delete-then-recreate would re-open
    # investigations for every scene already handled.
    assert "kept" in deleted.json()["watch_state"]
    assert client.get("/api/aois/editable").status_code == 404


def test_the_yaml_registry_is_migrated_in(client):
    """Existing deployments must not have to re-register their AOIs."""
    listing = client.get("/api/aois").json()
    sources = {a["id"]: a["source"] for a in listing}
    assert any(v == "yaml" for v in sources.values()), \
        f"no YAML-migrated AOI in {sources}"
    for aoi in listing:
        if aoi["source"] == "yaml":
            # The YAML only ever had a bbox. Inventing a rectangle here would
            # claim someone drew one.
            assert aoi["geometry"] is None


# --------------------------------------------------------------------------
# investigation v2 fields
# --------------------------------------------------------------------------

def test_a_reversed_time_window_is_refused(client):
    r = client.post("/api/investigations", json={
        "name": "Backwards", "window_start_utc": "2023-01-08T00:00:00Z",
        "window_end_utc": "2023-01-07T00:00:00Z"})
    assert r.status_code == 422


def test_an_unknown_aoi_is_refused(client):
    r = client.post("/api/investigations", json={"name": "Ghost", "aoi_id": "no-such-aoi"})
    assert r.status_code == 400


def test_an_uncached_product_is_refused_with_the_reason(client):
    """A catalogue hit is not a downloaded scene, and the 404 says so."""
    r = client.post("/api/investigations", json={
        "name": "Not fetched", "scene_product_id": "S1A_NEVER_DOWNLOADED"})
    assert r.status_code == 404
    assert "not in the local scene cache" in r.json()["detail"]


def test_an_aoi_investigation_records_the_footprint(client):
    client.post("/api/aois", json={"id": "footprint", "name": "Footprint", "geometry": GULF})
    r = client.post("/api/investigations", json={"name": "AOI run", "aoi_id": "footprint"})
    assert r.status_code in (200, 201), r.text
    inv_id = r.json()["id"]
    detail = client.get(f"/api/investigations/{inv_id}").json()
    assert detail["bbox"] == [-92.9, 27.3, -90.1, 29.1]
    assert detail["bbox_source"] == "aoi",         "an investigation with no run yet must still show the area it covers"


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------

def test_cancel_is_cooperative_and_leaves_the_run_unsealed(tmp_path):
    """The pipeline stops at a stage boundary and writes no manifest."""
    from backend.services.pipeline.run import (RunCancelled, clear_cancel_check,
                                               flush_status, set_cancel_check)

    class _Stage:
        def __init__(self, name, status=None):
            self.name, self.status = name, status
            self.owner = self.engine_used = self.source = self.detail = None
            self.seconds = 0.0
            self.warnings = []
            self.output = f"{name}.json"

        def to_dict(self):
            return {"stage": self.name, "status": self.status}

    stages = [_Stage("detect", "ok"), _Stage("characterise")]
    out = tmp_path / "run"
    out.mkdir()

    set_cancel_check("r1", lambda: False)
    flush_status(out, "r1", "S1A", stages)          # no cancel -> returns
    assert (out / "status.json").exists()

    set_cancel_check("r1", lambda: True)
    with pytest.raises(RunCancelled):
        flush_status(out, "r1", "S1A", stages)
    clear_cancel_check("r1")

    # The status of the completed stage still reached disk before the stop.
    written = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert written["stages"][0]["status"] == "ok"
    assert not (out / "manifest.json").exists(), \
        "a cancelled run must not be sealed"


def test_a_broken_cancel_check_does_not_stop_a_healthy_run(tmp_path):
    """Failing open: the worst case is a run that finishes."""
    from backend.services.pipeline.run import (clear_cancel_check, flush_status,
                                               set_cancel_check)

    class _Stage:
        name, status, output = "detect", "ok", "detect.json"
        owner = engine_used = source = detail = None
        seconds = 0.0
        warnings: list = []

        def to_dict(self):
            return {"stage": "detect", "status": "ok"}

    out = tmp_path / "run2"
    out.mkdir()

    def _explode():
        raise RuntimeError("cancel backend is down")

    set_cancel_check("r2", _explode)
    flush_status(out, "r2", "S1A", [_Stage()])      # must not raise
    clear_cancel_check("r2")


def test_cancelling_a_finished_job_is_reported_not_pretended(client):
    from backend.models.db import Job, Run, SessionLocal

    with SessionLocal() as db:
        db.add(Run(id="run-done", status="complete"))
        db.add(Job(id="job-run-done", run_id="run-done", status="complete"))
        db.commit()

    r = client.post("/api/jobs/job-run-done/cancel")
    assert r.status_code == 409
    assert "already complete" in r.json()["detail"]


def test_cancel_marks_the_status_file_rather_than_leaving_an_absence(tmp_path):
    """"No manifest" is not a statement. `cancelled` is."""
    from backend.services import jobs

    run_dir = Path(jobs._settings().runs_root) / "run-cancel-marker"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps({
        "run_id": "run-cancel-marker",
        "stages": [{"stage": "detect", "status": "ok"},
                   {"stage": "characterise", "status": "running"}]}),
        encoding="utf-8")

    jobs.mark_cancelled("run-cancel-marker")
    payload = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert payload["run_status"] == "cancelled"
    assert "not sealed" in payload["note"].lower()
    assert payload["stages"][0]["status"] == "ok", "completed stages keep their result"
    assert payload["stages"][1]["status"] == "cancelled"


# --------------------------------------------------------------------------
# rerun
# --------------------------------------------------------------------------

def test_rerun_uses_the_inputs_the_original_was_given(client):
    from backend.models.db import Job, Run, SessionLocal

    with SessionLocal() as db:
        db.add(Run(id="run-orig", status="complete"))
        db.add(Job(id="job-run-orig", run_id="run-orig", status="complete",
                   inputs_json=json.dumps({"scene_path": "/scenes/a.tif",
                                           "scene_meta_path": "/scenes/a.json",
                                           "engine": "ml"})))
        db.commit()

        from backend.services import jobs

        inputs = jobs.inputs_for_rerun(db, "run-orig")
    assert inputs["scene_path"] == "/scenes/a.tif"
    assert inputs["engine"] == "ml"
    assert inputs["source"] == "job record"


def test_rerun_of_an_unknown_run_is_404(client):
    assert client.post("/api/runs/nope/rerun").status_code == 404


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------

def test_sse_reports_every_stage_then_ends(client):
    """Five stages and a terminal event, from a status.json already on disk."""
    from backend.core.config import get_settings
    from backend.models.db import Run, SessionLocal

    run_id = "run-sse"
    with SessionLocal() as db:
        db.add(Run(id=run_id, status="complete"))
        db.commit()

    run_dir = Path(get_settings().runs_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps({
        "run_id": run_id, "run_status": "complete",
        "stages": [{"stage": n, "status": "ok"} for n in
                   ("detect", "characterise", "drift_hindcast",
                    "drift_forecast", "attribution")]}), encoding="utf-8")

    with client.stream("GET", f"/api/events/runs/{run_id}") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    stage_events = [l for l in body.splitlines() if l == "event: stage"]
    assert len(stage_events) == 5, f"expected 5 stage events, got {len(stage_events)}"
    assert "event: end" in body
    for name in ("detect", "characterise", "drift_hindcast", "drift_forecast",
                 "attribution"):
        assert f'"{name}"' in body


def test_sse_for_an_unknown_run_is_404(client):
    assert client.get("/api/events/runs/no-such-run").status_code == 404


def test_sse_says_it_is_waiting_rather_than_going_silent(client):
    """Silence and "not started yet" look identical to a client."""
    from backend.models.db import Run, SessionLocal

    with SessionLocal() as db:
        db.add(Run(id="run-quiet", status="failed"))
        db.commit()

    with client.stream("GET", "/api/events/runs/run-quiet") as response:
        body = "".join(response.iter_text())
    assert "event: waiting" in body
    assert "event: end" in body
