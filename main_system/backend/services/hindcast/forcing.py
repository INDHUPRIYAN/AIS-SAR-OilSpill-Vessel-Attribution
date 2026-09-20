"""The total-forcing field U(x,t) = U_curr + alpha * R(theta) * W + beta * U_stokes.

`ForcingField` holds currents, 10 m wind and Stokes drift on ONE regular
lon/lat grid at hourly steps, and samples them with its own bilinear kernel.
The kernel is the hot loop of the whole pipeline (every RK4 stage of every
ensemble member goes through it), so it gathers all six components in a single
fancy-index pass rather than calling a general-purpose interpolator six times.

alpha, theta, beta and the current-noise factor stay FREE parameters: they are
per-particle arrays at sampling time, which is what lets one call advance a
whole chunk of ensemble members at once.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import xarray as xr

from backend.services.hindcast.geo import M_PER_DEG_LAT, M_PER_DEG_LON_EQ

# index of each component in the stacked array
UC, VC, UW, VW, US, VS = range(6)

# Accepted variable names per component, in order of preference.
NAME_MAP: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "current": (("u_curr", "uo", "water_u", "u"), ("v_curr", "vo", "water_v", "v")),
    "wind": (("u_wind", "u10", "x_wind"), ("v_wind", "v10", "y_wind")),
    "stokes": (("u_stokes", "VSDX", "ustokes"), ("v_stokes", "VSDY", "vstokes")),
}
CF_NAMES = {
    "u_curr": "x_sea_water_velocity", "v_curr": "y_sea_water_velocity",
    "u_wind": "x_wind", "v_wind": "y_wind",
    "u_stokes": "sea_surface_wave_stokes_drift_x_velocity",
    "v_stokes": "sea_surface_wave_stokes_drift_y_velocity",
}


@dataclass
class DriftParams:
    """Per-particle free parameters. Scalars broadcast."""

    alpha: np.ndarray | float
    theta_deg: np.ndarray | float
    beta: np.ndarray | float
    current_scale: np.ndarray | float = 1.0


class ForcingField:
    def __init__(self, times: np.ndarray, lats: np.ndarray, lons: np.ndarray, data: np.ndarray) -> None:
        """data: float32 (n_time, 6, n_lat, n_lon); lats and lons ascending; times hourly."""
        if data.shape != (times.size, 6, lats.size, lons.size):
            raise ValueError(f"forcing array shape {data.shape} does not match its axes")
        if lats.size < 2 or lons.size < 2:
            raise ValueError("forcing grid needs at least 2x2 nodes")
        self.times = times.astype("datetime64[s]")
        self.lats, self.lons, self.data = lats.astype(np.float64), lons.astype(np.float64), data
        self._dlat = float(self.lats[1] - self.lats[0])
        self._dlon = float(self.lons[1] - self.lons[0])
        self._blend_cache: dict[float, np.ndarray] = {}
        self.clamped = 0   # samples that fell outside the grid (clamped to its edge)

    # ------------------------------------------------------------- time ---
    @property
    def n_times(self) -> int:
        return int(self.times.size)

    def index_of(self, when: datetime) -> float:
        t = np.datetime64(when.astimezone(timezone.utc).replace(tzinfo=None), "s")
        return float((t - self.times[0]) / np.timedelta64(3600, "s"))

    def time_at(self, k: float) -> datetime:
        t = self.times[0] + np.timedelta64(int(round(k * 3600)), "s")
        return datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc)

    def _slice(self, k: float) -> np.ndarray:
        k = float(np.clip(k, 0.0, self.n_times - 1))
        cached = self._blend_cache.get(k)
        if cached is not None:
            return cached
        k0 = int(np.floor(k))
        k1 = min(k0 + 1, self.n_times - 1)
        w = k - k0
        out = self.data[k0] if w == 0 else (1.0 - w) * self.data[k0] + w * self.data[k1]
        if len(self._blend_cache) > 8:
            self._blend_cache.clear()
        self._blend_cache[k] = out
        return out

    # ------------------------------------------------------------ space ---
    def sample(self, lon: np.ndarray, lat: np.ndarray, k: float) -> np.ndarray:
        """All six components at the points, shape (6, P)."""
        grid = self._slice(k)
        fx = (np.asarray(lon) - self.lons[0]) / self._dlon
        fy = (np.asarray(lat) - self.lats[0]) / self._dlat
        nx, ny = self.lons.size, self.lats.size
        outside = (fx < 0) | (fx > nx - 1) | (fy < 0) | (fy > ny - 1)
        if outside.any():
            self.clamped += int(outside.sum())
        fx = np.clip(fx, 0.0, nx - 1 - 1e-9)
        fy = np.clip(fy, 0.0, ny - 1 - 1e-9)
        x0, y0 = fx.astype(np.int64), fy.astype(np.int64)
        tx, ty = (fx - x0).astype(np.float32), (fy - y0).astype(np.float32)
        a, b = grid[:, y0, x0], grid[:, y0, x0 + 1]
        c, d = grid[:, y0 + 1, x0], grid[:, y0 + 1, x0 + 1]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    def velocity(self, lon: np.ndarray, lat: np.ndarray, k: float, p: DriftParams) -> tuple[np.ndarray, np.ndarray]:
        """Total drift velocity in m/s (east, north)."""
        s = self.sample(lon, lat, k)
        theta = np.radians(np.asarray(p.theta_deg, dtype=np.float64))
        # Wind-driven surface drift is deflected to the RIGHT of the wind in the
        # northern hemisphere (clockwise) and to the left in the southern.
        theta = np.where(np.asarray(lat) >= 0, -theta, theta)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        wu = s[UW] * cos_t - s[VW] * sin_t
        wv = s[UW] * sin_t + s[VW] * cos_t
        u = p.current_scale * s[UC] + p.alpha * wu + p.beta * s[US]
        v = p.current_scale * s[VC] + p.alpha * wv + p.beta * s[VS]
        return u, v

    def wind_at(self, lon: float, lat: float, k: float) -> tuple[float, float]:
        s = self.sample(np.array([lon]), np.array([lat]), k)
        return float(s[UW, 0]), float(s[VW, 0])

    def max_speed(self, alpha: float, beta: float) -> float:
        d = self.data
        u = d[:, UC] + alpha * d[:, UW] + beta * d[:, US]
        v = d[:, VC] + alpha * d[:, VW] + beta * d[:, VS]
        return float(np.sqrt(u * u + v * v).max())

    # -------------------------------------------------------------- I/O ---
    def to_dataset(self) -> xr.Dataset:
        names = ["u_curr", "v_curr", "u_wind", "v_wind", "u_stokes", "v_stokes"]
        ds = xr.Dataset(
            {n: (("time", "lat", "lon"), self.data[:, i]) for i, n in enumerate(names)},
            coords={"time": self.times.astype("datetime64[ns]"), "lat": self.lats, "lon": self.lons})
        for n in names:
            ds[n].attrs.update(standard_name=CF_NAMES[n], units="m s-1")
        ds["lat"].attrs.update(standard_name="latitude", units="degrees_north")
        ds["lon"].attrs.update(standard_name="longitude", units="degrees_east")
        return ds

    def save(self, path: Path) -> Path:
        """Engine-to-engine hand-off, as .npz.

        Deliberately NOT NetCDF: the HDF5 library under netCDF4 is not
        thread-safe in this process, and OceanTrace's own drift stage may be
        reading forcing grids at the same moment (that combination has taken
        the server down before). numpy's format shares no native state.
        """
        np.savez_compressed(path, times=self.times.astype("datetime64[s]").astype(np.int64),
                            lats=self.lats, lons=self.lons, data=self.data)
        return path

    @classmethod
    def load(cls, path: Path) -> "ForcingField":
        with np.load(path) as z:
            return cls(z["times"].astype("datetime64[s]"), z["lats"], z["lons"], z["data"])

    @classmethod
    def from_dataset(cls, ds: xr.Dataset) -> "ForcingField":
        names = ["u_curr", "v_curr", "u_wind", "v_wind", "u_stokes", "v_stokes"]
        data = np.stack([ds[n].values for n in names], axis=1).astype(np.float32)
        return cls(ds["time"].values, ds["lat"].values, ds["lon"].values, np.nan_to_num(data))

    def with_wind_correction(self, scale: float, rotation_deg: float) -> "ForcingField":
        """Apply the SAR-derived bias (speed scale + direction offset) to ALL wind history."""
        rot = np.radians(rotation_deg)
        data = self.data.copy()
        uw, vw = self.data[:, UW], self.data[:, VW]
        data[:, UW] = scale * (uw * np.cos(rot) - vw * np.sin(rot))
        data[:, VW] = scale * (uw * np.sin(rot) + vw * np.cos(rot))
        return ForcingField(self.times, self.lats, self.lons, data)


# --------------------------------------------------------------------------
# loading real products
# --------------------------------------------------------------------------

def _pick(ds: xr.Dataset, candidates: tuple[str, ...], what: str) -> xr.DataArray:
    for name in candidates:
        if name in ds:
            return ds[name]
    raise KeyError(f"{what}: none of {candidates} found; file has {sorted(ds.data_vars)}")


def _normalise(ds: xr.Dataset) -> xr.Dataset:
    rename = {}
    for have, want in (("latitude", "lat"), ("longitude", "lon"), ("valid_time", "time")):
        if have in ds.coords or have in ds.dims:
            rename[have] = want
    ds = ds.rename(rename)
    for dim in ("depth", "number", "expver"):
        if dim in ds.dims:
            ds = ds.isel({dim: 0}, drop=True)
    if "lon" in ds.coords and float(ds["lon"].max()) > 180.0:
        ds = ds.assign_coords(lon=((ds["lon"] + 180.0) % 360.0) - 180.0)
    return ds.sortby(["time", "lat", "lon"])


def product_extent(path: Path) -> dict:
    """Time span and lon/lat box a product file actually covers."""
    with xr.open_dataset(path) as raw:
        ds = _normalise(raw)
        return {"t0": ds["time"].values[0], "t1": ds["time"].values[-1],
                "west": float(ds["lon"].min()), "east": float(ds["lon"].max()),
                "south": float(ds["lat"].min()), "north": float(ds["lat"].max())}


def load_products(current_path: Optional[Path], wind_path: Optional[Path], stokes_path: Optional[Path],
                  t_start: datetime, t_end: datetime,
                  bbox: tuple[float, float, float, float], grid_deg: float = 0.05) -> ForcingField:
    """Read CMEMS currents, ERA5 wind and Stokes drift from local NetCDF onto one
    hourly grid covering `bbox` = (W, S, E, N). A component whose path is None
    stays zero; the caller is responsible for saying so out loud."""
    west, south, east, north = bbox
    lons = np.arange(west, east + grid_deg / 2, grid_deg)
    lats = np.arange(south, north + grid_deg / 2, grid_deg)
    t0 = np.datetime64(t_start.astimezone(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0), "s")
    n_hours = int(np.ceil((t_end - t_start).total_seconds() / 3600.0)) + 1
    times = t0 + np.arange(n_hours) * np.timedelta64(3600, "s")

    out = np.zeros((times.size, 6, lats.size, lons.size), dtype=np.float32)
    sources = (("current", current_path, (UC, VC)), ("wind", wind_path, (UW, VW)),
               ("stokes", stokes_path, (US, VS)))
    for kind, path, (iu, iv) in sources:
        if path is None:
            continue
        with xr.open_dataset(path) as raw:
            ds = _normalise(raw)
            have0, have1 = ds["time"].values[0], ds["time"].values[-1]
            if have0 > times[0] or have1 < times[-1]:
                raise ValueError(f"{kind} file {path.name} covers {have0}..{have1}, "
                                 f"need {times[0]}..{times[-1]}")
            u_name, v_name = NAME_MAP[kind]
            target = {"time": times.astype("datetime64[ns]"), "lat": lats, "lon": lons}
            out[:, iu] = _pick(ds, u_name, kind).interp(target).values
            out[:, iv] = _pick(ds, v_name, kind).interp(target).values
    # Land cells in an ocean product are NaN; a particle that reaches one feels
    # no current there, which is the least-wrong thing to assume.
    return ForcingField(times, lats, lons, np.nan_to_num(out))


# --------------------------------------------------------------------------
# synthetic fields (demo + tests)
# --------------------------------------------------------------------------

def synthetic_field(t_start: datetime, n_hours: int, center: tuple[float, float],
                    half_size_deg: float = 1.2, grid_deg: float = 0.05,
                    current: tuple[float, float] = (0.12, 0.05),
                    wind: tuple[float, float] = (6.0, 2.0),
                    wind_rotation_deg_per_h: float = 0.6,
                    eddy_strength: float = 0.06, stokes_fraction: float = 0.012,
                    t_ref: Optional[datetime] = None) -> ForcingField:
    """A smooth, non-trivial field: mean current + a slow eddy, a wind that veers
    steadily, and Stokes drift aligned with the wind.

    Deterministic in ABSOLUTE time: the phase is measured from `t_ref`, so two
    windows cut from the same spec agree wherever they overlap. The demo relies
    on that -- the truth run and the hindcast load different windows."""
    lon0, lat0 = center
    lons = np.arange(lon0 - half_size_deg, lon0 + half_size_deg + grid_deg / 2, grid_deg)
    lats = np.arange(lat0 - half_size_deg, lat0 + half_size_deg + grid_deg / 2, grid_deg)
    t0 = np.datetime64(t_start.astimezone(timezone.utc).replace(tzinfo=None), "s")
    times = t0 + np.arange(n_hours) * np.timedelta64(3600, "s")
    gx, gy = np.meshgrid((lons - lon0) / half_size_deg, (lats - lat0) / half_size_deg)
    data = np.zeros((n_hours, 6, lats.size, lons.size), dtype=np.float32)
    envelope = np.exp(-(gx * gx + gy * gy))
    ref = t0 if t_ref is None else np.datetime64(t_ref.astimezone(timezone.utc).replace(tzinfo=None), "s")
    hours_abs = (times - ref) / np.timedelta64(3600, "s")
    for k in range(n_hours):
        h = float(hours_abs[k])
        phase = 2 * np.pi * h / 72.0
        data[k, UC] = current[0] + eddy_strength * (-gy) * envelope * np.cos(phase)
        data[k, VC] = current[1] + eddy_strength * gx * envelope * np.cos(phase)
        rot = np.radians(wind_rotation_deg_per_h * h)
        wu = wind[0] * np.cos(rot) - wind[1] * np.sin(rot)
        wv = wind[0] * np.sin(rot) + wind[1] * np.cos(rot)
        gust = 1.0 + 0.15 * np.sin(phase * 3.0 + gx)
        data[k, UW], data[k, VW] = wu * gust, wv * gust
        data[k, US], data[k, VS] = stokes_fraction * wu * gust, stokes_fraction * wv * gust
    return ForcingField(times, lats, lons, data)


def metres_to_degrees(dx_m: np.ndarray, dy_m: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cos_lat = np.maximum(np.cos(np.radians(lat)), 0.05)
    return dx_m / (M_PER_DEG_LON_EQ * cos_lat), dy_m / M_PER_DEG_LAT
