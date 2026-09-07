"""Run history, and the funnel that explains one run's exclusions.

Two audit findings meet here.

RH-01..05: `/api/runs` returned a bare list truncated at 50, with no total, no
filters and no sort. A caller could not tell a short page from the end of the
data, and the CLI's runs had no database row at all, so work that existed on
disk was invisible in the UI.

H5: the funnel the UI needs (`found -> spatial -> temporal -> trajectory ->
candidates`) could not be built, because normalisation dropped the engine's
`filter_reason`/`failed_gates` and kept only a humanised sentence. Counting
gates by pattern-matching English prose is not a contract.

Filter and pagination correctness is checked against brute force so a wrong
WHERE clause cannot agree with itself.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "runs-test-password"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("runs_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'runs.db').as_posix()}"
    os.environ["SECRET_KEY"] = "r" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import (Investigation, ROLES, Run, SessionLocal,
                                   User, init_db)

    init_db()
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        role=role, active=True))
        db.add(Investigation(id="inv-hist", name="history"))
        for i in range(14):
            db.add(Run(
                id=f"run-{i:02d}", investigation_id="inv-hist",
                scene_id=f"S1A_SCENE_{'A' if i % 2 else 'B'}",
                status="complete" if i % 3 else "failed",
                started_utc=base + timedelta(hours=i),
                region="IN-E" if i % 2 else "IN-W",
                top_score=round(0.4 + i / 100, 3),
                slick_area_km2=float(i),
                archived=(i == 13),
            ))
        db.commit()

    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    os.environ.pop("DATABASE_URL", None)


def _as(client, role="analyst"):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def _all(client, **params):
    return client.get("/api/runs", params={"limit": 200, **params}).json()


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------

def test_listing_reports_a_total(env):
    """Without it a UI cannot page, and a short page is indistinguishable from
    the end of the data."""
    client, _ = env
    _as(client)
    body = _all(client)
    assert set(body) >= {"total", "offset", "limit", "items"}
    assert body["total"] == len(body["items"])


def test_archived_runs_are_hidden_but_not_deleted(env):
    """A run is evidence. Hiding it is a listing preference, not a lifecycle."""
    client, _ = env
    _as(client)
    default = {r["run_id"] for r in _all(client)["items"]}
    withall = {r["run_id"] for r in _all(client, archived=True)["items"]}
    assert "run-13" not in default
    assert "run-13" in withall


def test_denormalised_outcome_is_served(env):
    client, _ = env
    _as(client)
    row = next(r for r in _all(client)["items"] if r["run_id"] == "run-05")
    assert row["top_score"] == 0.45
    assert row["slick_area_km2"] == 5.0
    assert "incident_id" in row


# --------------------------------------------------------------------------
# filters, checked against brute force
# --------------------------------------------------------------------------

def test_status_filter_matches_brute_force(env):
    client, _ = env
    _as(client)
    expected = {r["run_id"] for r in _all(client)["items"] if r["status"] == "failed"}
    got = {r["run_id"] for r in _all(client, status="failed")["items"]}
    assert got == expected and got


def test_region_filter_matches_brute_force(env):
    client, _ = env
    _as(client)
    expected = {r["run_id"] for r in _all(client)["items"] if r["region"] == "IN-E"}
    got = {r["run_id"] for r in _all(client, region="IN-E")["items"]}
    assert got == expected and got


def test_text_search_matches_brute_force(env):
    client, _ = env
    _as(client)
    expected = {r["run_id"] for r in _all(client)["items"]
                if "SCENE_A" in (r["scene_id"] or "")}
    got = {r["run_id"] for r in _all(client, q="SCENE_A")["items"]}
    assert got == expected and got


def test_date_range_filter(env):
    client, _ = env
    _as(client)
    body = _all(client, **{"from": "2026-03-01T04:00:00Z", "to": "2026-03-01T08:00:00Z"})
    ids = {r["run_id"] for r in body["items"]}
    assert ids == {"run-04", "run-05", "run-06", "run-07", "run-08"}


def test_pagination_is_a_stable_partition(env):
    client, _ = env
    _as(client)
    total = _all(client)["total"]
    seen, offset = [], 0
    while offset < total:
        page = client.get("/api/runs", params={"limit": 4, "offset": offset,
                                               "sort": "started_utc",
                                               "order": "asc"}).json()
        seen.extend(r["run_id"] for r in page["items"])
        offset += 4
    assert len(seen) == total
    assert len(set(seen)) == total, "a run appeared on two pages"


def test_sorting_both_directions_and_by_score(env):
    client, _ = env
    _as(client)
    asc = [r["run_id"] for r in _all(client, sort="started_utc", order="asc")["items"]]
    desc = [r["run_id"] for r in _all(client, sort="started_utc", order="desc")["items"]]
    assert asc == list(reversed(desc))

    by_score = [r["top_score"] for r in _all(client, sort="top_score",
                                             order="desc")["items"]]
    assert by_score == sorted(by_score, reverse=True)


def test_archive_route_round_trips(env):
    client, _ = env
    _as(client, "investigator")
    assert client.post("/api/runs/run-00/archive").status_code == 200
    _as(client)
    assert "run-00" not in {r["run_id"] for r in _all(client)["items"]}

    _as(client, "investigator")
    assert client.post("/api/runs/run-00/archive",
                       params={"archived": False}).status_code == 200
    _as(client)
    assert "run-00" in {r["run_id"] for r in _all(client)["items"]}


def test_archiving_is_role_gated_and_audited(env):
    client, _ = env
    _as(client, "auditor")
    assert client.post("/api/runs/run-01/archive").status_code == 403

    _as(client, "investigator")
    client.post("/api/runs/run-01/archive")
    _as(client, "auditor")
    rows = client.get("/api/audit", params={"action": "run.archive",
                                            "limit": 100}).json()["items"]
    assert any(r["resource"] == "run-01" for r in rows)


# --------------------------------------------------------------------------
# the funnel
# --------------------------------------------------------------------------

@pytest.fixture()
def funnel_run(env):
    """A run whose suspects.json carries the engine's gate identities."""
    client, root = env
    run_id = "run-funnel"
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "stages": [
            {"stage": "attribution", "status": "ok",
             "warnings": ["AIS index: 43 vessels/1963 rows -> 27 vessels/1220 rows"]}]
    }), encoding="utf-8")
    (d / "suspects.json").write_text(json.dumps({
        "run_id": run_id, "source": "synthetic",
        "total_vessels_considered": 8,
        "suspects": [{"mmsi": 1, "total_score": 0.8},
                     {"mmsi": 2, "total_score": 0.5}],
        "filtered_out": [
            {"mmsi": 11, "reason": "…", "filter_reason": "outside origin region",
             "failed_gates": ["outside origin region"]},
            {"mmsi": 12, "reason": "…", "filter_reason": "outside origin region",
             "failed_gates": ["outside origin region", "outside time window"]},
            {"mmsi": 13, "reason": "…", "filter_reason": "outside time window",
             "failed_gates": ["outside time window"]},
            {"mmsi": 14, "reason": "…",
             "filter_reason": "course incompatible with slick axis",
             "failed_gates": ["course incompatible with slick axis"]},
            {"mmsi": 15, "reason": "…",
             "filter_reason": "course incompatible with slick axis",
             "failed_gates": ["course incompatible with slick axis"]},
            {"mmsi": 16, "reason": "…", "filter_reason": None, "failed_gates": []},
        ],
    }), encoding="utf-8")
    return client, run_id


