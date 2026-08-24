"""Engine B end-to-end: slick.geojson -> origin_cloud.geojson, contract and failures.

Runs the real Engine A first so the drift engine is fed a genuine contract file, not a
hand-written stand-in - that is the integration these two engines actually perform.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from engines.characterise import characterise
from engines.common.geo import m_per_deg_lat, m_per_deg_lon
from engines.common.timeutil import parse_utc
from engines.drift import hindcast
from engines.drift.__main__ import main as cli_main
from engines.schemas.origin_cloud import validate_origin_cloud
from tests.fixtures.make_mask import build_scene
from tests.fixtures.make_metocean import build_metocean

DRIFT_CONFIG = "config/drift.yaml"


@pytest.fixture(scope="module")
def inputs(tmp_path_factory) -> dict:
    """Engine A output + met-ocean mocks, i.e. exactly what Engine B receives."""
    work = tmp_path_factory.mktemp("drift_e2e")
    scene = build_scene(work / "scene")
    met = build_metocean(work / "met")

    slick_path = work / "slick.geojson"
    status = characterise(
        scene["mask_path"], scene["scene_meta_path"], slick_path,
        config_path="config/characterise.yaml",
    )
    assert status["ok"], status
    return {"slick": slick_path, "met": met, "scene": scene, "work": work}


@pytest.fixture(scope="module")
def run(inputs) -> tuple[dict, dict]:
    out = inputs["work"] / "origin_cloud.geojson"
    status = hindcast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_eddy"],
        wind_path=inputs["met"]["wind_uniform"],
        config_path=DRIFT_CONFIG,
    )
    assert status["ok"], status
    return status, json.loads(out.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ contract -------
def test_status_reports_the_fallback_engine(run):
    """Euler is the fallback by design; OpenOil/OceanDrift are the primaries."""
    status, _ = run
    assert status["ok"] is True
    assert status["engine_used"] == "fallback"
    assert "origin_cloud" in status["outputs"]


def test_output_validates_against_the_contract(run):
    _, document = run
    validate_origin_cloud(document)


def test_output_carries_particles_ellipses_and_one_window(run):
    _, document = run
    kinds = [(f["properties"].get("kind") or "particle") for f in document["features"]]
    assert kinds.count("origin_window") == 1
    assert kinds.count("confidence_ellipse") >= 12      # one per hourly timestep
    assert kinds.count("particle") > 1000               # 300 particles x timesteps


def test_particle_properties_match_the_contract(run):
    _, document = run
    particles = [
        f for f in document["features"] if not (f["properties"].get("kind"))
    ]
    for feature in particles[:50]:
        props = feature["properties"]
        assert set(props) == {"time_utc", "weight", "timestep_h"}
        assert 0.0 <= props["weight"] <= 1.0
        assert props["timestep_h"] <= 0.0               # a hindcast runs backward
        assert props["time_utc"].endswith("Z")
        assert feature["geometry"]["type"] == "Point"


def test_timesteps_span_the_whole_backward_run(run):
    _, document = run
    steps = {
        f["properties"]["timestep_h"]
        for f in document["features"]
        if f["properties"].get("kind") == "confidence_ellipse"
    }
    assert min(steps) == pytest.approx(-24.0)
    assert max(steps) == 0.0


def test_origin_window_is_a_window_not_a_point(run):
    """Handbook pitfall #5: never emit a single origin point."""
    _, document = run
    window = next(
        f for f in document["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    props = window["properties"]
    start = parse_utc(props["start_utc"])
    end = parse_utc(props["end_utc"])
    peak = parse_utc(props["peak_utc"])
    assert start <= peak <= end
    assert end > start, "the origin window has zero duration"
    assert props["engine_used"] == "euler"


def test_cloud_grows_backward_in_time(run):
    """Uncertainty must increase the further back the hindcast reaches."""
    _, document = run
    from shapely.geometry import shape

    areas = {
        f["properties"]["timestep_h"]: shape(f["geometry"]).area
        for f in document["features"]
        if f["properties"].get("kind") == "confidence_ellipse"
    }
    assert areas[-24.0] > areas[-1.0] > 0


def test_particles_move_away_from_the_slick(run, inputs):
    """A 24 h backtrack must actually displace the cloud, not sit on the slick."""
    _, document = run
    slick = json.loads(Path(inputs["slick"]).read_text(encoding="utf-8"))
    centroid = slick["features"][0]["properties"]["centroid"]

    latest = [
        f["geometry"]["coordinates"]
        for f in document["features"]
        if not f["properties"].get("kind")
        and f["properties"]["timestep_h"] == -24.0
    ]
    mean_lon = sum(c[0] for c in latest) / len(latest)
    mean_lat = sum(c[1] for c in latest) / len(latest)
    moved_m = (
        ((mean_lon - centroid[0]) * m_per_deg_lon(centroid[1])) ** 2
        + ((mean_lat - centroid[1]) * m_per_deg_lat(centroid[1])) ** 2
    ) ** 0.5
    assert moved_m > 500.0


def test_run_is_reproducible(inputs, tmp_path):
    kwargs = dict(
        currents_path=inputs["met"]["currents_eddy"],
        wind_path=inputs["met"]["wind_uniform"],
        config_path=DRIFT_CONFIG,
    )
    a, b = tmp_path / "a.geojson", tmp_path / "b.geojson"
    hindcast(inputs["slick"], a, **kwargs)
    hindcast(inputs["slick"], b, **kwargs)
    assert a.read_text() == b.read_text()


# ------------------------------------------------- degenerate and failure paths ----
def _window_of(path) -> dict:
    return next(
        f["properties"] for f in json.loads(Path(path).read_text())["features"]
        if f["properties"].get("kind") == "origin_window"
    )


@pytest.mark.parametrize("field", ["currents_uniform", "currents_eddy"])
def test_non_deforming_fields_admit_they_cannot_locate_the_origin_time(
    inputs, tmp_path, field
):
    """Uniform translation and rigid rotation both preserve cloud shape exactly.

    Neither can localise a release time from drift alone, and the engine must say so
    rather than inventing a peak. The window widens to the whole run - the honest
    answer, since an elongated slick under a non-deforming flow implies a *moving*
    source, which is Engine C's problem to resolve against AIS tracks.
    """
    out = tmp_path / f"{field}.geojson"
    status = hindcast(
        inputs["slick"], out,
        currents_path=inputs["met"][field],
        wind_path=inputs["met"]["wind_zero"],
        config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is True
    assert any("does not deform the cloud" in w for w in status["warnings"])

    window = _window_of(out)
    assert window["method"] in {"age_estimate", "midpoint"}
    span_h = (
        parse_utc(window["end_utc"]) - parse_utc(window["start_utc"])
    ).total_seconds() / 3600.0
    assert span_h == pytest.approx(24.0, abs=0.1)


def test_a_deforming_field_does_locate_a_convergence_peak(inputs, tmp_path):
    """With real deformation the backtracked cloud converges, and the peak is found.

    The window must then be narrower than the whole run - that narrowing is the entire
    value the hindcast adds over "somewhere in the last 24 hours".
    """
    out = tmp_path / "strain.geojson"
    status = hindcast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_strain"],
        wind_path=inputs["met"]["wind_zero"],
        config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is True
    assert not any("does not deform the cloud" in w for w in status["warnings"])

    window = _window_of(out)
    assert window["method"] == "cloud_convergence"
    span_h = (
        parse_utc(window["end_utc"]) - parse_utc(window["start_utc"])
    ).total_seconds() / 3600.0
    assert 0 < span_h < 24.0
    assert parse_utc(window["start_utc"]) <= parse_utc(window["peak_utc"])


def test_diffusion_is_removed_before_the_convergence_search():
    """A random walk inflates spread as sqrt(t) regardless of the flow.

    Left in, it would place the minimum at detection time on every run, so the known
    diffusive variance (4*K*t across both components) is subtracted analytically.
    """
    from engines.drift.cloud import advective_spread_m

    k, hours = 5.0, 12.0
    diffusive_only = math.sqrt(4.0 * k * hours * 3600.0)
    assert advective_spread_m(diffusive_only, -hours, k) == pytest.approx(0.0, abs=1e-6)
    # A flow-driven spread on top of the noise survives the subtraction.
    combined = math.sqrt(diffusive_only**2 + 800.0**2)
    assert advective_spread_m(combined, -hours, k) == pytest.approx(800.0, rel=1e-6)
    # With diffusion off the measurement passes through untouched.
    assert advective_spread_m(500.0, -hours, 0.0) == 500.0


def test_missing_slick_returns_MISSING_INPUT(inputs, tmp_path):
    status = hindcast(
        tmp_path / "nope.geojson", tmp_path / "o.geojson",
        currents_path=inputs["met"]["currents_uniform"], config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


def test_missing_netcdf_variable_returns_BAD_GRID(inputs, tmp_path):
    status = hindcast(
        inputs["slick"], tmp_path / "o.geojson",
        currents_path=inputs["met"]["currents_missing_v"], config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "BAD_GRID"


def test_grid_over_the_wrong_ocean_returns_BAD_GRID(inputs, tmp_path):
    status = hindcast(
        inputs["slick"], tmp_path / "o.geojson",
        currents_path=inputs["met"]["currents_wrong_region"], config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "BAD_GRID"


def test_wind_only_mode_still_runs(inputs, tmp_path):
    """Fallback register: with no currents the engine runs wind-only, with a warning."""
    out = tmp_path / "windonly.geojson"
    status = hindcast(
        inputs["slick"], out, wind_path=inputs["met"]["wind_uniform"],
        config_path=DRIFT_CONFIG,
    )
    assert status["ok"] is True
    assert any("zero-current" in w for w in status["warnings"])
    validate_origin_cloud(json.loads(out.read_text(encoding="utf-8")))


def test_unknown_slick_id_returns_MISSING_INPUT(inputs, tmp_path):
    status = hindcast(
        inputs["slick"], tmp_path / "o.geojson",
        currents_path=inputs["met"]["currents_uniform"],
        config_path=DRIFT_CONFIG, slick_id="does-not-exist",
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


# ---------------------------------------------------------------------- CLI --------
def test_cli_runs_a_hindcast(inputs, tmp_path, capsys):
    out = tmp_path / "cli.geojson"
    code = cli_main([
        "--slick", str(inputs["slick"]),
        "--currents", inputs["met"]["currents_eddy"],
        "--wind", inputs["met"]["wind_uniform"],
        "--mode", "hindcast", "--hours", "12",
        "--out", str(out), "--config", DRIFT_CONFIG,
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    validate_origin_cloud(json.loads(out.read_text(encoding="utf-8")))


def test_cli_routes_each_mode_to_its_own_writer(inputs, tmp_path, capsys):
    """Both frozen modes work; each names its own output in the status object."""
    for mode, key in (("hindcast", "origin_cloud"), ("forecast", "forecast")):
        code = cli_main([
            "--slick", str(inputs["slick"]),
            "--currents", inputs["met"]["currents_strain"],
            "--wind", inputs["met"]["wind_zero"],
            "--mode", mode, "--hours", "24",
            "--out", str(tmp_path / f"{mode}.geojson"), "--config", DRIFT_CONFIG,
        ])
        assert code == 0, mode
        assert key in json.loads(capsys.readouterr().out)["outputs"]
