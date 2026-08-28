"""Met-ocean grid reader and interpolator for Engine B.

Reads Keerthana's ``currents.nc`` (u/v) and ``wind.nc`` (u10/v10), validates that they
actually cover the slick in space and time, and samples them by bilinear interpolation
in space plus linear interpolation in time.

Variable naming is the project's one written coordination point (handbook §10.2). The
contract says ``u``/``v`` and ``u10``/``v10``; this reader also accepts the common CF and
provider aliases and records which name it matched, so a mismatch degrades to a warning
instead of a failed run.

Sign convention (handbook pitfall #4): u is eastward-positive, v is northward-positive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import xarray as xr

from ..common.errors import bad_grid, missing_input
from ..common.geo import km_to_deg_lat, km_to_deg_lon

LAT_ALIASES = ("lat", "latitude", "y", "nav_lat")
LON_ALIASES = ("lon", "longitude", "x", "nav_lon")
TIME_ALIASES = ("time", "t", "valid_time", "time_counter")

CURRENT_U = ("u", "uo", "water_u", "u_current", "ucur", "eastward_sea_water_velocity")
CURRENT_V = ("v", "vo", "water_v", "v_current", "vcur", "northward_sea_water_velocity")
WIND_U = ("u10", "u_10", "10u", "wind_u", "eastward_wind", "u_component_of_wind")
WIND_V = ("v10", "v_10", "10v", "wind_v", "northward_wind", "v_component_of_wind")

_EPOCH = np.datetime64("1970-01-01T00:00:00", "s")


def _pick(candidates: Iterable[str], available: Sequence[str], *, what: str,
          path: Path) -> str:
    lookup = {name.lower(): name for name in available}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    raise bad_grid(
        f"{path.name} has no {what} variable; looked for {list(candidates)}",
        path=str(path), available=list(available),
    )


def _to_epoch_seconds(values: np.ndarray) -> np.ndarray:
    """Time coordinate -> float seconds since the epoch, whatever its encoding."""
    if np.issubdtype(values.dtype, np.datetime64):
        return (values.astype("datetime64[s]") - _EPOCH).astype("float64")
    return np.asarray(values, dtype="float64")


def _index_weights(axis: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bracketing indices and interpolation weight for each value on a sorted axis.

    Values outside the axis clamp to the edge - callers validate coverage separately,
    so clamping here only ever affects the final fraction of a step.
    """
    if axis.size == 1:
        zeros = np.zeros(np.shape(values), dtype=int)
        return zeros, zeros, np.zeros(np.shape(values), dtype=float)

    i1 = np.clip(np.searchsorted(axis, values, side="right"), 1, axis.size - 1)
    i0 = i1 - 1
    span = axis[i1] - axis[i0]
    weight = np.where(span > 0, (values - axis[i0]) / np.where(span > 0, span, 1.0), 0.0)
    return i0, i1, np.clip(weight, 0.0, 1.0)


