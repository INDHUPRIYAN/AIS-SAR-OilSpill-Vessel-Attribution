"""The guaranteed drift path: an in-house Euler particle integrator.

Handbook pitfall #2 - "write the Euler fallback FIRST. It is your development harness,
your guaranteed demo path, and your sanity check on OpenDrift." It depends on nothing
but numpy: no OpenDrift, no GDAL, no network.

Physics, per handbook §2.1:

    v = current(x, t) + 0.03 * wind10(x, t)          leeway is 3% of the 10 m wind
    x <- x + v * dt * direction                      direction = -1 backward, +1 forward
    x <- x + N(0, sqrt(2 K dt))                      Gaussian turbulent diffusion

Two sign traps this file is careful about (pitfall #4):

* u is eastward-positive and v northward-positive, and a backward run negates the
  *timestep*, not the velocity field.
* Metres become degrees using the latitude cosine, re-evaluated per particle per step,
  never a single global factor (pitfall #3).

Diffusion is deliberately *not* negated on a backward run: a random walk is not
reversible, so a forward-then-backward round trip only returns to its start when the
diffusion coefficient is zero. The round-trip test asserts exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely import contains_xy
from shapely.geometry.base import BaseGeometry

from ..common.geo import m_per_deg_lat, m_per_deg_lon

BACKWARD = -1
FORWARD = 1


@dataclass
class DriftRun:
    """Particle positions through time.

    ``times_s`` is epoch seconds, ascending for a forward run and descending for a
    backward one - index 0 is always the seeded state at detection time.
    ``lons``/``lats`` are (steps + 1, particles).
    """

    times_s: np.ndarray
    lons: np.ndarray
    lats: np.ndarray
    direction: int
    engine: str = "euler"

    @property
    def n_particles(self) -> int:
        return self.lons.shape[1]

    @property
    def n_steps(self) -> int:
        return self.lons.shape[0] - 1

    def elapsed_hours(self) -> np.ndarray:
        """Signed hours from seeding: negative going backward, positive going forward."""
        return (self.times_s - self.times_s[0]) / 3600.0


def seed_particles(
    polygon: BaseGeometry, count: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Scatter ``count`` particles uniformly inside a polygon (rejection sampling).

    Uniform *in area*, so a long thin slick is sampled along its whole length rather
    than clustered at the centroid.
    """
    west, south, east, north = polygon.bounds
    lons = np.empty(0)
    lats = np.empty(0)

    # Rejection sampling; the batch factor keeps the loop to one or two passes even for
    # a sliver-shaped slick whose bounding box is mostly empty.
    while lons.size < count:
        batch = max(count * 4, 1024)
        cand_lon = rng.uniform(west, east, batch)
        cand_lat = rng.uniform(south, north, batch)
        inside = contains_xy(polygon, cand_lon, cand_lat)
        lons = np.concatenate([lons, cand_lon[inside]])
        lats = np.concatenate([lats, cand_lat[inside]])

    return lons[:count].copy(), lats[:count].copy()


def run_euler(
    seed_lons: np.ndarray,
    seed_lats: np.ndarray,
    metocean,
    start_time_s: float,
    *,
    hours: float,
    dt_seconds: float = 600.0,
    direction: int = BACKWARD,
    diffusion_m2_s: float = 5.0,
    rng: np.random.Generator | None = None,
) -> DriftRun:
    """Integrate particles for ``hours``, returning every intermediate position.

    ``direction`` is -1 for a hindcast (backward in time) and +1 for a forecast.
    """
    if direction not in (BACKWARD, FORWARD):
        raise ValueError("direction must be -1 (backward) or +1 (forward)")
    if hours <= 0:
        raise ValueError("hours must be positive; use direction to go backward")

    rng = rng or np.random.default_rng()
    steps = max(int(round(hours * 3600.0 / dt_seconds)), 1)

    lons = np.asarray(seed_lons, dtype=float).copy()
    lats = np.asarray(seed_lats, dtype=float).copy()
    n = lons.size

    out_lons = np.empty((steps + 1, n))
    out_lats = np.empty((steps + 1, n))
    out_times = np.empty(steps + 1)
    out_lons[0], out_lats[0], out_times[0] = lons, lats, start_time_s

    # One-sigma random-walk displacement per step, per component.
    sigma_m = float(np.sqrt(2.0 * diffusion_m2_s * dt_seconds)) if diffusion_m2_s else 0.0
    t_s = start_time_s

    for step in range(1, steps + 1):
        u, v = metocean.drift_velocity(t_s, lons, lats)

        dx_m = u * dt_seconds * direction
        dy_m = v * dt_seconds * direction
        if sigma_m:
            dx_m = dx_m + rng.normal(0.0, sigma_m, n)
            dy_m = dy_m + rng.normal(0.0, sigma_m, n)

        # Per-particle latitude conversion: a degree of longitude is not a degree of
        # latitude, and neither is constant across the cloud.
        lats = lats + dy_m / np.array([m_per_deg_lat(la) for la in lats])
        lons = lons + dx_m / np.array([m_per_deg_lon(la) for la in lats])

        t_s = t_s + dt_seconds * direction
        out_lons[step], out_lats[step], out_times[step] = lons, lats, t_s

    return DriftRun(out_times, out_lons, out_lats, direction)
