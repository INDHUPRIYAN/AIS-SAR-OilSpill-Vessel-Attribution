"""Cross-run vessel index and dossier.

The constraint that shapes this whole feature: `vessels.parquet` is a frozen
14-column contract, and `validate_vessels_df` rejects extra columns with
"extend the contract, don't smuggle". Name, IMO and call sign are therefore
carried BESIDE the contract file, lifted at the MarineCadastre ingest before
the projection drops them -- never added to it.

The tests below pin that boundary from both sides: the contract still has
exactly 14 columns and still refuses extras, and identity still reaches the
dossier. Plus the rule that matters most for a system that ranks suspects:
a name is never invented for a vessel that does not have one.
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
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ais_service"))

PASSWORD = "vessel-test-password"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("vessel_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'v.db').as_posix()}"
    os.environ["SECRET_KEY"] = "v" * 64
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
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        role=role, active=True))
        db.add(Investigation(id="inv-v", name="vessels"))
        for rid in ("run-a", "run-b"):
            db.add(Run(id=rid, investigation_id="inv-v", scene_id=f"S1A_{rid}",
                       status="complete"))
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


def _write_run(root, run_id, suspects, filtered=(), identities=None, source="synthetic"):
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "suspects.json").write_text(json.dumps({
        "run_id": run_id, "source": source,
        "suspects": suspects, "filtered_out": list(filtered),
    }), encoding="utf-8")
    if identities is not None:
        (d / "vessel_identities.json").write_text(json.dumps(identities),
                                                  encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def indexed(env):
    """Two runs sharing one MMSI; one real with identity, one synthetic."""
    client, root = env
    from backend.models.db import SessionLocal
    from backend.services import vessel_index

    a = _write_run(root, "run-a", source="real",
                   suspects=[{"mmsi": 111111111, "rank": 1, "total_score": 0.82,
                              "source": "real",
                              "evidence": {"ais_gap_minutes": 50}},
                             {"mmsi": 222222222, "rank": 2, "total_score": 0.41,
                              "source": "real"}],
                   filtered=[{"mmsi": 333333333,
                              "filter_reason": "outside time window",
                              "failed_gates": ["outside time window"]}],
                   identities={"111111111": {"vessel_name": "MV Example",
                                             "imo": "9876543",
                                             "call_sign": "A8BC3"}})
    b = _write_run(root, "run-b", source="synthetic",
                   suspects=[{"mmsi": 111111111, "rank": 3, "total_score": 0.30,
                              "source": "synthetic"}])

    with SessionLocal() as db:
        vessel_index.index_run(db, "run-a", a)
        vessel_index.index_run(db, "run-b", b)
    return client


# --------------------------------------------------------------------------
# the frozen contract stays frozen
# --------------------------------------------------------------------------

def test_vessel_columns_are_still_exactly_fourteen():
    from contracts.schemas.tabular import VESSEL_COLUMNS

    assert len(VESSEL_COLUMNS) == 14
    for forbidden in ("name", "vessel_name", "imo", "call_sign", "flag"):
        assert forbidden not in VESSEL_COLUMNS, \
            f"identity leaked into the frozen contract as '{forbidden}'"


def test_the_contract_still_refuses_extra_columns():
    """The guard that made a side channel necessary in the first place."""
    import pandas as pd

    from contracts.schemas.tabular import VESSEL_COLUMNS, validate_vessels_df

    df = pd.DataFrame({c: [] for c in VESSEL_COLUMNS})
    df["vessel_name"] = []
    with pytest.raises(ValueError) as exc:
        validate_vessels_df(df)
    assert "unexpected columns" in str(exc.value)


def test_ingest_lifts_identity_before_the_contract_drops_it():
    """`col_map` already carried name and IMO; call sign is added, and the
    identity table is taken from the frame BEFORE `to_contract` projects it."""
    import pandas as pd

    from ais.mc_ingest import MarineCadastreIngest

    df = pd.DataFrame({
        "mmsi": [111111111, 111111111, 222222222],
        "vessel_name": ["MV Example", "MV Example", None],
        "imo": ["9876543", "9876543", None],
        "call_sign": ["A8BC3", "A8BC3", "   "],
    })
    identities = MarineCadastreIngest.vessel_identities(df)

    assert identities[111111111] == {"vessel_name": "MV Example",
                                     "imo": "9876543", "call_sign": "A8BC3"}
    # A vessel the archive left blank gets no entry at all, so "we do not know"
    # and "it has none" stay different statements.
    assert 222222222 not in identities


def test_identity_survives_a_single_corrupt_row():
    """The archive repeats identity on every fix; one bad row must not rename
    a vessel."""
    import pandas as pd

    from ais.mc_ingest import MarineCadastreIngest

    df = pd.DataFrame({
        "mmsi": [1, 1, 1, 1],
        "vessel_name": ["MV Real", "MV Real", "MV Real", "GARBAGE"],
    })
    assert MarineCadastreIngest.vessel_identities(df)[1]["vessel_name"] == "MV Real"


def test_synthetic_ais_yields_no_identity():
    import pandas as pd

    from ais.mc_ingest import MarineCadastreIngest

    df = pd.DataFrame({"mmsi": [900000001], "lat": [1.0], "lon": [2.0]})
    assert MarineCadastreIngest.vessel_identities(df) == {}


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------

def test_dossier_shows_every_run_a_vessel_appeared_in(indexed):
    client = indexed
    _as(client)
    body = client.get("/api/vessels/111111111").json()

    assert body["appearances"] == 2
    assert {a["run_id"] for a in body["appearance_list"]} == {"run-a", "run-b"}
    assert body["ranked_in"] == 2 and body["filtered_in"] == 0


def test_identity_reaches_the_dossier_from_the_sidecar(indexed):
    client = indexed
    _as(client)
    body = client.get("/api/vessels/111111111").json()

    assert body["name"] == "MV Example"
    assert body["imo"] == "9876543"
    assert body["call_sign"] == "A8BC3"
    assert body["identity_available"] is True


def test_a_vessel_without_identity_is_null_never_invented(indexed):
    """The single worst thing this table could do is give a name to a vessel
    the system may go on to rank as a suspect."""
    client = indexed
    _as(client)
    body = client.get("/api/vessels/222222222").json()

    assert body["name"] is None
    assert body["imo"] is None
    assert body["call_sign"] is None
    assert body["identity_available"] is False


def test_exclusions_are_recorded_not_hidden(indexed):
    """A dossier of only the runs where a vessel scored well is a prosecution
    file, not a record."""
    client = indexed
    _as(client)
    body = client.get("/api/vessels/333333333").json()

    assert body["filtered_in"] == 1 and body["ranked_in"] == 0
    row = body["appearance_list"][0]
    assert row["filtered"] is True
    assert row["filter_reason"] == "outside time window"


def test_evidence_values_are_carried(indexed):
    client = indexed
    _as(client)
    body = client.get("/api/vessels/111111111").json()
    run_a = next(a for a in body["appearance_list"] if a["run_id"] == "run-a")
    assert run_a["ais_gap_minutes"] == 50
    assert run_a["rank"] == 1 and run_a["total_score"] == 0.82


def test_a_real_vessel_is_not_downgraded_by_a_later_synthetic_run(indexed):
    """The generator reuses MMSI ranges. Relabelling real evidence because a
    scenario borrowed a number would be a provenance error."""
    client = indexed
    _as(client)
    assert client.get("/api/vessels/111111111").json()["source"] == "real"


def test_reindexing_is_idempotent(env, indexed):
    """Running the backfill twice must not double an appearance count."""
    client, root = env
    from backend.models.db import SessionLocal
    from backend.services import vessel_index

    _as(client)
    before = client.get("/api/vessels/111111111").json()["appearances"]

    with SessionLocal() as db:
        vessel_index.index_run(db, "run-a", root / "runs" / "run-a")
        vessel_index.index_run(db, "run-b", root / "runs" / "run-b")

    assert client.get("/api/vessels/111111111").json()["appearances"] == before


def test_index_skips_a_run_with_no_suspects(env):
    client, root = env
    from backend.models.db import SessionLocal
    from backend.services import vessel_index

    (root / "runs" / "run-empty").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        result = vessel_index.index_run(db, "run-empty", root / "runs" / "run-empty")
    assert result["indexed"] == 0 and "no suspects.json" in result["reason"]


# --------------------------------------------------------------------------
# the routes
# --------------------------------------------------------------------------

def test_listing_filters_by_source(indexed):
    client = indexed
    _as(client)
    real = client.get("/api/vessels", params={"source": "real"}).json()
    assert real["total"] >= 1
    assert all(v["source"] == "real" for v in real["items"])


def test_search_matches_name_and_mmsi(indexed):
    client = indexed
    _as(client)
    by_name = client.get("/api/vessels", params={"q": "Example"}).json()
    assert 111111111 in {v["mmsi"] for v in by_name["items"]}

    # An MMSI is the only identifier a synthetic vessel has.
    by_mmsi = client.get("/api/vessels", params={"q": "222222222"}).json()
    assert 222222222 in {v["mmsi"] for v in by_mmsi["items"]}


def test_tracks_reference_the_sealed_artefact(indexed):
    """References, not copies -- a second copy could drift from the artefact
    it claims to represent."""
    client = indexed
    _as(client)
    body = client.get("/api/vessels/111111111/tracks").json()
    assert body["count"] == 2
    for t in body["tracks"]:
        assert t["vessels_geojson"] == f"/api/runs/{t['run_id']}/vessels_geojson"
        assert "available" in t


def test_unknown_vessel_is_404(indexed):
    client = indexed
    _as(client)
    assert client.get("/api/vessels/999999999").status_code == 404
    assert client.get("/api/vessels/999999999/tracks").status_code == 404


def test_dossier_requires_a_session(env):
    client, _ = env
    client.cookies.clear()
    assert client.get("/api/vessels/111111111").status_code == 401