@dataclass
class VectorField:
    """One (u, v) field on a regular time/lat/lon grid, sorted ascending."""

    name: str
    times_s: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    u: np.ndarray                 # (time, lat, lon)
    v: np.ndarray
    var_names: tuple[str, str]

    def sample(self, t_s, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        """Interpolate (u, v) at the given epoch seconds and positions."""
        t_s = np.atleast_1d(np.asarray(t_s, dtype=float))
        lon = np.atleast_1d(np.asarray(lon, dtype=float))
        lat = np.atleast_1d(np.asarray(lat, dtype=float))
        if t_s.size == 1 and lon.size > 1:
            t_s = np.repeat(t_s, lon.size)

        ti0, ti1, tw = _index_weights(self.times_s, t_s)
        yi0, yi1, yw = _index_weights(self.lats, lat)
        xi0, xi1, xw = _index_weights(self.lons, lon)

        def trilinear(cube: np.ndarray) -> np.ndarray:
            c00 = cube[ti0, yi0, xi0] * (1 - xw) + cube[ti0, yi0, xi1] * xw
            c01 = cube[ti0, yi1, xi0] * (1 - xw) + cube[ti0, yi1, xi1] * xw
            c10 = cube[ti1, yi0, xi0] * (1 - xw) + cube[ti1, yi0, xi1] * xw
            c11 = cube[ti1, yi1, xi0] * (1 - xw) + cube[ti1, yi1, xi1] * xw
            at_t0 = c00 * (1 - yw) + c01 * yw
            at_t1 = c10 * (1 - yw) + c11 * yw
            return at_t0 * (1 - tw) + at_t1 * tw

        return trilinear(self.u), trilinear(self.v)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return float(self.lons.min()), float(self.lats.min()), float(
            self.lons.max()
        ), float(self.lats.max())

    @property
    def time_range_s(self) -> tuple[float, float]:
        return float(self.times_s.min()), float(self.times_s.max())

    @property
    def max_speed_m_s(self) -> float:
        """Fastest speed anywhere in the grid."""
        return float(np.nanmax(np.hypot(self.u, self.v)))

    def max_speed_in(self, bbox: tuple[float, float, float, float]) -> float:
        """Fastest speed within a sub-region of the grid.

        Sizing the drift margin from the *global* maximum is far too pessimistic: real
        current fields (and the synthetic strain field) run fastest at their edges,
        nowhere near the slick, and using that number would reject usable grids.
        """
        west, south, east, north = bbox
        rows = np.where((self.lats >= south) & (self.lats <= north))[0]
        cols = np.where((self.lons >= west) & (self.lons <= east))[0]
        if rows.size == 0 or cols.size == 0:
            return self.max_speed_m_s
        window = np.hypot(
            self.u[:, rows[0]: rows[-1] + 1, cols[0]: cols[-1] + 1],
            self.v[:, rows[0]: rows[-1] + 1, cols[0]: cols[-1] + 1],
        )
        return float(np.nanmax(window)) if window.size else self.max_speed_m_s


def load_field(
    path: str | Path, u_aliases: Sequence[str], v_aliases: Sequence[str], *, name: str
) -> tuple[VectorField, list[str]]:
    """Open a NetCDF and pull one (u, v) pair out of it.

    Raises MISSING_INPUT if the file is absent/unreadable, BAD_GRID if it lacks the
    variables or coordinates the drift engine needs.
    """
    warnings: list[str] = []
    p = Path(path)
    if not p.is_file():
        raise missing_input(f"{name} NetCDF not found: {p}", path=str(p), kind=name)

    try:
        ds = xr.open_dataset(p)
    except Exception as exc:                       # noqa: BLE001 - any reader failure
        raise bad_grid(f"{p.name} could not be opened as NetCDF: {exc}", path=str(p))

    with ds:
        variables = list(ds.variables)
        u_name = _pick(u_aliases, variables, what=f"{name} eastward (u)", path=p)
        v_name = _pick(v_aliases, variables, what=f"{name} northward (v)", path=p)
        lat_name = _pick(LAT_ALIASES, variables, what="latitude", path=p)
        lon_name = _pick(LON_ALIASES, variables, what="longitude", path=p)
        time_name = _pick(TIME_ALIASES, variables, what="time", path=p)

        if (u_name, v_name) != (u_aliases[0], v_aliases[0]):
            warnings.append(
                f"{p.name}: using '{u_name}'/'{v_name}' as the {name} components "
                f"(the contract names them '{u_aliases[0]}'/'{v_aliases[0]}')"
            )

        lats = np.asarray(ds[lat_name].values, dtype=float)
        lons = np.asarray(ds[lon_name].values, dtype=float)
        times_s = _to_epoch_seconds(np.asarray(ds[time_name].values))

        u = np.asarray(ds[u_name].values, dtype=float)
        v = np.asarray(ds[v_name].values, dtype=float)

    if lats.ndim != 1 or lons.ndim != 1:
        raise bad_grid(
            f"{p.name} has curvilinear coordinates; the drift engine needs a regular "
            "lat/lon grid",
            path=str(p),
        )
    if u.ndim != 3 or u.shape != v.shape:
        raise bad_grid(
            f"{p.name}: expected (time, lat, lon) arrays, got {u.shape} and {v.shape}",
            path=str(p),
        )

    # Normalise axis direction - NetCDFs often store latitude descending.
    if lats[0] > lats[-1]:
        lats, u, v = lats[::-1], u[:, ::-1, :], v[:, ::-1, :]
    if lons[0] > lons[-1]:
        lons, u, v = lons[::-1], u[:, :, ::-1], v[:, :, ::-1]
    if times_s.size > 1 and times_s[0] > times_s[-1]:
        order = np.argsort(times_s)
        times_s, u, v = times_s[order], u[order], v[order]

    if np.isnan(u).all() or np.isnan(v).all():
        raise bad_grid(f"{p.name}: {name} field is entirely NaN", path=str(p))

    nan_fraction = float(np.isnan(u).mean())
    if nan_fraction > 0:
        warnings.append(
            f"{p.name}: {nan_fraction:.1%} of the {name} field is NaN (land mask?); "
            "those cells are treated as zero velocity"
        )
        u = np.nan_to_num(u)
        v = np.nan_to_num(v)

    return (
        VectorField(name, times_s, lats, lons, u, v, (u_name, v_name)),
        warnings,
    )


@dataclass
class Metocean:
    """The pair of fields the drift engine integrates: currents plus wind leeway."""

    current: VectorField | None
    wind: VectorField | None
    leeway: float = 0.03

    def drift_velocity(self, t_s, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        """v = current + leeway * wind10, per handbook §2.1 / pitfall #4."""
        n = np.size(lon)
        u_total = np.zeros(n, dtype=float)
        v_total = np.zeros(n, dtype=float)

        if self.current is not None:
            cu, cv = self.current.sample(t_s, lon, lat)
            u_total += cu
            v_total += cv
        if self.wind is not None:
            wu, wv = self.wind.sample(t_s, lon, lat)
            u_total += self.leeway * wu
            v_total += self.leeway * wv
        return u_total, v_total

    def max_drift_speed_near(self, bbox: tuple[float, float, float, float]) -> float:
        """Fastest drift the fields can produce near ``bbox``: current + wind leeway."""
        speed = 0.0
        if self.current is not None:
            speed += self.current.max_speed_in(bbox)
        if self.wind is not None:
            speed += self.leeway * self.wind.max_speed_in(bbox)
        return speed

    def drift_margin_deg(
        self, hours: float, lat: float, near_bbox: tuple[float, float, float, float]
    ) -> tuple[float, float]:
        """How far particles could plausibly travel in ``hours``, in (lon, lat) degrees.

        Checking only the slick's own bbox is not enough: over a 24 h run particles
        routinely travel several times the slick's width, and a grid that stops short
        would clamp them at its edge without anyone noticing.
        """
        reach_km = self.max_drift_speed_near(near_bbox) * abs(hours) * 3600.0 / 1000.0
        return km_to_deg_lon(reach_km, lat), km_to_deg_lat(reach_km, lat)

    def outside_fraction(self, lons: np.ndarray, lats: np.ndarray) -> float:
        """Fraction of particles that left every loaded grid at any point in a run."""
        ever_outside = None
        for field in (self.current, self.wind):
            if field is None:
                continue
            west, south, east, north = field.bbox
            outside = (
                (lons < west) | (lons > east) | (lats < south) | (lats > north)
            ).any(axis=0)
            ever_outside = outside if ever_outside is None else (ever_outside | outside)
        return 0.0 if ever_outside is None else float(ever_outside.mean())

    @staticmethod
    def _covers(field, bbox: tuple[float, float, float, float]) -> bool:
        west, south, east, north = bbox
        f_west, f_south, f_east, f_north = field.bbox
        return not (west < f_west or east > f_east or south < f_south or north > f_north)

    def check_coverage(
        self,
        bbox: tuple[float, float, float, float],
        t_start_s: float,
        t_end_s: float,
        reach_bbox: tuple[float, float, float, float] | None = None,
    ) -> list[str]:
        """Verify each loaded field spans the slick and, ideally, the drift reach.

        Two levels, deliberately:

        * ``bbox`` - the slick itself. Not covering this is fatal (BAD_GRID); there is
          nothing to integrate.
        * ``reach_bbox`` - where particles could plausibly travel. Not covering this is
          only a warning: it is common with real regional products, the edge velocity is
          held, and the post-run check reports what actually left.

        Temporal shortfall is likewise a warning, with the nearest time held.
        """
        warnings: list[str] = []
        for field in (self.current, self.wind):
            if field is None:
                continue
            if not self._covers(field, bbox):
                raise bad_grid(
                    f"{field.name} grid does not cover the slick: grid bbox "
                    f"{[round(b, 3) for b in field.bbox]}, needed "
                    f"{[round(b, 3) for b in bbox]}",
                    field=field.name, grid_bbox=list(field.bbox), needed_bbox=list(bbox),
                )

            if reach_bbox is not None and not self._covers(field, reach_bbox):
                warnings.append(
                    f"the {field.name} grid covers the slick but not the full distance "
                    "particles could drift; velocities are held at the grid edge "
                    "beyond it"
                )

            t_lo, t_hi = field.time_range_s
            if t_start_s < t_lo or t_end_s > t_hi:
                if t_end_s < t_lo or t_start_s > t_hi:
                    raise bad_grid(
                        f"{field.name} grid covers no part of the drift window",
                        field=field.name,
                    )
                warnings.append(
                    f"{field.name} grid does not span the whole drift window; "
                    "the nearest available time is held at the ends"
                )
        return warnings


def load_metocean(
    currents_path: str | Path | None,
    wind_path: str | Path | None,
    *,
    leeway: float = 0.03,
) -> tuple[Metocean, list[str]]:
    """Load whichever fields are available.

    Both are optional so the engine can degrade the way the fallback register expects
    (wind-only drift, or zero-current mode) - but at least one is required, since with
    neither there is no velocity to integrate.
    """
    warnings: list[str] = []
    current = wind = None

    if currents_path:
        current, w = load_field(currents_path, CURRENT_U, CURRENT_V, name="currents")
        warnings += w
    else:
        warnings.append("no currents file supplied; running in zero-current mode")

    if wind_path:
        wind, w = load_field(wind_path, WIND_U, WIND_V, name="wind")
        warnings += w
    else:
        warnings.append("no wind file supplied; running without wind leeway")

    if current is None and wind is None:
        raise missing_input("neither a currents nor a wind file was supplied")

    return Metocean(current=current, wind=wind, leeway=leeway), warnings
