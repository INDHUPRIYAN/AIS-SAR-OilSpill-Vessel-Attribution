"""Generate Engine B's mock met-ocean input: tiny CF-style NetCDF grids.

Handbook §5.4 / §8: "synthetic uniform and rotating current fields (with a constant
current the backtracked origin is hand-computable - analytic ground truth)". This
generator provides exactly that, plus deliberately broken files for the BAD_GRID tests.

Variable names follow the frozen contract (``u``/``v`` for currents, ``u10``/``v10`` for
wind). Keerthana's real files may use CF long names instead; the reader accepts the
usual aliases, so the one written coordination point cannot silently break the engine.

Run:  python -m tests.fixtures.make_metocean [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xarray as xr

from engines.common.geo import m_per_deg_lat, m_per_deg_lon

# Window around the demo scene (Chennai / Ennore), wide enough for a 24 h backtrack.
LON_MIN, LON_MAX = 80.00, 80.70
LAT_MIN, LAT_MAX = 12.70, 13.40
GRID_STEP_DEG = 0.02

TIME_START = np.datetime64("2017-02-01T00:00:00")
TIME_END = np.datetime64("2017-02-02T06:00:00")
TIME_STEP_H = 1

# Uniform field: a steady south-westward-ish set, chosen so a 24 h backtrack stays
# inside the grid. Eastward/northward positive, m/s.
UNIFORM_U = 0.18
UNIFORM_V = -0.09
UNIFORM_WIND_U = 4.0
UNIFORM_WIND_V = 2.0

# Rotating field: solid-body rotation (an eddy) about the grid centre, rad/s.
# Note this is a *rigid* rotation - it deforms nothing, so a backtracked cloud keeps
# its shape. Useful for exercising spatial interpolation, useless for convergence.
EDDY_OMEGA = 6.0e-6
EDDY_LON = 80.35
EDDY_LAT = 13.05

# Strain field: a pure saddle, stretching along x and compressing along y (s^-1).
# Unlike the uniform and eddy fields this genuinely deforms a cloud, which is what a
# backtracked slick needs in order to converge toward a release point.
STRAIN_RATE = 1.2e-5
STRAIN_LON = 80.35
STRAIN_LAT = 13.05


def _axes() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lons = np.arange(LON_MIN, LON_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG)
    lats = np.arange(LAT_MIN, LAT_MAX + GRID_STEP_DEG / 2, GRID_STEP_DEG)
    times = np.arange(TIME_START, TIME_END + np.timedelta64(1, "h"),
                      np.timedelta64(TIME_STEP_H, "h"))
    return times, lats, lons


def _dataset(times, lats, lons, u, v, names: tuple[str, str], units: str) -> xr.Dataset:
    u_name, v_name = names
    return xr.Dataset(
        {
            u_name: (("time", "lat", "lon"), u.astype("float32"),
                     {"units": units, "standard_name": f"eastward_{units and 'velocity'}"}),
            v_name: (("time", "lat", "lon"), v.astype("float32"),
                     {"units": units, "standard_name": f"northward_{units and 'velocity'}"}),
        },
        coords={
            "time": times,
            "lat": ("lat", lats, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", lons, {"units": "degrees_east", "standard_name": "longitude"}),
        },
        attrs={"title": "OceanTrace synthetic met-ocean mock", "synthetic": "true"},
    )


def _uniform(shape, value) -> np.ndarray:
    return np.full(shape, value, dtype="float32")


def _eddy(times, lats, lons) -> tuple[np.ndarray, np.ndarray]:
    """Solid-body rotation about (EDDY_LON, EDDY_LAT): u = -omega*dy, v = +omega*dx.

    Constant in time, but varying in space - which is what exercises the reader's
    bilinear interpolation (a uniform field would pass even a broken interpolator).
    """
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    dx = (lon_grid - EDDY_LON) * m_per_deg_lon(EDDY_LAT)
    dy = (lat_grid - EDDY_LAT) * m_per_deg_lat(EDDY_LAT)
    u2d = -EDDY_OMEGA * dy
    v2d = EDDY_OMEGA * dx
    return (
        np.repeat(u2d[None, :, :], len(times), axis=0).astype("float32"),
        np.repeat(v2d[None, :, :], len(times), axis=0).astype("float32"),
    )


def _strain(times, lats, lons) -> tuple[np.ndarray, np.ndarray]:
    """Pure deformation (saddle): u = +a*dx, v = -a*dy. Area-preserving, shape-changing."""
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    dx = (lon_grid - STRAIN_LON) * m_per_deg_lon(STRAIN_LAT)
    dy = (lat_grid - STRAIN_LAT) * m_per_deg_lat(STRAIN_LAT)
    u2d = STRAIN_RATE * dx
    v2d = -STRAIN_RATE * dy
    return (
        np.repeat(u2d[None, :, :], len(times), axis=0).astype("float32"),
        np.repeat(v2d[None, :, :], len(times), axis=0).astype("float32"),
    )


def build_metocean(out_dir: Path) -> dict:
    """Write the uniform, rotating and broken NetCDFs; return their ground truth."""
    out_dir.mkdir(parents=True, exist_ok=True)
    times, lats, lons = _axes()
    shape = (len(times), len(lats), len(lons))

    # --- uniform current + wind: the analytic ground-truth pair ---------------------
    currents = _dataset(times, lats, lons,
                        _uniform(shape, UNIFORM_U), _uniform(shape, UNIFORM_V),
                        ("u", "v"), "m s-1")
    currents.to_netcdf(out_dir / "currents_uniform.nc")

    wind = _dataset(times, lats, lons,
                    _uniform(shape, UNIFORM_WIND_U), _uniform(shape, UNIFORM_WIND_V),
                    ("u10", "v10"), "m s-1")
    wind.to_netcdf(out_dir / "wind_uniform.nc")

    # --- zero wind, for tests that want current-only motion ------------------------
    _dataset(times, lats, lons, _uniform(shape, 0.0), _uniform(shape, 0.0),
             ("u10", "v10"), "m s-1").to_netcdf(out_dir / "wind_zero.nc")

    # --- rotating (eddy) current field ---------------------------------------------
    eddy_u, eddy_v = _eddy(times, lats, lons)
    _dataset(times, lats, lons, eddy_u, eddy_v, ("u", "v"), "m s-1").to_netcdf(
        out_dir / "currents_eddy.nc"
    )

    # --- deforming (strain) current field ------------------------------------------
    strain_u, strain_v = _strain(times, lats, lons)
    _dataset(times, lats, lons, strain_u, strain_v, ("u", "v"), "m s-1").to_netcdf(
        out_dir / "currents_strain.nc"
    )

    # --- broken files for the BAD_GRID tests ---------------------------------------
    # 1. missing the 'v' variable entirely
    currents.drop_vars("v").to_netcdf(out_dir / "currents_missing_v.nc")
    # 2. a grid that covers a different ocean altogether
    elsewhere = _dataset(
        times, lats - 30.0, lons - 40.0,
        _uniform(shape, UNIFORM_U), _uniform(shape, UNIFORM_V), ("u", "v"), "m s-1"
    )
    elsewhere.to_netcdf(out_dir / "currents_wrong_region.nc")

    truth = {
        "currents_uniform": str(out_dir / "currents_uniform.nc"),
        "wind_uniform": str(out_dir / "wind_uniform.nc"),
        "wind_zero": str(out_dir / "wind_zero.nc"),
        "currents_eddy": str(out_dir / "currents_eddy.nc"),
        "currents_strain": str(out_dir / "currents_strain.nc"),
        "currents_missing_v": str(out_dir / "currents_missing_v.nc"),
        "currents_wrong_region": str(out_dir / "currents_wrong_region.nc"),
        "uniform": {
            "current_u": UNIFORM_U, "current_v": UNIFORM_V,
            "wind_u": UNIFORM_WIND_U, "wind_v": UNIFORM_WIND_V,
        },
        "eddy": {"omega": EDDY_OMEGA, "lon": EDDY_LON, "lat": EDDY_LAT},
        "strain": {"rate": STRAIN_RATE, "lon": STRAIN_LON, "lat": STRAIN_LAT},
        "bbox": [LON_MIN, LAT_MIN, LON_MAX, LAT_MAX],
        "time_start": str(TIME_START), "time_end": str(TIME_END),
    }
    (out_dir / "metocean_truth.json").write_text(
        json.dumps(truth, indent=2) + "\n", encoding="utf-8"
    )
    return truth


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Engine B met-ocean mocks")
    parser.add_argument("--out-dir", default="tests/fixtures/data", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_metocean(args.out_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
