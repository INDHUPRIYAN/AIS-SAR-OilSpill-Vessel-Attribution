"""BAYES-TRACK hindcast: each engine on its own, with fixtures small enough to check by hand.

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




# ------------------------------------------------------------------ ingest --

def test_ingest_reads_time_area_and_builds_an_aoi(make_ctx):
    ctx = make_ctx()
    res = IngestEngine().run(ctx)
    assert ctx.state["scene_time"] == T0.isoformat()
    assert res.metrics["area_km2"] == pytest.approx(shape_stats(square_slick()).area_km2, abs=0.01)
    assert shape(ctx.state["aoi_geojson"]).contains(square_slick())


def test_ingest_refuses_a_scene_without_a_capture_time(make_ctx):
    ctx = make_ctx({"scene_meta": {"scene_id": "X"}})
    with pytest.raises(EngineError, match="acquired_utc"):
        IngestEngine().run(ctx)


def test_ingest_refuses_a_slick_outside_the_footprint(make_ctx):
    ctx = make_ctx({"scene_meta": {"acquired_utc": T0.isoformat(), "footprint": [10.0, 10.0, 11.0, 11.0]}})
    with pytest.raises(EngineError, match="outside the scene footprint"):
        IngestEngine().run(ctx)


def test_ingest_extracts_a_damping_raster_that_marks_the_dark_half(make_ctx, tmp_path):
    # sigma0 in dB: sea at -8, slick at -14 on its west half and -20 on its east half.
    west, north, res, n = CENTER[0] - 0.1, CENTER[1] + 0.1, 0.002, 100
    band = np.full((n, n), -8.0, dtype=np.float32)
    lons = west + (np.arange(n) + 0.5) * res
    lats = north - (np.arange(n) + 0.5) * res
    inside = (np.abs(lons[None, :] - CENTER[0]) < 0.02) & (np.abs(lats[:, None] - CENTER[1]) < 0.02)
    band[inside & (lons[None, :] < CENTER[0])] = -14.0
    band[inside & (lons[None, :] >= CENTER[0])] = -20.0
    path = tmp_path / "scene.tif"
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(west, north, res, res)) as dst:
        dst.write(band, 1)
    ctx = make_ctx({"scene_meta": {"acquired_utc": T0.isoformat(), "scene_path": str(path)}})
    res_ = IngestEngine().run(ctx)
    assert res_.metrics["damping_raster"] is True
    weight = damping_weight_fn(ctx.state["damping_path"])
    assert weight(np.array([CENTER[0] + 0.01]), np.array([CENTER[1]]))[0] > \
        weight(np.array([CENTER[0] - 0.01]), np.array([CENTER[1]]))[0] > 0


# ------------------------------------------------------------------ bounds --

def _after_ingest(make_ctx, **request):
    ctx = make_ctx(request)
    IngestEngine().run(ctx)
    return ctx


def test_bounds_default_to_the_heavy_oil_ceiling(make_ctx):
    ctx = _after_ingest(make_ctx)
    res = BoundsEngine(NO_ARCHIVE).run(ctx)
    assert res.metrics["tau_max_h"] == 336 and "heavy" in res.metrics["limited_by"]
    assert sum(ctx.state["p_age"]) == pytest.approx(1.0)
    assert len(ctx.state["tau_hours"]) == len(ctx.state["p_age"]) == 336


def test_a_clean_prior_scene_is_a_hard_upper_bound(make_ctx):
    ctx = _after_ingest(make_ctx, oil_type="medium")
    hits = [ArchiveHit(T0 - timedelta(hours=30), False), ArchiveHit(T0 - timedelta(hours=90), False)]
    res = BoundsEngine(lambda aoi, a, b: hits).run(ctx)
    assert res.metrics["tau_max_h"] == 30 and "clean scene" in res.metrics["limited_by"]


def test_an_earlier_sighting_raises_the_lower_bound(make_ctx):
    ctx = _after_ingest(make_ctx, oil_type="medium")
    hits = [ArchiveHit(T0 - timedelta(hours=40), False), ArchiveHit(T0 - timedelta(hours=12), True)]
    res = BoundsEngine(lambda aoi, a, b: hits).run(ctx)
    assert (res.metrics["tau_min_h"], res.metrics["tau_max_h"]) == (12, 40)


def test_a_sighting_before_the_last_clean_pass_is_a_different_slick(make_ctx):
    ctx = _after_ingest(make_ctx, oil_type="medium")
    hits = [ArchiveHit(T0 - timedelta(hours=60), True), ArchiveHit(T0 - timedelta(hours=20), False)]
    res = BoundsEngine(lambda aoi, a, b: hits).run(ctx)
    assert (res.metrics["tau_min_h"], res.metrics["tau_max_h"]) == (1, 20)


def test_older_looking_slicks_get_an_older_prior():
    assert estimate_age_hours(40.0, 4, 0.5) > estimate_age_hours(2.0, 1, 0.05)


def test_unknown_oil_type_is_an_engine_error(make_ctx):
    ctx = _after_ingest(make_ctx, oil_type="custard")
    with pytest.raises(EngineError, match="oil_type"):
        BoundsEngine(NO_ARCHIVE).run(ctx)


# ----------------------------------------------------------------- forcing --

def _after_bounds(make_ctx, clean_h=12, **request):
    ctx = _after_ingest(make_ctx, oil_type="light", **request)
    BoundsEngine(lambda aoi, a, b: [ArchiveHit(T0 - timedelta(hours=clean_h), False)]).run(ctx)
    return ctx


def test_forcing_engine_loads_synthetic_and_reports_no_bias_without_sar_wind(make_ctx):
    ctx = _after_bounds(make_ctx, forcing={"synthetic": {}})
    res = ForcingEngine().run(ctx)
    assert res.metrics["source"] == "synthetic" and res.metrics["wind_scale"] == 1.0
    assert ctx.path("forcing.npz").exists()


def test_forcing_engine_without_files_or_credentials_fails_with_the_todo(make_ctx):
    ctx = _after_bounds(make_ctx)
    with pytest.raises(EngineError, match="CDS_API_KEY|wind_path"):
        ForcingEngine().run(ctx)


def test_forcing_engine_reads_era5_and_cmems_style_netcdf(make_ctx, tmp_path):
    field = uniform_field(current=(0.3, -0.1), wind=(5.0, 1.0), hours=72)
    ds = field.to_dataset()
    ds[["u_wind", "v_wind"]].rename({"u_wind": "u10", "v_wind": "v10", "lat": "latitude", "lon": "longitude"}) \
        .to_netcdf(tmp_path / "era5.nc")
    ds[["u_curr", "v_curr"]].rename({"u_curr": "uo", "v_curr": "vo"}).expand_dims(depth=[0.5]) \
        .transpose("time", "depth", "lat", "lon").to_netcdf(tmp_path / "cmems.nc")
    ctx = _after_bounds(make_ctx, forcing={"wind_path": str(tmp_path / "era5.nc"),
                                           "current_path": str(tmp_path / "cmems.nc"), "half_size_deg": 0.6})
    ForcingEngine().run(ctx)
    loaded = ForcingField.load(ctx.state["forcing_path"])
    sample = loaded.sample(np.array([CENTER[0]]), np.array([CENTER[1]]), 3.0)[:, 0]
    assert sample == pytest.approx([0.3, -0.1, 5.0, 1.0, 0.0, 0.0], abs=1e-4)


# ------------------------------------------------------------------- drift --

def _with_field(ctx, field):
    field.save(ctx.path("forcing.npz"))
    ctx.state["forcing_path"] = str(ctx.path("forcing.npz"))
    return ctx


def test_drift_backtracks_upstream_and_writes_one_band_per_age(make_ctx):
    ctx = _with_field(_after_bounds(make_ctx, clean_h=10), uniform_field(current=(0.25, 0.0)))
    res = DriftEngine(NO_SINK).run(ctx)
    taus = ctx.state["tau_hours"]
    l_drift = read_l_drift(ctx.state["l_drift_path"])
    assert l_drift.shape[0] == len(taus) == 10
    assert l_drift.sum(axis=(1, 2)) == pytest.approx(np.ones(10), abs=1e-5)
    grid = RasterGrid.from_dict(ctx.state["drift_grid"])
    row, col = np.unravel_index(np.argmax(l_drift[-1]), l_drift[-1].shape)
    lon, lat = grid.center(row, col)
    upstream = CENTER[0] - 0.25 * 10 * 3600 / (111_320 * math.cos(math.radians(CENTER[1])))
    assert float(lon) == pytest.approx(upstream, abs=0.02) and float(lat) == pytest.approx(CENTER[1], abs=0.02)
    assert res.metrics["particles_simulated"] == 4 * 300 and ctx.state["drift_mass_inside_grid"] > 0.98


def test_drift_saves_snapshots_per_member_and_registers_them(make_ctx):
    ctx = _with_field(_after_bounds(make_ctx, clean_h=6), uniform_field())
    rows: list[dict] = []
    DriftEngine(lambda job_id, r: rows.extend(r)).run(ctx)
    assert len(rows) == 4 * 6 and {r["tau_hours"] for r in rows} == set(range(1, 7))
    frame = pd.read_parquet(rows[0]["parquet_path"])
    assert len(frame) == 6 * 50 and set(frame.columns) == {"tau_hours", "lon", "lat"}


def test_drift_refuses_to_finalize_a_partial_ensemble(make_ctx):
    ctx = _with_field(_after_bounds(make_ctx, clean_h=6), uniform_field())
    engine = DriftEngine(NO_SINK)
    engine.prepare(ctx)
    engine.run_members(ctx, 0, 2)
    with pytest.raises(EngineError, match="only 2 of 4"):
        engine.finalize(ctx)


def test_member_chunks_cover_every_member_once():
    assert chunk_ranges(10, 4) == [(0, 4), (4, 8), (8, 10)]


# ------------------------------------------------------------------- shape --

def test_shape_prefers_the_age_whose_spreading_matches_the_area(make_ctx, tiny_config):
    true_age = 8
    side_km = math.sqrt(float(predicted_area_km2(tiny_config.spill_volume_m3, true_age, "light",
                                                 tiny_config.k_diff.mid)))
    half = side_km / 2 / 110.54
    ctx = make_ctx({"slick_polygon_geojson": geojson_of(box(CENTER[0] - half, CENTER[1] - half,
                                                           CENTER[0] + half, CENTER[1] + half)),
                    "oil_type": "light"})
    IngestEngine().run(ctx)
    BoundsEngine(lambda aoi, a, b: [ArchiveHit(T0 - timedelta(hours=30), False)]).run(ctx)
    _with_field(ctx, uniform_field(wind=(0.5, 0.0)))
    res = ShapeEngine().run(ctx)
    assert abs(res.metrics["best_fit_age_h"] - true_age) <= 2
    assert max(ctx.state["l_shape"]) == pytest.approx(1.0) and res.metrics["slick_type"] == "blob"


def test_slick_classification_and_wind_veer():
    assert classify(5.0, 8000.0, 0) == "linear_discharge"
    assert classify(1.2, 3000.0, 0) == "blob"
    assert classify(5.0, 8000.0, 3) == "recurring_seep"
    ang = np.radians([40.0, 30.0, 10.0, 0.0])                     # newest first; older winds further CCW
    winds = np.column_stack([np.cos(ang), np.sin(ang)])
    assert net_rotation_deg(winds) == pytest.approx(40.0)          # veered CCW by 40 from oldest to newest


# ------------------------------------------------------------------ verify --

def test_candidates_are_stratified_so_every_age_is_represented():
    l_drift = np.zeros((6, 10, 10))
    for i in range(6):
        l_drift[i, i, i] = 1.0 / (i + 1)        # young ages have much taller peaks
    picks = select_candidates(l_drift, np.ones(6), n_total=6)
    assert sorted(p[0] for p in picks) == list(range(6))
    assert all(r == c == i for i, r, c in picks)


def _through_shape(make_ctx, clean_h=12):
    ctx = _with_field(_after_bounds(make_ctx, clean_h=clean_h), uniform_field(current=(0.25, 0.0), wind=(2.0, 0.0)))
    DriftEngine(NO_SINK).run(ctx)
    ShapeEngine().run(ctx)
    return ctx


def test_verify_scores_candidates_and_keeps_components(make_ctx):
    ctx = _through_shape(make_ctx)
    res = VerifyEngine().run(ctx)
    cands = ctx.state["verify_candidates"]
    assert res.metrics["candidates_verified"] == len(cands) > 0
    assert all(0.0 <= c["l_fwd"] <= 1.0 and 0.0 <= c["iou"] <= 1.0 for c in cands)
    best = max(cands, key=lambda c: c["l_fwd"])
    assert best["centroid_err_m"] < 3000        # a good candidate lands back on the slick


# --------------------------------------------------------------- posterior --

def test_posterior_reports_map_window_and_nested_regions(make_ctx):
    ctx = _through_shape(make_ctx)
    VerifyEngine().run(ctx)
    res = PosteriorEngine().run(ctx)
    r = ctx.state["result"]
    assert sum(r["p_tau"]["probability"]) == pytest.approx(1.0, abs=1e-6)
    hdr50, hdr90 = shape(r["hdr50"]), shape(r["hdr90"])
    assert hdr90.buffer(1e-4).contains(hdr50) and hdr90.covers(Point(r["map"]["lon"], r["map"]["lat"]))
    assert r["release_window"]["tau_lo_h"] <= r["map"]["tau_h"] <= r["release_window"]["tau_hi_h"]
    assert res.metrics["hdr90_area_km2"] > 0


def test_two_separate_origins_are_both_reported_never_averaged(make_ctx, tiny_config):
    ctx = make_ctx()
    IngestEngine().run(ctx)
    grid = RasterGrid.around(87.8, 13.8, 88.2, 14.2, max_cells=60)
    l_drift = np.zeros((3, grid.ny, grid.nx), dtype=np.float32)
    a, b = (grid.ny // 4, grid.nx // 4), (3 * grid.ny // 4, 3 * grid.nx // 4)
    for i in range(3):
        l_drift[i, a[0] - 2:a[0] + 3, a[1] - 2:a[1] + 3] = 1.0
        l_drift[i, b[0] - 2:b[0] + 3, b[1] - 2:b[1] + 3] = 0.8
        l_drift[i] /= l_drift[i].sum()
    with rasterio.open(ctx.path("l_drift.tif"), "w", driver="GTiff", height=grid.ny, width=grid.nx, count=3,
                       dtype="float32", crs="EPSG:4326", transform=grid.transform) as dst:
        dst.write(l_drift)
    cand = lambda i, rc: {"tau_index": i, "tau_h": i + 1, "row": rc[0], "col": rc[1], "l_fwd": 0.8, "iou": 0.7,  # noqa: E731
                          "centroid_err_m": 200.0, "area_ratio": 0.9, "orientation_match": 1.0}
    ctx.state.update({"tau_hours": [1, 2, 3], "p_age": [1 / 3] * 3, "archive_mask": [1.0] * 3,
                      "l_shape": [1.0] * 3, "drift_grid": grid.to_dict(), "l_drift_path": str(ctx.path("l_drift.tif")),
                      "verify_candidates": [cand(i, rc) for i in range(3) for rc in (a, b)]})
    PosteriorEngine().run(ctx)
    r = ctx.state["result"]
    assert r["multi_modal"] is True and len(r["modes"]) == 2
    assert isinstance(shape(r["hdr90"]), MultiPolygon)
    midpoint = Point(*[float(v) for v in grid.center((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)])
    assert not shape(r["hdr90"]).covers(midpoint)
    assert r["modes"][0]["mass"] > r["modes"][1]["mass"]


def test_age_helpers():
    taus = np.arange(1, 11)
    p = np.array([0, 0, .1, .5, .3, .1, 0, 0, 0, 0], dtype=float)
    assert hdr_interval(taus, p, 0.9) == (3, 5)          # .1 + .5 + .3 is exactly the mass asked for
    bimodal = np.array([0, .3, .05, 0, 0, 0, .05, .4, .2, 0], dtype=float)
    assert [m["tau_h"] for m in tau_modes(taus, bimodal)] == [2, 8]
