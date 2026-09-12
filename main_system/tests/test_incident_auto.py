"""Automatic incident creation, zone routing, and the honesty of the gate.

The chain under test is the one the problem statement centres on:

    sealed run -> validation gate -> Incident -> point-in-polygon -> Zone
              -> responsible officer -> routed Alert

Four things here are load-bearing:

  * **The gate introduces no tuned constant.** It compares confidence against
    `tiling.detect_threshold` from the frozen `normalisation.yaml` -- the same
    0.5 the segmenter was calibrated at from a recorded sweep -- and area is
    not a gate at all. Standing rule 11 forbids tuning a threshold to
    manufacture a better demo, and a second confidence number invented here
    would mean the pipeline called a pixel oil while the case-opening logic
    disbelieved it.

  * **A fallback engine does not open a case.** The segmenter used to fall
    back to threshold-morphology SILENTLY on any full-size scene. An automatic
    case opened by an engine whose accuracy was never measured on these scenes
    is worse than no case.

  * **Three counts are never conflated.** `oil_candidates` is the screen's
    classification; `slick_regions` is the segmenter's connected regions,
    which carry no class. On the flagship those are 31 and 62. The area figure
    is of ALL segmented regions, and the verdict says so.

  * **"Nobody will see this" is reported, not hidden.** `unzoned` (no zone
    covers it) and `unassigned` (a zone does, and no officer) are separate
    states, because the fixes differ, and neither is quietly handed to an
    administrator to keep the queue looking clean.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "auto-incident-password"

# A point inside the seeded Zone 04 (lon >= 87, lat 11..18).
IN_ZONE_04 = [89.5, 13.5]
# Inside Zone 01 (lon < 88, lat >= 18), offshore Odisha.
IN_ZONE_01 = [85.5, 19.0]
# Arabian Sea: inside no declared zone.
OUTSIDE_ALL = [60.0, 15.0]


def _write_run(root: Path, run_id: str, *, centroid=IN_ZONE_04, engine="ml",
               confidence=0.6882, oil=31, lookalike=361, areas=(6.826, 3.1),
               acquired="2026-09-12T04:10:08Z", age_label="low"):
    """A sealed run's artefacts, in the exact shape the flagship writes them."""
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "detect_response.json").write_text(json.dumps({
        "scene_id": f"S1A_TEST_{run_id}",
        "mask_path": str(d / "raw_mask.tif"),
        "confidence": confidence,
        "engine": engine,
        "model_version": "unet-r34-fullcorpus-e48+yolo-screen",
        "runtime_ms": 1234,
        "candidates": (
            [{"bbox": [89.4, 13.4, 89.6, 13.6], "class": "oil",
              "score": confidence, "phenomenon": None}] * oil
            + [{"bbox": [89.1, 13.1, 89.2, 13.2], "class": "lookalike",
                "score": confidence, "phenomenon": None}] * lookalike),
    }), encoding="utf-8")

    features = []
    for i, area in enumerate(areas, start=1):
        # The largest region carries the centroid the incident is placed at.
        offset = 0.0 if area == max(areas) else 0.4
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [centroid[0] - 0.05, centroid[1] - 0.05],
                [centroid[0] + 0.05, centroid[1] - 0.05],
                [centroid[0] + 0.05, centroid[1] + 0.05],
                [centroid[0] - 0.05, centroid[1] + 0.05],
                [centroid[0] - 0.05, centroid[1] - 0.05]]]},
            "properties": {
                "slick_id": f"TEST_slick_{i:02d}",
                "confidence": confidence, "area_km2": area,
                "perimeter_km": 31.9,
                "centroid": [centroid[0] + offset, centroid[1] + offset],
                "major_axis_m": 8845.0, "minor_axis_m": 1186.0,
                "orientation_deg": 173.5, "damping_ratio": 1.38,
                "age_hours_estimate": 23.68, "age_confidence": 0.25,
                "age_method": "damping+fay",
                "age_confidence_label": age_label,
                "engine": engine, "source": "real"},
        })
    (d / "slick.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "metadata": {"scene_id": f"S1A_TEST_{run_id}",
                     "detected_utc": acquired, "acquired_utc": acquired,
                     "model_version": "unet-r34-fullcorpus-e48+yolo-screen",
                     "crs": "EPSG:4326"},
        "features": features,
    }), encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("auto_inc_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'auto.db').as_posix()}"
    os.environ["SECRET_KEY"] = "k" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db
    from backend.services.zone_seed import seed_bay_of_bengal

    init_db()
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        display_name=role, role=role, active=True))
        db.commit()
        seed_bay_of_bengal(db)

    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    os.environ.pop("DATABASE_URL", None)


