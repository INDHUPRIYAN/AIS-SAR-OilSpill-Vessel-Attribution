"""Engine B core tests: grid reading, interpolation, and the Euler integrator.

The must-pass items from handbook §8 live here:
  * analytic  - a constant current field backtracks to the hand-computed point
  * round-trip - forward then backward returns near the start
Plus the BAD_GRID / MISSING_INPUT failure classes.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from engines.common.errors import EngineError
from engines.common.geo import m_per_deg_lat, m_per_deg_lon
from engines.common.timeutil import parse_utc
from engines.drift.euler_fallback import BACKWARD, FORWARD, run_euler, seed_particles
from engines.drift.grids import Metocean, load_field, load_metocean
from engines.drift.grids import CURRENT_U, CURRENT_V
from tests.fixtures.make_metocean import build_metocean

START_UTC = "2017-02-02T00:39:42Z"
START_LON, START_LAT = 80.312, 13.052


@pytest.fixture(scope="module")
def met(tmp_path_factory) -> dict:
    return build_metocean(tmp_path_factory.mktemp("metocean"))


@pytest.fixture(scope="module")
def uniform(met) -> Metocean:
    field, _ = load_metocean(met["currents_uniform"], met["wind_zero"])
    return field


# ------------------------------------------------------------------- the grid ------
def test_uniform_field_samples_its_own_constant(met, uniform):
    u, v = uniform.current.sample(parse_utc(START_UTC).timestamp(), [START_LON], [START_LAT])
    assert u[0] == pytest.approx(met["uniform"]["current_u"], rel=1e-6)
    assert v[0] == pytest.approx(met["uniform"]["current_v"], rel=1e-6)


def test_interpolation_is_bilinear_between_grid_nodes(met):
    """The eddy field varies linearly in space, so interpolation must be exact."""
    field, _ = load_field(met["currents_eddy"], CURRENT_U, CURRENT_V, name="currents")
    eddy = met["eddy"]
    t = parse_utc(START_UTC).timestamp()

    # A point deliberately off-node, halfway between grid cells.
    lon, lat = eddy["lon"] + 0.031, eddy["lat"] + 0.017
    u, v = field.sample(t, [lon], [lat])

    dx = (lon - eddy["lon"]) * m_per_deg_lon(eddy["lat"])
    dy = (lat - eddy["lat"]) * m_per_deg_lat(eddy["lat"])
    assert u[0] == pytest.approx(-eddy["omega"] * dy, rel=1e-3)
    assert v[0] == pytest.approx(eddy["omega"] * dx, rel=1e-3)


def test_wind_leeway_is_three_percent(met):
    """v = current + 0.03 * wind10 (handbook §2.1)."""
    metocean, _ = load_metocean(met["currents_uniform"], met["wind_uniform"], leeway=0.03)
    u, v = metocean.drift_velocity(
        parse_utc(START_UTC).timestamp(), [START_LON], [START_LAT]
    )
    expected_u = met["uniform"]["current_u"] + 0.03 * met["uniform"]["wind_u"]
    expected_v = met["uniform"]["current_v"] + 0.03 * met["uniform"]["wind_v"]
    assert u[0] == pytest.approx(expected_u, rel=1e-6)
    assert v[0] == pytest.approx(expected_v, rel=1e-6)


def test_missing_variable_raises_BAD_GRID(met):
    with pytest.raises(EngineError) as excinfo:
        load_metocean(met["currents_missing_v"], met["wind_zero"])
    assert excinfo.value.error_class == "BAD_GRID"


def test_missing_file_raises_MISSING_INPUT(met, tmp_path):
    with pytest.raises(EngineError) as excinfo:
        load_metocean(tmp_path / "nope.nc", met["wind_zero"])
    assert excinfo.value.error_class == "MISSING_INPUT"


def test_grid_that_misses_the_slick_raises_BAD_GRID(met):
    metocean, _ = load_metocean(met["currents_wrong_region"], None)
    with pytest.raises(EngineError) as excinfo:
        metocean.check_coverage((80.2, 12.9, 80.4, 13.2), 0.0, 1.0)
    assert excinfo.value.error_class == "BAD_GRID"


def test_no_files_at_all_raises_MISSING_INPUT():
    with pytest.raises(EngineError) as excinfo:
        load_metocean(None, None)
    assert excinfo.value.error_class == "MISSING_INPUT"


# --------------------------------------------------------- the analytic ground truth
def test_backtrack_in_a_constant_current_hits_the_hand_computed_point(met, uniform):
    """Handbook §8: constant current -> the backtracked origin is computable by hand.

    In a steady field with no diffusion a particle travels exactly v*t, so rewinding
    24 h must land at the seed position minus that displacement - converted to degrees
    with the latitude scaling, not a flat constant.
    """
    hours = 24.0
    run = run_euler(
        np.array([START_LON]), np.array([START_LAT]), uniform,
        parse_utc(START_UTC).timestamp(),
        hours=hours, dt_seconds=600.0, direction=BACKWARD, diffusion_m2_s=0.0,
    )

    seconds = hours * 3600.0
    dx_m = met["uniform"]["current_u"] * seconds
    dy_m = met["uniform"]["current_v"] * seconds
    expected_lat = START_LAT - dy_m / m_per_deg_lat(START_LAT)
    expected_lon = START_LON - dx_m / m_per_deg_lon(START_LAT)

    # Tolerance ~15 m: the only error is the latitude scaling drifting slightly as the
    # particle moves, which a first-order Euler step cannot capture exactly.
    assert run.lats[-1, 0] == pytest.approx(expected_lat, abs=1.5e-4)
    assert run.lons[-1, 0] == pytest.approx(expected_lon, abs=1.5e-4)
    assert run.times_s[-1] == pytest.approx(run.times_s[0] - seconds)


def test_backward_run_reports_negative_elapsed_hours(uniform):
    run = run_euler(
        np.array([START_LON]), np.array([START_LAT]), uniform,
        parse_utc(START_UTC).timestamp(),
        hours=12.0, direction=BACKWARD, diffusion_m2_s=0.0,
    )
    elapsed = run.elapsed_hours()
    assert elapsed[0] == 0.0
    assert elapsed[-1] == pytest.approx(-12.0)
    assert (np.diff(elapsed) < 0).all()


def test_forward_and_backward_move_in_opposite_directions(uniform):
    kwargs = dict(hours=6.0, diffusion_m2_s=0.0)
    start = parse_utc(START_UTC).timestamp()
    back = run_euler(np.array([START_LON]), np.array([START_LAT]), uniform, start,
                     direction=BACKWARD, **kwargs)
    fwd = run_euler(np.array([START_LON]), np.array([START_LAT]), uniform, start,
                    direction=FORWARD, **kwargs)
    assert (fwd.lons[-1, 0] - START_LON) * (back.lons[-1, 0] - START_LON) < 0
    assert (fwd.lats[-1, 0] - START_LAT) * (back.lats[-1, 0] - START_LAT) < 0


def test_round_trip_returns_near_the_start(met):
    """Handbook §8: forward then backward returns near the start.

    Run in the eddy field so the test exercises spatially varying velocity, and with
    diffusion off - a random walk is not reversible, which is the point of the next
    test.
    """
    metocean, _ = load_metocean(met["currents_eddy"], met["wind_zero"])
    start = parse_utc("2017-02-01T06:00:00Z").timestamp()

    fwd = run_euler(np.array([START_LON]), np.array([START_LAT]), metocean, start,
                    hours=12.0, dt_seconds=300.0, direction=FORWARD, diffusion_m2_s=0.0)
    back = run_euler(fwd.lons[-1], fwd.lats[-1], metocean, fwd.times_s[-1],
                     hours=12.0, dt_seconds=300.0, direction=BACKWARD, diffusion_m2_s=0.0)

    error_m = math.hypot(
        (back.lons[-1, 0] - START_LON) * m_per_deg_lon(START_LAT),
        (back.lats[-1, 0] - START_LAT) * m_per_deg_lat(START_LAT),
    )
    assert error_m < 50.0, f"round trip drifted {error_m:.1f} m"


def test_diffusion_is_not_reversible(met):
    """Turbulent diffusion is a random walk; a round trip must NOT close with it on."""
    metocean, _ = load_metocean(met["currents_uniform"], met["wind_zero"])
    start = parse_utc("2017-02-01T06:00:00Z").timestamp()
    rng = np.random.default_rng(0)

    fwd = run_euler(np.array([START_LON]), np.array([START_LAT]), metocean, start,
                    hours=12.0, direction=FORWARD, diffusion_m2_s=5.0, rng=rng)
    back = run_euler(fwd.lons[-1], fwd.lats[-1], metocean, fwd.times_s[-1],
                     hours=12.0, direction=BACKWARD, diffusion_m2_s=5.0, rng=rng)

    error_m = math.hypot(
        (back.lons[-1, 0] - START_LON) * m_per_deg_lon(START_LAT),
        (back.lats[-1, 0] - START_LAT) * m_per_deg_lat(START_LAT),
    )
    assert error_m > 100.0


def test_diffusion_spreads_the_cloud_over_time(uniform):
    """Cloud spread must grow ~sqrt(2*K*t) - the defining property of the random walk."""
    n = 400
    lons = np.full(n, START_LON)
    lats = np.full(n, START_LAT)
    k = 5.0
    hours = 12.0
    run = run_euler(lons, lats, uniform, parse_utc(START_UTC).timestamp(),
                    hours=hours, dt_seconds=600.0, direction=BACKWARD,
                    diffusion_m2_s=k, rng=np.random.default_rng(7))

    spread_m = np.std((run.lons[-1] - run.lons[-1].mean()) * m_per_deg_lon(START_LAT))
    expected = math.sqrt(2.0 * k * hours * 3600.0)
    assert spread_m == pytest.approx(expected, rel=0.15)


# ------------------------------------------------------------------- seeding -------
def test_particles_seed_inside_the_polygon():
    from shapely.geometry import Point

    circle = Point(START_LON, START_LAT).buffer(0.01)
    lons, lats = seed_particles(circle, 250, np.random.default_rng(3))
    assert lons.size == lats.size == 250
    from shapely import contains_xy

    assert contains_xy(circle, lons, lats).all()


def test_seeding_is_reproducible():
    from shapely.geometry import Point

    circle = Point(START_LON, START_LAT).buffer(0.01)
    a = seed_particles(circle, 100, np.random.default_rng(11))
    b = seed_particles(circle, 100, np.random.default_rng(11))
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
