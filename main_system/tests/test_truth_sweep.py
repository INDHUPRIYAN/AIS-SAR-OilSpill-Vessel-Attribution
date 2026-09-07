"""Four small defects that each let a run misrepresent itself.

PC-11  a static mock provider board was copied into every run directory, so a
       sealed run carried a fictional provider snapshot beside real artefacts;
X-05   `?lite=true` compared whole feature dicts to split one list into two,
       making the "fast" path ~20x slower than the full file it replaced;
S-04/5 the weight profile had no read API and no integrity statement;
X-02   the manifest recorded artefacts but not the code, models or weights
       that produced them -- so a sealed run proved only that its files were
       unmodified, not what generated them.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture(scope="module")
def client():
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# S-04 / S-05 — weight profile
# --------------------------------------------------------------------------

def test_weights_endpoint_reports_the_file_as_written(client):
    r = client.get("/api/attribution/weights")
    assert r.status_code == 200
    body = r.json()

    assert body["source"].endswith("analysis_engines/config/attribution_weights.yaml")
    assert body["validated"] is True
    assert body["problems"] == []
    assert abs(body["sum"] - 1.0) <= 1e-6
    assert set(body["weights"]) == {
        "proximity", "temporal", "trajectory", "anomaly", "ais_gap", "prior"}
    assert body["weights"]["proximity"] == 0.30


def test_only_the_weights_block_is_summed():
    """The same YAML carries gates, priors and scoring thresholds. Summing the
    document instead of the block would never validate."""
    import yaml

    doc = yaml.safe_load(
        (REPO_ROOT / "analysis_engines" / "config" / "attribution_weights.yaml")
        .read_text(encoding="utf-8"))
    assert {"gates", "priors", "scoring"} <= set(doc), "fixture assumption"
    assert abs(sum(doc["weights"].values()) - 1.0) <= 1e-6
    everything = sum(v for v in doc["priors"].values() if isinstance(v, (int, float)))
    assert abs(everything - 1.0) > 1e-6, "summing more than `weights:` must not accidentally pass"


def test_profile_hash_is_stable_and_covers_only_weights():
    from backend.api.routes import weights_profile

    a = weights_profile()
    b = weights_profile()
    assert a["profile_hash"] == b["profile_hash"], "hash must not drift between reads"
    assert len(a["profile_hash"]) == 64


def test_published_factor_aliases_are_declared(client):
    """suspects.json renames two factors; the mapping must be stated, not guessed."""
    body = client.get("/api/attribution/weights").json()
    assert body["published_as"] == {"anomaly": "behaviour", "prior": "vessel_prior"}


# --------------------------------------------------------------------------
# X-05 — lite origin
# --------------------------------------------------------------------------

def test_lite_origin_partitions_without_quadratic_scan():
    """The defect was `f not in parts`, an O(n*m) whole-dict comparison. This
    would take minutes at 40k features if it came back."""
    from backend.api.routes import _lite_origin

    feats = [{"type": "Feature", "geometry": {},
              "properties": {"feature_type": "particle", "i": i}}
             for i in range(40_000)]
    feats += [{"type": "Feature", "geometry": {},
               "properties": {"feature_type": "ellipse", "step_index": s}}
              for s in range(25)]

    start = time.perf_counter()
    out = _lite_origin({"features": feats, "metadata": {}}, max_particles=1800)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"subsampling took {elapsed:.2f}s — the quadratic scan is back"
    kinds = [f["properties"]["feature_type"] for f in out["features"]]
    assert kinds.count("ellipse") == 25, "every ellipse is kept"
    # Stride subsampling lands near the target rather than exactly on it
    # (stride = n // max, so the count is ceil(n / stride)). The point is the
    # order-of-magnitude reduction, not a hard cap.
    assert 1800 <= kinds.count("particle") <= 1900
    assert out["metadata"]["lite_subsampled"] is True
    assert out["metadata"]["particles_full"] == 40_000


def test_lite_origin_is_a_noop_below_the_threshold():
    from backend.api.routes import _lite_origin

    payload = {"features": [{"properties": {"feature_type": "particle"}}], "metadata": {}}
    assert _lite_origin(payload, max_particles=1800) is payload


# --------------------------------------------------------------------------
# X-02 — manifest v2
# --------------------------------------------------------------------------

def test_manifest_records_the_code_that_ran():
    from backend.services.pipeline.run import _git_sha

    sha = _git_sha()
    assert sha, "a manifest with no code identity cannot be traced to a fix"
    assert sha == "unknown" or len(sha.split("+")[0]) == 40
    if sha != "unknown":
        assert all(c in "0123456789abcdef" for c in sha.split("+")[0])


def test_manifest_records_the_deployed_models():
    """Identity comes from the ONNX metadata, so a re-export cannot leave the
    manifest naming the previous checkpoint."""
    from backend.services.pipeline.run import _model_records

    records = {r["kind"]: r for r in _model_records()}
    if not records:
        pytest.skip("weights not present in this checkout")

    assert records["segment"]["name"] == "unet-r34-fullcorpus-e48"
    assert records["segment"]["sha256"].startswith("a4aac81f")
    assert records["screen"]["name"].startswith("yolo11n")
    assert records["screen"]["sha256"].startswith("9a6ff8df")
    for r in records.values():
        assert r["bytes"] > 0 and len(r["sha256"]) == 64


def test_manifest_records_the_weight_profile():
    from backend.services.pipeline.run import _weights_profile_stamp

    stamp = _weights_profile_stamp()
    assert stamp and stamp["validated"] is True
    assert abs(stamp["sum"] - 1.0) <= 1e-6
    assert len(stamp["profile_hash"]) == 64


def test_run_stamp_matches_the_api(client):
    """The manifest and the endpoint must describe the same profile, or a
    report citing one would disagree with the UI showing the other."""
    from backend.services.pipeline.run import _weights_profile_stamp

    assert (client.get("/api/attribution/weights").json()["profile_hash"]
            == _weights_profile_stamp()["profile_hash"])


# --------------------------------------------------------------------------
# PC-11 — provider snapshot
# --------------------------------------------------------------------------

def test_provider_snapshot_is_measured_not_mocked():
    from backend.services.pipeline.run import _run_provider_snapshot

    class _Stage:
        def __init__(self, status, ds):
            self.status, self.data_source = status, ds

    by_name = {"detect": _Stage("ok", "sensor"), "attribution": _Stage("ok", "real")}
    snap = _run_provider_snapshot(
        {"provider_used": "LocalCache", "source": "real", "scene_id": "S1"},
        None, None, None, by_name)

    assert snap["owner"] == "measured"
    assert snap["scene"]["provider"] == "LocalCache"
    assert snap["scene"]["data_source"] == "sensor"
    # Absent forcing stays absent rather than being reported as a healthy provider.
    assert snap.get("currents") is None and snap.get("wind") is None


def test_no_run_directory_receives_the_mock_board():
    """The mock file must no longer be the source for any newly sealed run."""
    src = REPO_ROOT / "contracts" / "mocks" / "provider_status.json"
    run = REPO_ROOT / "dev_evidence" / "P05" / "p05verify_provider_status.json"
    if not run.exists():
        pytest.skip("verification snapshot not present")

    written = json.loads(run.read_text(encoding="utf-8"))
    assert written.get("owner") == "measured"
    if src.exists():
        assert written != json.loads(src.read_text(encoding="utf-8"))