def _as(client, role):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid",
                          "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def _uid(client, role) -> int:
    was = dict(client.cookies)
    try:
        _as(client, role)
        return client.get("/api/auth/me").json()["id"]
    finally:
        client.cookies.clear()
        for k, v in was.items():
            client.cookies.set(k, v)


def _seed_run(root, run_id, **kw):
    """Artefacts plus the `runs` row the service needs."""
    from backend.models.db import Run, SessionLocal, utcnow

    _write_run(root, run_id, **kw)
    with SessionLocal() as db:
        if db.get(Run, run_id) is None:
            db.add(Run(id=run_id, status="complete", started_utc=utcnow(),
                       finished_utc=utcnow(), stages_total=5, stages_real=5))
            db.commit()
    return run_id


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def test_the_gate_reads_the_deployed_threshold_not_its_own(env):
    """No second confidence number. The gate compares against the value in
    the frozen normalisation file, which the segmenter was calibrated at."""
    import yaml

    from backend.services import incident_auto

    cfg = yaml.safe_load(
        (REPO_ROOT / "main_system" / "config" / "normalisation.yaml")
        .read_text(encoding="utf-8"))
    deployed = cfg["tiling"]["detect_threshold"]

    _, root = env
    _seed_run(root, "run-thr")
    verdict = incident_auto.validate_detection("run-thr", runs_root=root / "runs")
    assert verdict.confidence_threshold == deployed


def test_a_validated_detection_passes_and_says_why(env):
    from backend.services import incident_auto

    _, root = env
    _seed_run(root, "run-pass", centroid=IN_ZONE_04)
    v = incident_auto.validate_detection("run-pass", runs_root=root / "runs")
    assert v.validated is True
    # Reasons on a PASS too. A gate that only explains failures cannot be
    # audited when it fires.
    assert any("deployed ML engine" in r for r in v.reasons)
    assert any("classified 31 candidate(s) as oil" in r for r in v.reasons)
    assert any("meets the deployed threshold" in r for r in v.reasons)


def test_a_fallback_engine_does_not_open_a_case(env):
    """The silent-fallback defect: an engine nobody evaluated must not create
    evidence on its own."""
    from backend.services import incident_auto

    _, root = env
    _seed_run(root, "run-fallback", centroid=IN_ZONE_04,
              engine="threshold_fallback")
    v = incident_auto.validate_detection("run-fallback",
                                         runs_root=root / "runs")
    assert v.validated is False
    assert any("not one of ['ml']" in r for r in v.reasons)
    assert any("still available for a human to promote" in r for r in v.reasons)


def test_zero_oil_candidates_does_not_open_a_case(env):
    """361 look-alikes and no oil is a scene with no oil in it."""
    from backend.services import incident_auto

    _, root = env
    _seed_run(root, "run-lookalikes", centroid=IN_ZONE_04, oil=0,
              lookalike=361)
    v = incident_auto.validate_detection("run-lookalikes",
                                         runs_root=root / "runs")
    assert v.validated is False
    assert v.oil_candidates == 0
    assert v.lookalike_candidates == 361
    assert any("classified 0 candidate(s) as oil" in r for r in v.reasons)


def test_confidence_below_the_deployed_threshold_is_refused(env):
    from backend.services import incident_auto

    _, root = env
    _seed_run(root, "run-lowconf", centroid=IN_ZONE_04, confidence=0.31)
    v = incident_auto.validate_detection("run-lowconf",
                                         runs_root=root / "runs")
    assert v.validated is False
    assert any("below the deployed detection threshold" in r for r in v.reasons)