def test_funnel_counts_each_gate(funnel_run):
    client, run_id = funnel_run
    _as(client)
    f = client.get(f"/api/runs/{run_id}/funnel").json()

    assert f["found"] == 43, "top of funnel read from the AIS index note"
    assert f["indexed"] == 8
    # 8 - 2 spatial = 6; - 1 remaining temporal (12 already gone) = 5; - 2 = 3
    assert f["after_spatial"] == 6
    assert f["after_temporal"] == 5
    assert f["after_trajectory"] == 3
    assert f["candidates"] == 2
    assert f["filtered"] == 6


def test_funnel_reports_each_gate_independently_of_order(funnel_run):
    """Vessel 12 fails two gates. A cumulative funnel can only attribute it to
    one, so the per-gate toll is published separately."""
    client, run_id = funnel_run
    _as(client)
    f = client.get(f"/api/runs/{run_id}/funnel").json()

    assert f["exclusive_by_gate"]["after_spatial"] == 2
    assert f["exclusive_by_gate"]["after_temporal"] == 2      # 12 and 13
    assert f["exclusive_by_gate"]["after_trajectory"] == 2
    assert f["gates_are_sequential"] is False
    assert "together" in f["note"]


def test_funnel_histogram_uses_gate_identities_not_prose(funnel_run):
    client, run_id = funnel_run
    _as(client)
    hist = client.get(f"/api/runs/{run_id}/funnel").json()["reasons_histogram"]
    assert hist["outside origin region"] == 2
    assert hist["outside time window"] == 1
    assert hist["course incompatible with slick axis"] == 2
    assert hist["unclassified"] == 1


def test_funnel_says_so_when_the_top_is_unknown(env):
    """A fabricated top-of-funnel would overstate the filtering the system did."""
    client, root = env
    run_id = "run-nofound"
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "suspects.json").write_text(json.dumps(
        {"suspects": [], "filtered_out": [], "total_vessels_considered": 0}),
        encoding="utf-8")

    _as(client)
    f = client.get(f"/api/runs/{run_id}/funnel").json()
    assert f["found"] is None
    assert "not recorded" in f["found_note"]


def test_funnel_404s_without_suspects(env):
    client, root = env
    (root / "runs" / "run-bare").mkdir(parents=True, exist_ok=True)
    _as(client)
    assert client.get("/api/runs/run-bare/funnel").status_code == 404


def test_normalise_carries_gate_identities():
    """The published artefact must keep the machine-readable gate, not only
    the sentence a human reads."""
    from backend.services.pipeline import normalise

    out = normalise.normalise_suspects(
        {"vessels": [
            {"mmsi": 9, "filtered": True,
             "filter_reason": "outside time window",
             "failed_gates": ["outside time window"],
             "reason": "Filtered out: present in the origin region, but 4 h outside."},
        ]},
        {"scene_id": "S1", "source": "real"}, run_id="r1")

    row = out["filtered_out"][0]
    assert row["filter_reason"] == "outside time window"
    assert row["failed_gates"] == ["outside time window"]
    assert row["reason"].startswith("Filtered out:")
