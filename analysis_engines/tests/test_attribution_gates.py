"""Engine C Phase 5: track loading and the three filtering gates.

Handbook §8's gate test lives here - "a vessel outside the time window is filtered with
the reason recorded" - alongside one test per designed vessel in the mock fleet.

The origin cloud is produced by actually running Engines A and B, so these tests cover
the real hand-off rather than a hand-written stand-in.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from engines.attribution import GateConfig, apply_gates, build_origin_context, load_vessels
from engines.attribution.gates import (
    REASON_SPATIAL,
    REASON_TEMPORAL,
    REASON_TRAJECTORY,
    axis_offset_deg,
    slick_axis_from_cloud,
)
from engines.characterise import characterise
from engines.common.errors import EngineError
from engines.drift import hindcast
from tests.fixtures.make_mask import build_scene
from tests.fixtures.make_metocean import build_metocean
from tests.fixtures.make_vessels import build_vessels
from engines.attribution.runner import DEFAULT_WEIGHTS_PATH

# Anchored to this file so the suite passes from any working directory.
# These paths used to be CWD-relative, which meant the tests only ran
# when pytest happened to be invoked from analysis_engines/.
MODULE_ROOT = Path(__file__).resolve().parents[1]


CULPRIT_MMSI = 419001234


@pytest.fixture(scope="module")
def scenario(tmp_path_factory) -> dict:
    """Engines A and B for real, then a vessel fleet planted on B's origin window."""
    work = tmp_path_factory.mktemp("attribution")
    scene = build_scene(work / "scene")
    met = build_metocean(work / "met")

    slick = work / "slick.geojson"
    assert characterise(
        scene["mask_path"], scene["scene_meta_path"], slick,
        config_path=str(MODULE_ROOT / "config" / "characterise.yaml"),
    )["ok"]

    cloud_path = work / "origin_cloud.geojson"
    assert hindcast(
        slick, cloud_path,
        currents_path=met["currents_strain"], wind_path=met["wind_uniform"],
        config_path=str(MODULE_ROOT / "config" / "drift.yaml"),
    )["ok"]

    document = json.loads(cloud_path.read_text(encoding="utf-8"))
    window = next(
        f for f in document["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    lon, lat = window["geometry"]["coordinates"]

    truth = build_vessels(
        work / "ais",
        origin_lon=lon, origin_lat=lat,
        window_start=window["properties"]["start_utc"],
        window_end=window["properties"]["end_utc"],
        slick_axis_deg=slick_axis_from_cloud(document),
    )

    config = yaml.safe_load(DEFAULT_WEIGHTS_PATH.read_text())
    return {
        "document": document,
        "truth": truth,
        "slick": slick,
        "gate_config": GateConfig.from_config(config.get("gates")),
        "work": work,
    }


@pytest.fixture(scope="module")
def gated(scenario) -> dict:
    context, warnings = build_origin_context(
        scenario["document"], scenario["gate_config"]
    )
    tracks, track_warnings = load_vessels(scenario["truth"]["vessels_path"])
    results = {
        track.mmsi: apply_gates(track, context, scenario["gate_config"])
        for track in tracks
    }
    return {
        "context": context,
        "tracks": {t.mmsi: t for t in tracks},
        "results": results,
        "warnings": warnings + track_warnings,
    }


# --------------------------------------------------------------------- tracks ------
def test_all_contract_columns_load_without_warnings(gated):
    assert gated["warnings"] == []
    assert len(gated["tracks"]) == 6


def test_timestamps_land_in_the_right_decade(gated, scenario):
    """Regression: parquet may store datetime64[us], not [ns].

    Dividing a microsecond view by 1e9 puts every fix in 1970, and every vessel then
    fails the temporal gate for a reason that has nothing to do with the vessel.
    """
    track = gated["tracks"][CULPRIT_MMSI]
    window_start = pd.Timestamp(scenario["truth"]["window_start_utc"]).timestamp()
    assert abs(track.times_s[0] - window_start) < 24 * 3600
    assert 1.4e9 < track.times_s[0] < 1.6e9          # somewhere in 2014-2020


def test_missing_required_column_is_MISSING_INPUT(scenario, tmp_path):
    frame = pd.read_parquet(scenario["truth"]["vessels_path"]).drop(columns=["lon"])
    broken = tmp_path / "no_lon.parquet"
    frame.to_parquet(broken, index=False)

    with pytest.raises(EngineError) as excinfo:
        load_vessels(broken)
    assert excinfo.value.error_class == "MISSING_INPUT"
    assert "lon" in str(excinfo.value.detail["missing"])


def test_missing_optional_column_degrades_with_a_warning(scenario, tmp_path):
    frame = pd.read_parquet(scenario["truth"]["vessels_path"]).drop(columns=["imo"])
    thinned = tmp_path / "no_imo.parquet"
    frame.to_parquet(thinned, index=False)

    tracks, warnings = load_vessels(thinned)
    assert len(tracks) == 6
    assert any("imo" in w for w in warnings)


def test_naive_timestamps_are_assumed_utc_with_a_warning(scenario, tmp_path):
    """Deliberately laxer than Engine A: a contract-typed column loses tz in parquet."""
    frame = pd.read_parquet(scenario["truth"]["vessels_path"])
    frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
    naive = tmp_path / "naive.parquet"
    frame.to_parquet(naive, index=False)

    tracks, warnings = load_vessels(naive)
    assert any("no timezone" in w for w in warnings)
    assert tracks[0].times_s[0] > 1.4e9


def test_duplicate_fixes_are_dropped(scenario, tmp_path):
    frame = pd.read_parquet(scenario["truth"]["vessels_path"])
    doubled = tmp_path / "dupes.parquet"
    pd.concat([frame, frame]).to_parquet(doubled, index=False)

    tracks, warnings = load_vessels(doubled)
    assert any("duplicate" in w for w in warnings)
    assert sum(t.n_fixes for t in tracks) == len(frame)


def test_empty_parquet_yields_no_tracks(scenario, tmp_path):
    frame = pd.read_parquet(scenario["truth"]["vessels_path"]).iloc[0:0]
    empty = tmp_path / "empty.parquet"
    frame.to_parquet(empty, index=False)
    tracks, _ = load_vessels(empty)
    assert tracks == []


# --------------------------------------------------------------- the slick axis ----
def test_axis_is_recovered_from_the_cloud_without_needing_slick_geojson(
    scenario, gated
):
    """Engine C's contract inputs exclude slick.geojson, so the axis is derived.

    The particles at timestep 0 are the seeded slick, so their ellipse orientation is
    the slick's major axis - it must agree with what Engine A measured.
    """
    engine_a = json.loads(Path(scenario["slick"]).read_text())["features"][0]
    measured = engine_a["properties"]["orientation_deg"]
    assert gated["context"].axis_source == "origin_cloud"
    assert gated["context"].axis_deg == pytest.approx(measured, abs=5.0)


def test_supplied_axis_overrides_the_derived_one(scenario):
    context, _ = build_origin_context(
        scenario["document"], scenario["gate_config"], slick_axis_deg=123.0
    )
    assert context.axis_source == "engine_a"
    assert context.axis_deg == 123.0


@pytest.mark.parametrize(
    "course, axis, expected",
    [(60, 60, 0), (240, 60, 0), (150, 60, 90), (105, 60, 45), (350, 10, 20)],
)
def test_axis_offset_folds_an_undirected_axis(course, axis, expected):
    """A vessel may run either way along the slick axis and still be compatible."""
    assert axis_offset_deg(course, axis) == pytest.approx(expected)


# ---------------------------------------------------------------------- gates ------
def test_culprit_passes_every_gate(gated):
    result = gated["results"][CULPRIT_MMSI]
    assert result.passed is True
    assert result.filter_reason is None
    assert result.failed == []


def test_vessel_outside_the_time_window_is_filtered_with_the_reason(gated):
    """The handbook §8 must-pass gate test."""
    result = gated["results"][419002222]          # MV EARLY BIRD, 12 h too early
    assert result.passed is False
    assert result.filter_reason == REASON_TEMPORAL
    assert result.metrics["fixes_in_region"] > 0          # it was there...
    assert result.metrics["fixes_in_region_and_window"] == 0   # ...but not then
    assert result.metrics["hours_outside_window"] > 1.0


def test_distant_vessel_is_filtered_on_the_spatial_gate(gated):
    result = gated["results"][419003333]          # MV FAR AWAY, 30 km off
    assert result.passed is False
    assert result.filter_reason == REASON_SPATIAL
    assert result.metrics["distance_to_region_km"] > 1.0


def test_crossing_vessel_is_filtered_on_the_trajectory_gate(gated):
    """Right place, right time, but running across the slick rather than along it."""
    result = gated["results"][419004444]          # MV CROSSCUT
    assert result.passed is False
    assert result.filter_reason == REASON_TRAJECTORY
    assert result.metrics["axis_offset_deg"] == pytest.approx(90.0, abs=5.0)


def test_ferry_passes_the_gates(gated):
    """Gates are not the ranking: an innocent vessel can legitimately pass them."""
    assert gated["results"][419005555].passed is True


def test_background_traffic_fails_more_than_one_gate(gated):
    result = gated["results"][419006666]          # FV NIGHT HAUL
    assert result.passed is False
    assert len(result.failed) >= 2
    assert result.filter_reason == result.failed[0]


def test_every_vessel_matches_its_designed_outcome(gated, scenario):
    """The fleet was built so each vessel exercises one gate; nothing may drift."""
    for vessel in scenario["truth"]["vessels"]:
        result = gated["results"][vessel["mmsi"]]
        should_pass = vessel["expectation"].startswith("passes")
        assert result.passed is should_pass, f"{vessel['name']}: {vessel['expectation']}"


def test_filter_reasons_are_ui_ready_phrases(gated):
    """The UI renders "filtered out: <reason>", so reasons stay short and plain."""
    for result in gated["results"].values():
        if result.filter_reason:
            assert result.filter_reason in {
                REASON_SPATIAL, REASON_TEMPORAL, REASON_TRAJECTORY
            }
            assert len(result.filter_reason) < 45


# ------------------------------------------------------------ degraded inputs ------
def test_origin_cloud_without_a_window_is_MISSING_INPUT(scenario):
    document = {
        "type": "FeatureCollection",
        "features": [
            f for f in scenario["document"]["features"]
            if f["properties"].get("kind") != "origin_window"
        ],
    }
    with pytest.raises(EngineError) as excinfo:
        build_origin_context(document, scenario["gate_config"])
    assert excinfo.value.error_class == "MISSING_INPUT"


def test_origin_cloud_without_ellipses_is_MISSING_INPUT(scenario):
    document = {
        "type": "FeatureCollection",
        "features": [
            f for f in scenario["document"]["features"]
            if f["properties"].get("kind") != "confidence_ellipse"
        ],
    }
    with pytest.raises(EngineError) as excinfo:
        build_origin_context(document, scenario["gate_config"])
    assert excinfo.value.error_class == "MISSING_INPUT"


def test_a_wider_buffer_lets_more_vessels_through(scenario, gated):
    """The gates are configurable, and loosening them must actually loosen them."""
    generous = GateConfig(
        spatial_buffer_km=60.0, temporal_buffer_min=24 * 60, max_axis_offset_deg=89.0
    )
    context, _ = build_origin_context(scenario["document"], generous)
    tracks, _ = load_vessels(scenario["truth"]["vessels_path"])
    passed = sum(1 for t in tracks if apply_gates(t, context, generous).passed)
    strict = sum(1 for r in gated["results"].values() if r.passed)
    assert passed > strict