def test_the_three_counts_are_kept_separate(env):
    """oil candidates (screen) vs segmented regions (segmenter, no class).
    On the flagship these are 31 and 62, and conflating them would report
    62 confirmed oil slicks."""
    from backend.services import incident_auto

    _, root = env
    _seed_run(root, "run-counts", centroid=IN_ZONE_04, oil=31, lookalike=361,
              areas=(6.826, 3.1, 1.2))
    v = incident_auto.validate_detection("run-counts", runs_root=root / "runs")
    assert v.oil_candidates == 31
    assert v.lookalike_candidates == 361
    assert v.slick_regions == 3
    assert v.total_area_km2 == pytest.approx(11.126)
    assert v.largest_area_km2 == pytest.approx(6.826)
    payload = v.as_dict()
    assert "different quantities" in payload["counting_note"]
    assert "NOT of confirmed" in payload["counting_note"]


def test_a_missing_run_directory_is_refused_not_crashed(env):
    from backend.services import incident_auto

    _, root = env
    v = incident_auto.validate_detection("run-nonexistent",
                                         runs_root=root / "runs")
    assert v.validated is False
    assert any("no run directory" in r for r in v.reasons)


def test_the_preview_endpoint_acts_on_nothing(env):
    from backend.models.db import Incident, SessionLocal

    client, root = env
    _seed_run(root, "run-preview", centroid=IN_ZONE_04)
    with SessionLocal() as db:
        before = db.query(Incident).count()

    _as(client, "auditor")
    body = client.get("/api/incidents/auto/preview/run-preview").json()
    assert body["validated"] is True
    with SessionLocal() as db:
        assert db.query(Incident).count() == before


# --------------------------------------------------------------------------
# creation and routing
# --------------------------------------------------------------------------

def test_a_validated_run_opens_a_routed_case_with_an_alert(env):
    client, root = env
    officer = _uid(client, "zone_officer")
    _as(client, "admin")
    assert client.post("/api/zones/zone-bob-04/assignments",
                       json={"user_id": officer}).status_code == 201

    _seed_run(root, "run-routed", centroid=IN_ZONE_04)
    _as(client, "investigator")
    r = client.post("/api/incidents/auto/run-routed")
    assert r.status_code == 201, r.text
    out = r.json()

    assert out["created"] is True
    assert out["routing"]["zone_id"] == "zone-bob-04"
    assert out["routing"]["routing"] == "zone"
    assert out["routing"]["officer"]["user_id"] == officer

    inc = client.get(f"/api/incidents/{out['incident_id']}").json()
    assert inc["origin"] == "auto"
    # `open`, never `attributed`. A model raised this; a human concludes it.
    assert inc["status"] == "open"
    assert inc["zone_id"] == "zone-bob-04"
    assert inc["zone_path"] == "zone-bob/zone-bob-04"
    assert inc["severity"] in ("low", "medium", "high", "critical")
    assert inc["detection_confidence"] == pytest.approx(0.6882)
    assert inc["source_run_id"] == "run-routed"
    # The routed officer is also the assignee, so the two cannot disagree.
    assert inc["assignee_id"] == officer

    # And the notes carry the limits, so an officer reading the case a week
    # later does not have to find the source to learn what the numbers mean.
    assert "AUTOMATIC DETECTION" in inc["notes"]
    assert "model output, not a confirmed spill" in inc["notes"]
    assert "LOW confidence" in inc["notes"]        # slick age
    assert "different quantity" in inc["notes"]


def test_the_alert_names_the_zone_and_the_officer(env):
    client, root = env
    _as(client, "investigator")
    alerts = client.get("/api/alerts", params={"limit": 200}).json()["alerts"]
    routed = [a for a in alerts if a["run_id"] == "run-routed"]
    assert routed, "no alert was raised for the routed run"
    alert = routed[0]
    assert alert["kind"] == "detection"
    assert alert["zone_id"] == "zone-bob-04"
    assert alert["routing"] == "zone"
    assert alert["routed_to"] == _uid(client, "zone_officer")
    assert "Zone 04" in alert["title"]
    # Severity is derived from area, never typed in.
    assert alert["severity"] in ("info", "warning", "critical")
    assert "Responsible officer" in alert["detail"]


