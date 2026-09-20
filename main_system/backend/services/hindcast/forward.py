"""Forward point-release simulation.

One routine serves two callers: Stage 4 re-verification (hundreds of candidate
releases advanced together) and the synthetic demo (one known release that
manufactures the "observed" slick). Sharing it is the point: the demo proves
origin recovery only if truth and hindcast obey the same physics.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.services.hindcast.forcing import DriftParams, ForcingField
from backend.services.hindcast.integrator import random_walk, rk4_step
from backend.services.hindcast.oil import fay_variance_m2, remaining_fraction


@dataclass
class Release:
    lon: float
    lat: float
    tau_h: int          # hours before the end time at which the oil entered the water


def simulate_releases(field: ForcingField, releases: list[Release], k_end: float, n_particles: int,
                      params: DriftParams, k_diff: float, volume_m3: float, oil_type: str,
                      rng: np.random.Generator, dt_s: float = 3600.0
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Advance every release to `k_end`. Returns lon, lat of shape (n_releases, n_particles)
    and the surviving mass fraction per release.

    All releases share one clock running from the oldest release to the end; a
    release simply joins when its hour comes. Spreading is a random walk whose
    variance per step is turbulent diffusion plus the GROWTH of the Fay patch
    over that step, so a young slick is tight and an old one is broad.
    """
    n_rel = len(releases)
    tau = np.repeat(np.array([r.tau_h for r in releases], dtype=np.int64), n_particles)
    lon = np.repeat(np.array([r.lon for r in releases], dtype=np.float64), n_particles)
    lat = np.repeat(np.array([r.lat for r in releases], dtype=np.float64), n_particles)
    dk = dt_s / 3600.0
    for h in range(int(tau.max()), 0, -1):          # h = hours still to go
        active = tau >= h
        if not active.any():
            continue
        k = k_end - h * dk
        new_lon, new_lat = rk4_step(field, lon[active], lat[active], k, dk, dt_s, params)
        age_s = (tau[active] - h) * dt_s
        variance = (2.0 * k_diff * dt_s
                    + fay_variance_m2(volume_m3, age_s + dt_s, oil_type)
                    - np.where(age_s > 0, fay_variance_m2(volume_m3, age_s, oil_type), 0.0))
        lon[active], lat[active] = random_walk(new_lon, new_lat, variance, rng)
    mass = remaining_fraction(np.array([r.tau_h for r in releases], dtype=np.float64), oil_type)
    return lon.reshape(n_rel, n_particles), lat.reshape(n_rel, n_particles), mass
