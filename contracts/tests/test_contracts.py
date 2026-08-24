"""
Contract test suite. Green here means: your file will not break integration.

Run from the repo root:   pytest contracts/tests -q

Every developer runs this against THEIR OWN output before handover:
    from contracts.schemas import CONTRACTS
    model, _ = CONTRACTS["slick"]
    model.model_validate_json(Path("my_output.geojson").read_text())
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import rasterio

ROOT = Path(__file__).resolve().parents[2]      # repo root (parent of contracts/)
sys.path.insert(0, str(ROOT))

from contracts.schemas import (  # noqa: E402
    CONTRACTS,
    DetectResponse,
    ForecastCollection,
    OriginCloud,
    ProviderStatusFile,
    SceneMeta,
    SlickCollection,
    SuspectsReport,
    validate_vessels_df,
)

MOCKS = ROOT / "contracts" / "mocks"


def load(name: str):
    return json.loads((MOCKS / name).read_text())


# ---------------------------------------------------------------------------
# every mock exists and validates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", [
    "scene_meta.json", "detect_response.json", "slick.geojson", "origin_cloud.geojson",
    "forecast.geojson", "suspects.json", "provider_status.json",
    "vessels.parquet", "scene_sigma0_db.tif", "raw_mask.tif",
])
def test_mock_exists(filename):
    assert (MOCKS / filename).exists(), f"missing mock: {filename} — run python contracts/make_mocks.py"


@pytest.mark.parametrize("key", list(CONTRACTS))
def test_mock_validates_against_schema(key):
    model, filename = CONTRACTS[key]
    model.model_validate_json((MOCKS / filename).read_text())


# ---------------------------------------------------------------------------
# cross-file invariants — the bugs that actually kill integrations
# ---------------------------------------------------------------------------


def test_scene_id_consistent_everywhere():
    scene_id = SceneMeta.model_validate(load("scene_meta.json")).scene_id
    assert DetectResponse.model_validate(load("detect_response.json")).scene_id == scene_id
    assert SlickCollection.model_validate(load("slick.geojson")).metadata.scene_id == scene_id
    assert OriginCloud.model_validate(load("origin_cloud.geojson")).metadata.scene_id == scene_id
    assert ForecastCollection.model_validate(load("forecast.geojson")).metadata.scene_id == scene_id
    assert SuspectsReport.model_validate(load("suspects.json")).scene_id == scene_id


def test_all_geometry_inside_scene_bbox():
    """A slick outside its own scene means a CRS or lon/lat bug upstream."""
    bbox = SceneMeta.model_validate(load("scene_meta.json")).bbox
    slick = SlickCollection.model_validate(load("slick.geojson"))
    for feat in slick.features:
        for ring in feat.geometry.coordinates:
            for lon, lat in ring:
                assert bbox[0] - 0.05 <= lon <= bbox[2] + 0.05, f"lon {lon} outside scene"
                assert bbox[1] - 0.05 <= lat <= bbox[3] + 0.05, f"lat {lat} outside scene"


def test_hindcast_runs_backwards_in_time():
    """Every particle timestamp must be at or before acquisition. A forward-drifting
    'hindcast' is the single easiest way to lose the attribution argument."""
    acquired = SlickCollection.model_validate(load("slick.geojson")).metadata.acquired_utc
    cloud = OriginCloud.model_validate(load("origin_cloud.geojson"))
    for feat in cloud.features:
        t = datetime.fromisoformat(feat.properties["t_utc"].replace("Z", "+00:00"))
        assert t <= acquired, "origin cloud contains a timestamp after acquisition"
    assert cloud.metadata.origin_window_start_utc < cloud.metadata.origin_window_end_utc
    assert cloud.metadata.origin_window_end_utc <= acquired


def test_forecast_runs_forwards_in_time():
    acquired = SlickCollection.model_validate(load("slick.geojson")).metadata.acquired_utc
    fc = ForecastCollection.model_validate(load("forecast.geojson"))
    for feat in fc.features:
        assert feat.properties.valid_utc > acquired
    assert sorted({f.properties.horizon_h for f in fc.features}) == [6, 12, 24]


def test_forecast_uncertainty_grows_with_horizon():
    fc = ForecastCollection.model_validate(load("forecast.geojson"))
    areas = {}
    for f in fc.features:
        if f.properties.confidence_level == 0.9:
            areas[f.properties.horizon_h] = f.properties.area_km2
    assert areas[6] < areas[12] < areas[24], "uncertainty must widen with time — honesty rule"


def test_all_timestamps_are_utc_z():
    """IST leaking into a timestamp shifts the origin window by 5.5 h and blames the wrong ship."""
    for name in ("scene_meta.json", "slick.geojson", "origin_cloud.geojson",
                 "forecast.geojson", "suspects.json", "provider_status.json"):
        text = (MOCKS / name).read_text()
        for token in ("+05:30", "+0530"):
            assert token not in text, f"{name} contains a local-time offset"


def test_crs_is_wgs84_everywhere():
    for name in ("scene_meta.json", "slick.geojson", "origin_cloud.geojson", "forecast.geojson"):
        assert load(name).get("crs", "EPSG:4326") == "EPSG:4326" or \
               load(name).get("metadata", {}).get("crs", "EPSG:4326") == "EPSG:4326"


# ---------------------------------------------------------------------------
# rasters
# ---------------------------------------------------------------------------


def test_scene_and_mask_share_the_same_grid():
    """Engine A overlays the mask on the scene — a grid mismatch silently corrupts geometry."""
    with rasterio.open(MOCKS / "scene_sigma0_db.tif") as scene, \
         rasterio.open(MOCKS / "raw_mask.tif") as mask:
        assert scene.crs == mask.crs == "EPSG:4326"
        assert scene.shape == mask.shape
        assert scene.transform == mask.transform


def test_mask_is_binary_and_non_empty():
    with rasterio.open(MOCKS / "raw_mask.tif") as src:
        arr = src.read(1)
    assert set(arr.flatten().tolist()) <= {0, 1}
    assert arr.sum() > 0, "mock mask has no slick pixels"


def test_scene_values_within_declared_db_range():
    meta = SceneMeta.model_validate(load("scene_meta.json"))
    with rasterio.open(MOCKS / "scene_sigma0_db.tif") as src:
        arr = src.read(1)
    assert arr.min() >= meta.db_range[0] - 1e-3
    assert arr.max() <= meta.db_range[1] + 1e-3


# ---------------------------------------------------------------------------
# vessels.parquet
# ---------------------------------------------------------------------------


def test_vessels_parquet_matches_contract():
    df = pd.read_parquet(MOCKS / "vessels.parquet")
    validate_vessels_df(df)


def test_vessels_have_one_synthetic_culprit():
    df = pd.read_parquet(MOCKS / "vessels.parquet")
    culprits = df.loc[df["culprit"], "mmsi"].unique()
    assert len(culprits) == 1, "the synthetic benchmark needs exactly one injected culprit"
    assert (df["source"] == "synthetic").all()


def test_vessel_track_times_cover_the_origin_window():
    df = pd.read_parquet(MOCKS / "vessels.parquet")
    cloud = OriginCloud.model_validate(load("origin_cloud.geojson"))
    assert df["timestamp_utc"].min() <= cloud.metadata.origin_window_start_utc
    assert df["timestamp_utc"].max() >= cloud.metadata.origin_window_end_utc


# ---------------------------------------------------------------------------
# suspects + monitoring
# ---------------------------------------------------------------------------


def test_top_suspect_is_the_injected_culprit():
    """The end-to-end sanity check for the whole project: the pipeline must blame the ship
    the synthetic generator actually blamed."""
    df = pd.read_parquet(MOCKS / "vessels.parquet")
    culprit = int(df.loc[df["culprit"], "mmsi"].iloc[0])
    report = SuspectsReport.model_validate(load("suspects.json"))
    assert report.suspects[0].mmsi == culprit


def test_suspect_scores_match_declared_weights():
    """The UI shows the weights; the totals must actually be those weights applied."""
    report = SuspectsReport.model_validate(load("suspects.json"))
    for s in report.suspects:
        expected = sum(report.weights[k] * getattr(s.sub_scores, k) for k in report.weights)
        assert abs(expected - s.total_score) < 1e-3, f"score for {s.mmsi} is not auditable"


def test_every_suspect_mmsi_exists_in_the_ais_file():
    df = pd.read_parquet(MOCKS / "vessels.parquet")
    known = set(df["mmsi"].astype(int).tolist())
    report = SuspectsReport.model_validate(load("suspects.json"))
    for s in report.suspects:
        assert s.mmsi in known, f"suspect {s.mmsi} is not in vessels.parquet"


def test_provider_status_fallback_is_coherent():
    status = ProviderStatusFile.model_validate(load("provider_status.json"))
    for p in status.providers:
        assert p.active_provider in p.chain
        if p.status.value == "FAILED":
            assert p.active_provider != p.provider, \
                "a FAILED primary must have handed over to a fallback"


# ---------------------------------------------------------------------------
# negative tests — the schema must REJECT bad data, not just accept good data
# ---------------------------------------------------------------------------


def test_schema_rejects_swapped_latlon():
    bad = load("slick.geojson")
    bad["features"][0]["properties"]["centroid"] = [13.05, 80.31]   # lat, lon — wrong order
    with pytest.raises(Exception):
        SlickCollection.model_validate(bad)


def test_schema_rejects_naive_timestamp():
    bad = load("scene_meta.json")
    bad["acquired_utc"] = "2017-02-02T00:39:42"                     # no Z
    with pytest.raises(Exception):
        SceneMeta.model_validate(bad)


def test_schema_rejects_non_wgs84_crs():
    bad = load("scene_meta.json")
    bad["crs"] = "EPSG:32644"
    with pytest.raises(Exception):
        SceneMeta.model_validate(bad)


def test_schema_rejects_unknown_field():
    bad = load("scene_meta.json")
    bad["secret_extra"] = 1
    with pytest.raises(Exception):
        SceneMeta.model_validate(bad)


def test_schema_rejects_unranked_suspects():
    bad = load("suspects.json")
    bad["suspects"][0]["total_score"] = 0.01                        # now out of order
    with pytest.raises(Exception):
        SuspectsReport.model_validate(bad)


def test_schema_rejects_weights_that_do_not_sum_to_one():
    bad = load("suspects.json")
    bad["weights"]["proximity"] = 0.9
    with pytest.raises(Exception):
        SuspectsReport.model_validate(bad)


def test_vessel_validator_rejects_missing_column():
    df = pd.read_parquet(MOCKS / "vessels.parquet").drop(columns=["sog_kn"])
    with pytest.raises(ValueError):
        validate_vessels_df(df)


def test_vessel_validator_rejects_culprit_flag_on_real_data():
    df = pd.read_parquet(MOCKS / "vessels.parquet").copy()
    df["source"] = "real"
    with pytest.raises(ValueError):
        validate_vessels_df(df)


def test_empty_detection_is_valid():
    """No slick in the scene is a normal outcome, not an error."""
    DetectResponse.model_validate({
        "scene_id": "S1A_EMPTY", "mask_path": "x.tif", "confidence": 0.0,
        "candidates": [], "model_version": "mock-v0", "engine": "threshold_fallback",
    })