def test_a_detection_outside_every_zone_is_unzoned_not_reassigned(env):
    """Open ocean outside the theatre. Reported as unrouted rather than
    quietly handed to an administrator so the queue looks clean."""
    client, root = env
    _seed_run(root, "run-outside", centroid=OUTSIDE_ALL)
    _as(client, "investigator")
    out = client.post("/api/incidents/auto/run-outside").json()
    assert out["created"] is True
    assert out["routing"]["zone_id"] is None
    assert out["routing"]["routing"] == "unzoned"
    assert out["routing"]["officer"] is None
    assert "outside every declared operational zone" in out["routing"]["detail"]
    assert "Draw a zone" in out["routing"]["detail"]

    inc = client.get(f"/api/incidents/{out['incident_id']}").json()
    assert inc["zone_id"] is None
    assert inc["assignee_id"] is None


def test_a_zone_with_no_officer_is_unassigned_not_unzoned(env):
    """Two different failures with two different fixes: draw a zone, versus
    assign somebody to one."""
    client, root = env
    _seed_run(root, "run-unassigned", centroid=IN_ZONE_01)
    _as(client, "investigator")
    out = client.post("/api/incidents/auto/run-unassigned").json()
    assert out["routing"]["zone_id"] == "zone-bob-01"
    assert out["routing"]["routing"] == "unassigned"
    assert out["routing"]["officer"] is None
    assert "no assigned officer" in out["routing"]["detail"]
    assert "Assign one" in out["routing"]["detail"]


def test_routing_escalates_to_the_parent_zones_officer(env):
    client, root = env
    reviewer = _uid(client, "reviewer")
    _as(client, "admin")
    assert client.post("/api/zones/zone-bob/assignments",
                       json={"user_id": reviewer}).status_code == 201

    _seed_run(root, "run-escalate", centroid=IN_ZONE_01)
    _as(client, "investigator")
    out = client.post("/api/incidents/auto/run-escalate").json()
    assert out["routing"]["zone_id"] == "zone-bob-01"
    assert out["routing"]["routing"] == "escalated"
    assert out["routing"]["officer"]["user_id"] == reviewer
    assert "escalated" in out["routing"]["detail"]

    _as(client, "admin")
    client.delete(f"/api/zones/zone-bob/assignments/{reviewer}")


def test_a_refused_run_returns_the_reasons_not_a_500(env):
    client, root = env
    _seed_run(root, "run-refused", centroid=IN_ZONE_04,
              engine="threshold_fallback")
    _as(client, "investigator")
    r = client.post("/api/incidents/auto/run-refused")
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["created"] is False
    assert body["verdict"]["validated"] is False
    assert any("not one of ['ml']" in x for x in body["verdict"]["reasons"])


def test_forcing_past_the_gate_records_that_it_was_forced(env):
    """A human may overrule the gate. A forced case must never look like a
    validated one."""
    client, root = env
    _as(client, "investigator")
    r = client.post("/api/incidents/auto/run-refused", params={"force": True})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["forced"] is True
    assert out["verdict"]["validated"] is False
    inc = client.get(f"/api/incidents/{out['incident_id']}").json()
    assert inc["origin"] == "auto"


def test_a_run_is_not_promoted_twice(env):
    client, root = env
    _as(client, "investigator")
    r = client.post("/api/incidents/auto/run-routed")
    assert r.status_code == 409
    assert "already belongs to" in r.json()["detail"]["reason"]


def test_severity_comes_from_area_and_is_labelled_a_triage_band(env):
    from backend.services import incident_auto

    assert incident_auto.severity_for_area(0.4) == "low"
    assert incident_auto.severity_for_area(4.0) == "medium"
    assert incident_auto.severity_for_area(40.0) == "high"
    assert incident_auto.severity_for_area(400.0) == "critical"

    client, root = env
    _seed_run(root, "run-big", centroid=IN_ZONE_04, areas=(160.0, 20.0))
    _as(client, "investigator")
    out = client.post("/api/incidents/auto/run-big").json()
    inc = client.get(f"/api/incidents/{out['incident_id']}").json()
    assert inc["severity"] == "critical"
    # Never presented as a measurement.
    assert "presentation choice, not a measurement" in inc["notes"]


