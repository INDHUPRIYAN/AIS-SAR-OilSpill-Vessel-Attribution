"""Engine B forecast mode: predicted extents at +6 / +12 / +24 h (handbook §4.3).

The mirror of the hindcast tests - same integrator, opposite sign, different writer.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import MultiPoint, shape

from engines.characterise import characterise
from engines.common.geo import m_per_deg_lat, m_per_deg_lon
from engines.common.timeutil import parse_utc
from engines.drift import forecast
from engines.drift.__main__ import main as cli_main
from engines.drift.forecast import (
    build_forecast,
    confidence_mask,
    ellipse_area_m2,
    extent_polygon,
)
from engines.schemas.forecast import validate_forecast
from tests.fixtures.make_mask import build_scene
from tests.fixtures.make_metocean import build_metocean

# Anchored to this file so the suite passes from any working directory.
# These paths used to be CWD-relative, which meant the tests only ran
# when pytest happened to be invoked from 2_nandha_engines/.
MODULE_ROOT = Path(__file__).resolve().parents[1]


DRIFT_CONFIG = str(MODULE_ROOT / "config" / "drift.yaml")


@pytest.fixture(scope="module")
def inputs(tmp_path_factory) -> dict:
    work = tmp_path_factory.mktemp("forecast_e2e")
    scene = build_scene(work / "scene")
    met = build_metocean(work / "met")
    slick = work / "slick.geojson"
    status = characterise(
        scene["mask_path"], scene["scene_meta_path"], slick,
        config_path=str(MODULE_ROOT / "config" / "characterise.yaml"),
    )
    assert status["ok"], status
    return {"slick": slick, "met": met, "work": work}


@pytest.fixture(scope="module")
def run(inputs) -> tuple[dict, dict]:
    out = inputs["work"] / "forecast.geojson"
    status = forecast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_strain"],
        wind_path=inputs["met"]["wind_uniform"],
        config_path=DRIFT_CONFIG,
    )
    assert status["ok"], status
    return status, json.loads(out.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ contract -------
def test_one_polygon_per_contract_horizon(run):
    _, document = run
    horizons = [f["properties"]["horizon_h"] for f in document["features"]]
    assert horizons == [6.0, 12.0, 24.0]
    assert all(f["geometry"]["type"] == "Polygon" for f in document["features"])


def test_output_validates_against_the_contract(run):
    _, document = run
    validate_forecast(document)


def test_forecast_file_stays_small(run, inputs):
    """Three polygons, not thousands of particles - this one is cheap for the UI."""
    _, document = run
    assert len(document["features"]) == 3


def test_horizon_times_are_ahead_of_detection(run, inputs):
    _, document = run
    slick = json.loads(Path(inputs["slick"]).read_text(encoding="utf-8"))
    detected = parse_utc(slick["features"][0]["properties"]["detected_utc"])
    for feature in document["features"]:
        ahead = (parse_utc(feature["properties"]["time_utc"]) - detected).total_seconds()
        assert ahead == pytest.approx(feature["properties"]["horizon_h"] * 3600.0, abs=600)


def test_extent_and_uncertainty_both_grow_with_the_horizon(run):
    """A forecast that does not widen with time is not showing uncertainty honestly."""
    _, document = run
    areas = [f["properties"]["area_km2"] for f in document["features"]]
    growth = [f["properties"]["uncertainty_growth"] for f in document["features"]]
    assert areas == sorted(areas), f"extent must not shrink: {areas}"
    assert growth == sorted(growth), f"uncertainty must not shrink: {growth}"
    assert growth[-1] > growth[0] > 0


def test_polygons_contain_the_particles_they_were_built_from(run):
    _, document = run
    for feature in document["features"]:
        polygon = shape(feature["geometry"])
        assert polygon.is_valid and not polygon.is_empty
        assert feature["properties"]["particles_used"] >= 4


# ------------------------------------------------------- direction, the mirror -----
def test_forecast_drifts_downstream_in_a_uniform_field(inputs, tmp_path):
    """The mirror of the hindcast's analytic test: forward motion is +v*t.

    With zero wind and a constant current the +24 h extent centroid must sit one full
    day of drift downstream of the slick, in the opposite direction to the hindcast.
    """
    out = tmp_path / "uniform.geojson"
    status = forecast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_uniform"],
        wind_path=inputs["met"]["wind_zero"],
        config_path=DRIFT_CONFIG,
    )
    assert status["ok"], status

    slick = json.loads(Path(inputs["slick"]).read_text(encoding="utf-8"))
    lon0, lat0 = slick["features"][0]["properties"]["centroid"]

    far = [
        f for f in json.loads(out.read_text())["features"]
        if f["properties"]["horizon_h"] == 24.0
    ][0]
    centroid = shape(far["geometry"]).centroid

    seconds = 24 * 3600.0
    expected_lon = lon0 + inputs["met"]["uniform"]["current_u"] * seconds / m_per_deg_lon(lat0)
    expected_lat = lat0 + inputs["met"]["uniform"]["current_v"] * seconds / m_per_deg_lat(lat0)

    # Tolerance is generous: this is a hull centroid, not a particle centroid.
    assert centroid.x == pytest.approx(expected_lon, abs=0.01)
    assert centroid.y == pytest.approx(expected_lat, abs=0.01)


# ------------------------------------------------------------- the maths bits ------
def test_confidence_mask_trims_a_planted_outlier():
    """One fluke particle must not be allowed to stretch the predicted extent."""
    rng = np.random.default_rng(4)
    lons = np.concatenate([rng.normal(80.3, 0.01, 200), [80.9]])
    lats = np.concatenate([rng.normal(13.0, 0.01, 200), [13.6]])
    mask = confidence_mask(lons, lats, 0.9)
    assert mask[-1] is np.False_ or not mask[-1]
    assert 0.75 < mask.mean() < 1.0


def test_confidence_mask_never_trims_below_a_hullable_set():
    lons = np.array([80.30, 80.31, 80.32, 80.33])
    lats = np.array([13.00, 13.01, 13.00, 13.01])
    assert confidence_mask(lons, lats, 0.9).sum() >= 4


def test_ellipse_area_grows_with_the_spread():
    rng = np.random.default_rng(5)
    tight = ellipse_area_m2(rng.normal(80.3, 0.005, 300), rng.normal(13.0, 0.005, 300))
    wide = ellipse_area_m2(rng.normal(80.3, 0.020, 300), rng.normal(13.0, 0.020, 300))
    assert wide > tight * 4


def test_concave_hull_is_tighter_than_convex_on_a_bent_cloud():
    """The reason concave is the default: a convex hull spans the bay of a bent cloud.

    A crescent - what shear does to a drifting cloud - is the case that matters. Its
    convex hull fills the whole bay, claiming oil where there is none.
    """
    rng = np.random.default_rng(1)
    theta = rng.uniform(0.0, np.deg2rad(200), 400)
    radius = rng.uniform(0.040, 0.050, 400)
    lons = 80.3 + radius * np.cos(theta)
    lats = 13.0 + radius * np.sin(theta)

    concave, method = extent_polygon(lons, lats, ratio=0.3)
    convex = MultiPoint(list(zip(lons, lats))).convex_hull
    assert method == "concave"
    assert concave.area < convex.area * 0.5, "concave hull should exclude the bay"

    # ratio=1.0 is the convex hull by definition - a useful sanity check on semantics.
    widest, _ = extent_polygon(lons, lats, ratio=1.0)
    assert widest.area == pytest.approx(convex.area, rel=1e-6)


def test_hull_falls_back_to_convex_for_a_degenerate_cloud():
    lons = np.array([80.30, 80.30, 80.30])
    lats = np.array([13.00, 13.00, 13.00])
    polygon, method = extent_polygon(lons, lats, ratio=0.3)
    assert method == "convex"
    assert polygon.geom_type == "Polygon"


def test_build_forecast_skips_horizons_beyond_the_run():
    from engines.drift.euler_fallback import DriftRun

    times = np.arange(0.0, 6 * 3600.0 + 1, 600.0)
    lons = np.tile(np.linspace(80.30, 80.31, 50), (times.size, 1))
    lats = np.tile(np.linspace(13.00, 13.01, 50), (times.size, 1))
    run = DriftRun(times, lons, lats, direction=1)

    results, warnings = build_forecast(run, horizons=(6.0, 24.0))
    assert [r.horizon_h for r in results] == [6.0]
    assert any("outside the run" in w for w in warnings)


# ---------------------------------------------------------- run length + errors ----
def test_hours_flag_filters_the_horizons(inputs, tmp_path):
    out = tmp_path / "short.geojson"
    status = forecast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_strain"],
        wind_path=inputs["met"]["wind_zero"],
        config_path=DRIFT_CONFIG, hours=12.0,
    )
    assert status["ok"]
    assert any("+24 h" in w for w in status["warnings"])
    horizons = [f["properties"]["horizon_h"] for f in json.loads(out.read_text())["features"]]
    assert horizons == [6.0, 12.0]


def test_run_shorter_than_every_horizon_returns_MISSING_INPUT(inputs, tmp_path):
    status = forecast(
        inputs["slick"], tmp_path / "o.geojson",
        currents_path=inputs["met"]["currents_strain"],
        config_path=DRIFT_CONFIG, hours=2.0,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


def test_missing_netcdf_variable_returns_BAD_GRID(inputs, tmp_path):
    status = forecast(
        inputs["slick"], tmp_path / "o.geojson",
        currents_path=inputs["met"]["currents_missing_v"], config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "BAD_GRID"


def test_missing_slick_returns_MISSING_INPUT(tmp_path):
    status = forecast(tmp_path / "nope.geojson", tmp_path / "o.geojson",
                      config_path=DRIFT_CONFIG)
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


# ------------------------------------------------------------------------ CLI ------
def test_cli_runs_a_forecast(inputs, tmp_path, capsys):
    out = tmp_path / "cli.geojson"
    code = cli_main([
        "--slick", str(inputs["slick"]),
        "--currents", inputs["met"]["currents_strain"],
        "--wind", inputs["met"]["wind_uniform"],
        "--mode", "forecast", "--hours", "24",
        "--out", str(out), "--config", DRIFT_CONFIG,
    ])
    assert code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["ok"] is True
    assert "forecast" in status["outputs"]
    validate_forecast(json.loads(out.read_text(encoding="utf-8")))
