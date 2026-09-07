"""The sealed flagship run must be able to prove it used real data.

PROMPT 14's acceptance criterion: *"the flagship run's manifest asserts
attribution `data_source != "synthetic"` and `suspects.source == "real"`."*

This reads the run that `dev_evidence/P14/flagship.json` points at and checks
the claims that artefact makes about itself. It is deliberately a check on the
recorded run rather than on a fresh pipeline invocation: the flagship is a
one-off artefact produced from a 1.2 GB scene and ~660 MB of AIS archives, and
re-running it inside a unit test is neither possible nor the point. Every test
here skips when the run is not in the checkout, so the suite stays green on a
clean clone.

What it deliberately does NOT assert: that a culprit was found, or that the top
score is high. D1 is explicit that an honest run finding no strong suspect is a
valid result, and a test demanding one would be exactly the pressure to tune
gates that the standing rules forbid.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

POINTER = REPO_ROOT / "dev_evidence" / "P14" / "flagship.json"


def _run_dir() -> Path:
    if not POINTER.exists():
        pytest.skip("no flagship pointer in this checkout")
    run_id = json.loads(POINTER.read_text(encoding="utf-8"))["run_id"]
    run_dir = REPO_ROOT / "data" / "runs" / run_id
    if not (run_dir / "manifest.json").exists():
        pytest.skip(f"flagship run {run_id} not present in this checkout")
    return run_dir


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((_run_dir() / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def suspects() -> dict:
    return json.loads((_run_dir() / "suspects.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the AIS claim
# --------------------------------------------------------------------------

def test_the_manifest_says_which_ais_path_ran(manifest):
    """Audit A-05/06: a run that records only a filename cannot distinguish
    real AIS from a fleet synthesised around its own origin."""
    ais = manifest.get("ais")
    assert ais, "the manifest carries no AIS decision block"
    assert ais["selection"] == "real", \
        f"the flagship ran on {ais['selection']} AIS, not real"
    assert ais["data_source"] in ("real", "mixed")
    assert ais["covers_origin"] is True
    assert ais["considered"], "no candidate ledger: the choice is unfalsifiable"


def test_attribution_did_not_run_on_synthetic_data(manifest):
    stages = {s["name"]: s for s in manifest["stages"]}
    attribution = stages["attribution"]
    assert attribution["data_source"] != "synthetic", \
        "the flagship's attribution stage ran on synthetic vessels"


def test_suspects_declare_real_provenance(suspects):
    assert suspects["source"] == "real"
    for suspect in suspects.get("suspects", []):
        assert suspect.get("source") != "synthetic", \
            f"MMSI {suspect.get('mmsi')} is a synthesised vessel"


def test_no_culprit_was_planted(manifest):
    """Synthesis plants a culprit at the computed origin. Real AIS must not
    have one beside it, or the ranking would be scoring an invented vessel."""
    run_dir = _run_dir()
    for candidate in (run_dir / "engine_native" / "culprit.json",
                      run_dir / "engine" / "culprit.json"):
        assert not candidate.exists(), f"{candidate.name} exists beside real AIS"
    vessels = run_dir / "vessels.parquet"
    if vessels.exists():
        import pandas as pd

        df = pd.read_parquet(vessels, columns=["source", "culprit"])
        assert set(df["source"].str.lower().unique()) == {"real"}
        assert not df["culprit"].any(), "a row is flagged culprit in real AIS"


# --------------------------------------------------------------------------
# the run is sealed and verifiable
# --------------------------------------------------------------------------

def test_the_run_verifies_against_its_own_manifest():
    """§12: the artefacts must still hash to what was sealed."""
    from backend.services.pipeline import provenance

    report = provenance.verify_run(_run_dir())
    assert report["ok"], f"verify failed: {report.get('problems')}"
    assert report["checked"] > 0


def test_the_manifest_names_the_code_and_models_that_ran(manifest):
    assert manifest.get("code_git_sha")
    models = {m["kind"]: m for m in manifest.get("models", [])}
    assert models, "a flagship with no model identity cannot be reproduced"
    assert models["segment"]["name"] == "unet-r34-fullcorpus-e48"
    assert models["screen"]["name"].startswith("yolo11n")


def test_the_scene_is_real_sentinel1_not_a_mock(manifest):
    stages = {s["name"]: s for s in manifest["stages"]}
    assert stages["detect"]["data_source"] == "real"
    assert manifest["scene_id"].startswith("S1")


# --------------------------------------------------------------------------
# drift: the origin cloud is what attribution is scored against
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def origin_cloud() -> dict:
    path = _run_dir() / "origin_cloud.geojson"
    if not path.exists():
        pytest.fail("the flagship has no origin cloud: drift did not run")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_hindcast_actually_ran(manifest):
    stages = {s["name"]: s for s in manifest["stages"]}
    hindcast = stages["drift_hindcast"]
    assert hindcast["status"] in ("ok", "fallback"), \
        f"drift_hindcast is {hindcast['status']}: {hindcast.get('detail')}"
    assert hindcast["data_source"] != "synthetic"


def test_the_origin_window_is_stated(origin_cloud):
    """Attribution asks 'was this vessel here, then'. Without a window the
    second half of that question has no answer."""
    md = origin_cloud["metadata"]
    assert md.get("origin_window_start_utc"), "no origin window start"
    assert md.get("origin_window_end_utc"), "no origin window end"
    from datetime import datetime

    start = datetime.fromisoformat(md["origin_window_start_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(md["origin_window_end_utc"].replace("Z", "+00:00"))
    assert start <= end


def test_uncertainty_is_measured_not_zeroed(origin_cloud):
    """A cloud whose ellipses are all zero-width claims the origin is known
    exactly. Backward drift under diffusion never justifies that, and P04
    made `_require_axes` raise rather than zero-fill for this reason."""
    ellipses = [f for f in origin_cloud["features"]
                if f["properties"].get("feature_type") == "ellipse"]
    assert ellipses, "the cloud carries no uncertainty ellipses"
    axes = [f["properties"].get("semi_major_m") or 0.0 for f in ellipses]
    assert all(a is not None for a in axes)
    # Step 0 is the release point and is legitimately a point; every cloud
    # after it has spread.
    assert max(axes) > 0.0, "every uncertainty ellipse is zero-width"
    assert sum(1 for a in axes if a > 0.0) >= len(axes) - 1


def test_the_forcing_is_named_and_real(origin_cloud):
    """`forcing` records what actually drove the particles. A mock or absent
    provider here would make the origin -- and everything ranked against it --
    a statement about nothing."""
    forcing = origin_cloud["metadata"].get("forcing") or {}
    assert forcing, "the cloud does not say what forced it"
    assert forcing.get("engine"), "no drift engine recorded"

    named = [forcing.get("currents"), forcing.get("wind")]
    present = [f for f in named if f]
    assert present, "neither currents nor wind were recorded"
    for block in present:
        text = json.dumps(block).lower()
        assert "mock" not in text and "synthetic" not in text, \
            f"the flagship drifted on non-real forcing: {block}"


def test_the_forecast_artefact_exists(manifest):
    stages = {s["name"]: s for s in manifest["stages"]}
    assert stages["drift_forecast"]["status"] in ("ok", "fallback"), \
        f"drift_forecast is {stages['drift_forecast']['status']}"
    assert (_run_dir() / "forecast.geojson").exists()
