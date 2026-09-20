"""BAYES-TRACK hindcast: geometry, forcing field, integrator and oil physics.

Hindcast code is imported INSIDE the module fixture, after DATA_ROOT and
DATABASE_URL point at a temp directory and every cached `backend` module has
been dropped -- the same discipline as the rest of this suite, so nothing here
can touch the live database or write a job folder into the real data root.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Point, box, shape

T0 = datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)
CENTER = (88.0, 14.0)
NO_ARCHIVE = lambda aoi, a, b: []      # noqa: E731
NO_SINK = lambda job_id, rows: None    # noqa: E731

_IMPORTS = """
from backend.services.hindcast.config import PipelineConfig
from backend.services.hindcast.engines import ENGINE_ORDER
from backend.services.hindcast.engines.base import EngineError, PipelineContext
from backend.services.hindcast.engines.bounds import ArchiveHit, BoundsEngine, estimate_age_hours
from backend.services.hindcast.engines.drift import DriftEngine, chunk_ranges, read_l_drift
from backend.services.hindcast.engines.forcing_engine import ForcingEngine, met_direction_from, wind_bias
from backend.services.hindcast.engines.ingest import IngestEngine, damping_weight_fn
from backend.services.hindcast.engines.posterior import PosteriorEngine, hdr_interval, tau_modes
from backend.services.hindcast.engines.shape import ShapeEngine, classify, net_rotation_deg
from backend.services.hindcast.engines.verify import VerifyEngine, select_candidates
from backend.services.hindcast.forcing import DriftParams, ForcingField, synthetic_field
from backend.services.hindcast.geo import (RasterGrid, edge_roughness, geojson_of, hdr_mask, load_geometry,
                                           mask_to_geometry, sample_in_polygon, shape_stats, smooth_density)