# --------------------------------------------------------------------------
# the officer's queue
# --------------------------------------------------------------------------

def test_an_officer_sees_the_whole_queue_by_default(env):
    """Spec section 20: global situational awareness. `mine` is an opt-in
    view, not a permission boundary."""
    client, _ = env
    _as(client, "zone_officer")
    everything = client.get("/api/alerts", params={"limit": 200}).json()
    assert everything["scoped_to_you"] is False
    zones = {a["zone_id"] for a in everything["alerts"]}
    assert len(zones) > 1, "the officer should see alerts beyond their own zone"

    mine = client.get("/api/alerts",
                      params={"mine": True, "limit": 200}).json()
    assert mine["scoped_to_you"] is True
    assert len(mine["alerts"]) < len(everything["alerts"])
    for alert in mine["alerts"]:
        assert (alert["zone_id"] == "zone-bob-04"
                or alert["routed_to"] == _uid(client, "zone_officer"))


def test_the_summary_surfaces_unrouted_alerts(env):
    """An alert nobody is responsible for is the failure zone routing exists
    to remove. A summary that omitted it would make the queue look handled."""
    client, _ = env
    _as(client, "zone_officer")
    body = client.get("/api/alerts/summary").json()
    assert body["unrouted"] >= 1
    assert set(body["unrouted_reasons"]) <= {"unzoned", "unassigned"}
    assert "by_zone" in body
    assert body["mine"] >= 1


def test_alerts_can_be_filtered_by_zone_and_routing(env):
    client, _ = env
    _as(client, "admin")
    by_zone = client.get("/api/alerts",
                         params={"zone_id": "zone-bob-04",
                                 "limit": 200}).json()["alerts"]
    assert by_zone and all(a["zone_id"] == "zone-bob-04" for a in by_zone)

    unzoned = client.get("/api/alerts",
                         params={"routing": "unzoned",
                                 "limit": 200}).json()["alerts"]
    assert unzoned and all(a["routing"] == "unzoned" for a in unzoned)


# --------------------------------------------------------------------------
# backfill
# --------------------------------------------------------------------------

def test_backfilling_zones_never_guesses(env):
    """An incident outside every zone stays unzoned rather than being snapped
    to the nearest one."""
    from backend.models.db import Incident, SessionLocal, utcnow

    client, _ = env
    with SessionLocal() as db:
        db.add(Incident(id="INC-OLD-001", title="legacy, in zone",
                        geometry_json=json.dumps(
                            {"type": "Point", "coordinates": IN_ZONE_04}),
                        status="open", created_utc=utcnow()))
        db.add(Incident(id="INC-OLD-002", title="legacy, outside",
                        geometry_json=json.dumps(
                            {"type": "Point", "coordinates": OUTSIDE_ALL}),
                        status="open", created_utc=utcnow()))
        db.add(Incident(id="INC-OLD-003", title="legacy, no geometry",
                        status="open", created_utc=utcnow()))
        db.commit()

    _as(client, "admin")
    body = client.post("/api/incidents/backfill-zones").json()
    updated = {u["incident_id"]: u["zone_id"] for u in body["updated"]}
    assert updated.get("INC-OLD-001") == "zone-bob-04"
    assert "INC-OLD-002" not in updated
    assert "INC-OLD-003" not in updated
    assert body["skipped"] >= 2
    assert "nearest guess" in body["note"]


def test_backfill_is_admin_only(env):
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor",
                 "zone_officer"):
        _as(client, role)
        assert client.post(
            "/api/incidents/backfill-zones").status_code == 403, role


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

def test_auto_creation_and_routing_are_both_audited(env):
    client, _ = env
    _as(client, "auditor")
    entries = client.get("/api/audit", params={"limit": 400}).json()["items"]
    actions = {e["action"] for e in entries}
    assert "incident.create" in actions
    assert "alert.route" in actions

    routed = next(e for e in entries if e["action"] == "alert.route")
    detail = json.loads(routed["detail"])
    assert "routing" in detail and "zone_id" in detail
    assert client.get("/api/audit/verify").json()["ok"] is True
