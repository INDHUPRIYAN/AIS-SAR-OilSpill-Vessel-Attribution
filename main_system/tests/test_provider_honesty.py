"""Provider honesty, the data catalogue and the model registry.

PROMPT 17. Every test here defends one distinction that a dashboard tends to
erase.

**REACHABLE is not WORKING.** An unauthenticated GET returning 200 proves a
host answered. It does not prove our credentials are accepted, that the dataset
exists, or that a download would succeed. Reporting it as WORKING is how a
board shows all-green while every fetch fails on authentication -- which is
exactly what nearly happened with ERA5 during the flagship run: the host was
intermittently unreachable, the credentials were never exercised, and only a
functional probe could tell the two apart.

**"No credentials needed" is not "credentials configured".** The two-state
version rendered a green tick for HYCOM, Open-Meteo and MarineCadastre, which
take no credentials at all. A tick claims a key is present and correct; that is
not a claim anyone can make about a field that does not exist.

**Not deployed is not missing and not broken.** Sentinel-2 and live AIS have
adapters and no pipeline wiring. Omitting them suggests they were never
considered; showing them beside working providers suggests they are available.

**A tile for a component we do not run is a claim, not a placeholder.**
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture(scope="module")
def client(sign_in_helper, tmp_path_factory):
    root = tmp_path_factory.mktemp("catalog")
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'c.db').as_posix()}"
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        yield c
    os.environ.pop("DATABASE_URL", None)


# --------------------------------------------------------------------------
# the status vocabulary
# --------------------------------------------------------------------------

def test_an_unauthenticated_200_is_reachable_not_working(monkeypatch, tmp_path):
    """The defect this closes: a front page that loads reading as a healthy
    data source."""
    import requests

    from backend.models.db import ApiProvider, SessionLocal, init_db
    from backend.services.providers import health

    class _Resp:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(health, "FUNCTIONAL_PROBES", {})

    init_db()
    with SessionLocal() as db:
        row = db.get(ApiProvider, "HYCOM")
        assert row is not None
        result = health.probe(db, "HYCOM")

    assert result["status"] == "REACHABLE", \
        "an unauthenticated 200 was reported as WORKING"
    assert "does not prove" in result["detail"]


def test_a_functional_probe_earns_working(monkeypatch):
    """WORKING is reserved for asking the provider for something real."""
    import requests

    from backend.models.db import SessionLocal, init_db
    from backend.services.providers import health

    class _Resp:
        status_code = 200

    monkeypatch.setattr(requests, "head", lambda *a, **k: _Resp())
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())

    init_db()
    with SessionLocal() as db:
        result = health.probe(db, "MarineCadastre")

    assert result["status"] == "WORKING"
    assert result["probe"] == "functional"
    assert "downloadable" in result["detail"]


def test_a_functional_probe_that_is_refused_is_degraded(monkeypatch):
    """Reachable and refusing us is not an outage and not health."""
    import requests

    from backend.models.db import SessionLocal, init_db
    from backend.services.providers import health

    class _Resp:
        status_code = 403

    monkeypatch.setattr(requests, "head", lambda *a, **k: _Resp())
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())

    init_db()
    with SessionLocal() as db:
        result = health.probe(db, "MarineCadastre")
    assert result["status"] == "DEGRADED"
    assert "unauthorised" in result["detail"]


# --------------------------------------------------------------------------
# credentials, tri-state
# --------------------------------------------------------------------------

def test_a_keyless_provider_reads_n_a_not_configured():
    from backend.models.db import SessionLocal, init_db
    from backend.services.providers import health

    init_db()
    with SessionLocal() as db:
        for keyless in ("HYCOM", "OpenMeteo", "MarineCadastre", "DMA"):
            assert health.credential_state(db, keyless) == "n_a", \
                f"{keyless} takes no credentials but does not report n_a"


def test_a_provider_needing_keys_reports_configured_or_missing():
    from backend.models.db import SessionLocal, init_db
    from backend.services.providers import health

    init_db()
    with SessionLocal() as db:
        for gated in ("CDSE", "CMEMS", "ERA5"):
            assert health.credential_state(db, gated) in ("configured", "missing")


def test_the_aisstream_key_field_is_gone():
    """Nothing reads it. Offering the field invited operators to configure a
    capability that does not exist."""
    from backend.core.config import Settings
    from backend.services.providers.health import CREDENTIAL_ALTERNATIVES

    assert "AISStream" not in CREDENTIAL_ALTERNATIVES
    assert Settings().env_credentials("AISStream") == {}


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------

def test_catalog_lists_every_provider_with_coverage(client):
    body = client.get("/api/catalog").json()
    names = {p["name"] for p in body["providers"]}
    assert {"CDSE", "CMEMS", "ERA5", "MarineCadastre"} <= names

    for provider in body["providers"]:
        assert provider["coverage"] is not None, \
            f"{provider['name']} has no coverage metadata"
        assert "dataset" in provider["coverage"]


def test_catalog_declares_what_not_deployed_means(client):
    body = client.get("/api/catalog").json()
    by_name = {p["name"]: p for p in body["providers"]}

    for name in ("Sentinel2", "AISStream"):
        assert by_name[name]["deployment"] == "NOT_DEPLOYED"
        assert by_name[name]["status"] == "NOT_DEPLOYED"
        # "not deployed" and "does not exist" are different statements, and the
        # difference is the roadmap.
        assert by_name[name]["reason"], f"{name} says NOT_DEPLOYED without a reason"

    assert "adapter" in by_name["Sentinel2"]["reason"].lower()


def test_catalog_says_how_each_status_was_measured(client):
    """A status without its probe kind is unfalsifiable."""
    body = client.get("/api/catalog").json()
    for provider in body["providers"]:
        assert provider["probe"] in ("functional", "reachability", "none")
        if provider["probe"] == "functional":
            assert provider["probe_proves"], \
                f"{provider['name']} claims a functional probe without saying what it proves"


def test_the_vocabulary_is_published_with_the_data(client):
    """The UI must not have to invent a meaning for REACHABLE."""
    body = client.get("/api/catalog").json()
    assert "REACHABLE" in body["vocabulary"]
    assert "does not prove" in body["vocabulary"]["REACHABLE"]
    assert body["credentials_vocabulary"]["n_a"].startswith(
        "this provider takes no credentials")


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------

def test_models_reports_the_deployed_pair_with_hashes(client):
    body = client.get("/api/models").json()
    by_kind = {m["kind"]: m for m in body["models"]}

    if by_kind["segment"]["status"] == "MISSING":
        pytest.skip("weights not present in this checkout")

    assert by_kind["segment"]["name"] == "unet-r34-fullcorpus-e48"
    assert by_kind["screen"]["name"].startswith("yolo11n")
    for kind in ("segment", "screen"):
        assert len(by_kind[kind]["sha256"]) == 64
        assert by_kind[kind]["bytes"] > 0


def test_the_drift_residual_is_listed_experimental_and_disabled(client):
    """It was evaluated, it did not help, and it is off. Omitting it hides
    work; listing it without the verdict implies it is in use."""
    body = client.get("/api/models").json()
    residual = next(m for m in body["models"] if m["kind"] == "drift_residual")

    assert residual["status"] == "EXPERIMENTAL"
    assert residual["applied"] is False
    assert "evaluated negative" in residual["note"]
    assert "no machine learning contributes to the origin" in residual["detail"].lower()


def test_no_model_is_named_yolov8(client):
    """The deployed screen is YOLO11n. The wrong name in a registry is how a
    report cites a model that was never run."""
    import json as _json

    body = client.get("/api/models").json()
    assert "yolov8" not in _json.dumps(body).lower()


# --------------------------------------------------------------------------
# host health
# --------------------------------------------------------------------------

def test_system_health_fields_trace_to_measurements(client):
    body = client.get("/api/system/health").json()

    assert body["database"]["ok"] is True
    assert "runs" in body["database"]
    assert body["disk"]["free_gb"] > 0
    assert isinstance(body["providers"], dict)

    host = body["host"]
    if host.get("measured_by") == "psutil":
        assert 0 <= host["cpu_percent"] <= 100
        assert host["memory_total_mb"] > 0
    else:
        # Absent, not zero: a 0% CPU reading would be a measurement we did not
        # make.
        assert "cpu_percent" not in host
        assert "not installed" in host["note"]


def test_system_health_has_no_tiles_for_components_we_do_not_run(client):
    """A tile for a queue this system does not have is a claim, not a
    placeholder."""
    body = client.get("/api/system/health").json()

    for absent in ("queue", "broker", "cluster", "storage_backend"):
        assert absent not in body, f"/api/system/health invents a '{absent}' tile"

    listed = " ".join(body["not_reported"]).lower()
    for component in ("queue", "broker", "cluster"):
        assert component in listed


def test_system_health_reports_the_model_files(client):
    body = client.get("/api/system/health").json()
    files = {m["file"]: m for m in body["models"]}
    assert {"segment.onnx", "screen.onnx"} == set(files)
    for entry in files.values():
        assert isinstance(entry["present"], bool)
        if entry["present"]:
            assert entry["bytes"] > 0


def test_the_keys_page_cannot_offer_an_aisstream_field(client):
    """The Keys page derives its fields from CREDENTIAL_FIELDS. Removing the
    provider from the alternatives is what removes the field -- this pins that
    the two stay connected rather than the UI keeping its own list."""
    from backend.services.providers.health import CREDENTIAL_FIELDS

    assert "AISStream" not in CREDENTIAL_FIELDS

    listing = client.get("/api/keys")
    if listing.status_code != 200:
        pytest.skip("keys endpoint not reachable for this role")
    payload = listing.json()
    names = {row.get("provider") for row in
             (payload if isinstance(payload, list) else payload.get("providers", []))}
    assert "AISStream" not in names


def test_the_credential_checker_says_not_deployed_rather_than_optional():
    """"Optional" implies configuring it would enable something."""
    import backend.verify_credentials as vc

    status, message = vc.check_aisstream()
    assert "NOT DEPLOYED" in message.upper()
    assert "optional" not in message.lower()
    labels = [name for name, _ in vc.CHECKS]
    assert any("NOT DEPLOYED" in label for label in labels)