from backend.services.hindcast.integrator import MemberParams, NumpyRK4Backend
from backend.services.hindcast.oil import predicted_area_km2, remaining_fraction
from backend.services.hindcast.synthetic import build_demo_request
"""


@pytest.fixture(scope="module", autouse=True)
def hindcast_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("hindcast_root")
    saved = {k: os.environ.get(k) for k in ("DATA_ROOT", "DATABASE_URL", "SECRET_KEY", "HINDCAST_DRIFT_BACKEND")}
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'hindcast.db').as_posix()}"
    os.environ["SECRET_KEY"] = "h" * 64
    os.environ["HINDCAST_DRIFT_BACKEND"] = "numpy"
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    exec(_IMPORTS, globals())
    yield root
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]


def uniform_field(current=(0.2, 0.0), wind=(0.0, 0.0), stokes=(0.0, 0.0), hours=60,
                  start=datetime(2026, 2, 27, 0, 0, tzinfo=timezone.utc)):
    lons = np.arange(CENTER[0] - 1.5, CENTER[0] + 1.5 + 1e-9, 0.1)
    lats = np.arange(CENTER[1] - 1.5, CENTER[1] + 1.5 + 1e-9, 0.1)
    times = np.datetime64(start.replace(tzinfo=None), "s") + np.arange(hours) * np.timedelta64(3600, "s")
    data = np.zeros((hours, 6, lats.size, lons.size), dtype=np.float32)
    for i, value in enumerate([*current, *wind, *stokes]):
        data[:, i] = value
    return ForcingField(times, lats, lons, data)


def square_slick(half_deg: float = 0.02):
    return box(CENTER[0] - half_deg, CENTER[1] - half_deg, CENTER[0] + half_deg, CENTER[1] + half_deg)


@pytest.fixture
def tiny_config():
    return PipelineConfig(members=4, particles=300, member_chunk=2, snapshot_particles=50,
                          grid_max_cells=64, verify_candidates=24, verify_particles=200)


@pytest.fixture
def make_ctx(tmp_path, tiny_config):
    def factory(request=None, state=None, config=None):
        req = {"scene_meta": {"scene_id": "TEST", "acquired_utc": T0.isoformat()},
               "slick_polygon_geojson": geojson_of(square_slick()), **(request or {})}
        return PipelineContext("test-job", tmp_path, config or tiny_config, req, state=dict(state or {}))

    return factory


# ---------------------------------------------------------------- geometry --

def test_hdr_mask_holds_the_requested_mass_in_the_fewest_cells():
    p = np.array([[0.5, 0.2], [0.2, 0.1]])
    assert hdr_mask(p, 0.5).sum() == 1
    assert hdr_mask(p, 0.85).sum() == 3
    assert hdr_mask(np.zeros((3, 3)), 0.9).sum() == 0


def test_shape_stats_reads_orientation_elongation_and_area_of_a_rectangle():
    rect = box(88.0, 14.0, 88.2, 14.02)          # long axis east-west
    s = shape_stats(rect)
    assert s.orientation_deg == pytest.approx(0.0, abs=3.0) or s.orientation_deg == pytest.approx(180.0, abs=3.0)
    assert s.elongation > 5
    expected_km2 = (0.2 * 111.32 * math.cos(math.radians(14.01))) * (0.02 * 110.54)
    assert s.area_km2 == pytest.approx(expected_km2, rel=0.02)
    assert abs(s.bend_deg) < 5 and s.fragments == 1


def test_fragments_are_counted_and_rough_edges_score_higher():
    two = MultiPolygon([box(0, 0, 1, 1), box(3, 0, 4, 1)])
    assert shape_stats(two).fragments == 2
    smooth = Point(0, 0).buffer(1.0)
    ragged = smooth.difference(box(-0.2, 0, 0.2, 2)).difference(box(0, -0.2, 2, 0.2))
    assert edge_roughness(ragged) > edge_roughness(smooth)


def test_samples_fall_inside_the_polygon_and_follow_weights():
    rng = np.random.default_rng(0)
    slick = square_slick()
    lon, lat = sample_in_polygon(slick, 500, rng)
    assert lon.size == 500 and all(slick.covers(Point(x, y)) for x, y in zip(lon[:50], lat[:50]))
    east_only = lambda lo, la: (lo > CENTER[0]).astype(float)   # noqa: E731
    lon_w, _ = sample_in_polygon(slick, 500, rng, east_only)
    assert (lon_w > CENTER[0]).mean() > 0.9


def test_raster_grid_round_trips_a_polygon():
    grid = RasterGrid.around(87.9, 13.9, 88.1, 14.1, max_cells=80)
    mask = grid.rasterize(square_slick())
    geom = mask_to_geometry(mask, grid, smooth=False)
    assert geom.area == pytest.approx(square_slick().area, rel=0.15)
    dens = smooth_density(grid.histogram(np.array([88.0]), np.array([14.0])), 1.0)
    assert dens.sum() == pytest.approx(1.0)


def test_load_geometry_rejects_non_polygons_and_accepts_features():
    assert load_geometry({"type": "Feature", "geometry": square_slick().__geo_interface__}).area > 0
    with pytest.raises(ValueError):
        load_geometry({"type": "Point", "coordinates": [0, 0]})


# ----------------------------------------------------------------- forcing --

def test_bilinear_sampling_is_exact_on_a_linear_field():
    f = uniform_field()
    f.data[:, 0] = (f.lons[None, None, :] - CENTER[0]) * 2.0      # u_curr linear in lon
    out = f.sample(np.array([88.03, 88.47]), np.array([14.2, 13.9]), 3.0)
    assert out[0] == pytest.approx([0.06, 0.94], abs=1e-4)


def test_wind_drift_is_deflected_right_in_the_north_and_left_in_the_south():
    f = uniform_field(current=(0, 0), wind=(10.0, 0.0))            # wind blowing east
    p = DriftParams(alpha=0.03, theta_deg=20.0, beta=1.0)
    _, v_north = f.velocity(np.array([88.0]), np.array([14.0]), 0, p)
    assert v_north[0] < 0                                          # pushed south of east = to the right
    f.lats[:] = f.lats - 30                                        # same grid, southern hemisphere
    _, v_south = f.velocity(np.array([88.0]), np.array([-16.0]), 0, p)
    assert v_south[0] > 0


def test_wind_bias_recovers_a_known_scale_and_veer():
    u, v = 6.0, 2.0
    veer = math.radians(-8.0)                                      # 8 degrees clockwise
    tu = 1.1 * (u * math.cos(veer) - v * math.sin(veer))
    tv = 1.1 * (u * math.sin(veer) + v * math.cos(veer))
    bias = wind_bias(math.hypot(tu, tv), met_direction_from(tu, tv), u, v)
    assert bias["scale"] == pytest.approx(1.1, abs=1e-6)
    assert bias["d_dir"] == pytest.approx(8.0, abs=1e-6)
    corrected = uniform_field(wind=(u, v)).with_wind_correction(bias["scale"], bias["rotation_deg"])
    assert corrected.wind_at(88.0, 14.0, 0) == pytest.approx((tu, tv), abs=1e-4)


def test_wind_bias_does_not_rotate_by_the_direction_of_a_calm():
    assert wind_bias(5.0, 90.0, 0.1, 0.1)["rotation_deg"] == 0.0


def test_synthetic_field_is_the_same_in_overlapping_windows():
    ref = T0 - timedelta(hours=200)
    a = synthetic_field(T0 - timedelta(hours=30), 40, CENTER, t_ref=ref)
    b = synthetic_field(T0 - timedelta(hours=10), 20, CENTER, t_ref=ref)
    np.testing.assert_allclose(a.data[20:40], b.data[:20], rtol=1e-5)


# -------------------------------------------------------------- integrator --

def _params(n, k_diff=0.0):
    return MemberParams(alpha=np.full(n, 0.03), theta_deg=np.zeros(n), beta=np.ones(n),
                        current_scale=np.ones(n), k_diff=np.full(n, k_diff))


def test_backward_rk4_in_a_uniform_current_moves_upstream_by_speed_times_time():
    f = uniform_field(current=(0.2, 0.0))
    k = f.index_of(T0)
    lon, lat = NumpyRK4Backend().integrate(f, np.array([88.0]), np.array([14.0]), _params(1), k, 10, True,
                                           np.random.default_rng(0))
    expected = 88.0 - 0.2 * 36000 / (111_320 * math.cos(math.radians(14.0)))
    assert lon[0] == pytest.approx(expected, abs=1e-5) and lat[0] == pytest.approx(14.0, abs=1e-9)


def test_forward_then_backward_returns_home_without_diffusion():
    f = synthetic_field(T0 - timedelta(hours=30), 40, CENTER)
    k0, rng, be = f.index_of(T0) - 20, np.random.default_rng(0), NumpyRK4Backend()
    lon, lat = be.integrate(f, np.array([88.1]), np.array([14.05]), _params(1), k0, 20, False, rng)
    lon, lat = be.integrate(f, lon, lat, _params(1), k0 + 20, 20, True, rng)
    assert (lon[0], lat[0]) == pytest.approx((88.1, 14.05), abs=2e-4)


def test_diffusion_spreads_the_cloud_as_sqrt_2kt():
    f = uniform_field(current=(0, 0))
    n, k_diff, hours = 4000, 20.0, 12
    lon, lat = NumpyRK4Backend().integrate(f, np.full(n, 88.0), np.full(n, 14.0), _params(n, k_diff),
                                           f.index_of(T0), hours, True, np.random.default_rng(1))
    sigma_m = np.std((lat - 14.0) * 110_540.0)
    assert sigma_m == pytest.approx(math.sqrt(2 * k_diff * hours * 3600), rel=0.06)


def test_oil_physics_is_monotonic_and_bounded():
    area = predicted_area_km2(50.0, np.array([1.0, 6.0, 24.0]), "medium", 10.0)
    assert np.all(np.diff(area) > 0)
    left = remaining_fraction(np.array([0.0, 24.0, 1000.0]), "light")
    assert left[0] == 1.0 and left[1] < 1.0 and left[2] == pytest.approx(0.45, abs=1e-3)
