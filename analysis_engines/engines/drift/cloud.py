"""Turn a raw particle run into the probability cloud the contract asks for.

Handbook pitfall #5: "never output a single origin point - always the weighted cloud +
ellipse + time window". This module produces all three:

* per-particle **weights** from the local cloud density, so the UI heatmap and Engine
  C's density-weighted proximity factor have something to work with;
* a per-timestep **confidence ellipse** at a configurable level (0.9 by default);
* the **origin window** (start / peak / end), derived from where the backtracked cloud
  is most concentrated.

Why concentration marks the origin
----------------------------------
Backtracked particles converge as you rewind toward the moment of release - but only if
the flow *deforms* the cloud. Uniform translation preserves its shape exactly, and so
does a rigid rotation, so under either there is no minimum to find and no way to
localise the release time from the drift alone. That is not a bug in the engine: an
elongated slick under a uniform current genuinely implies a *moving* source, so the
origin is a track in space-time rather than a point, and it is Engine C's job to
resolve which vessel track it was. The degenerate case is reported honestly - the whole
run becomes the window, with Engine A's age estimate (itself low-confidence) used only
to mark a nominal peak.

Diffusion is removed before the search. A random walk inflates cloud spread as sqrt(t)
regardless of the flow, which would otherwise swamp the deformation signal entirely and
place the minimum at the detection time on every single run. Since the engine adds that
noise itself, its variance is known exactly (2*K*t per component, so 4*K*t across both)
and is subtracted analytically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..common.geo import LocalFrame

# Chi-square quantiles with 2 degrees of freedom, for covariance-ellipse scaling.
_CHI2_2DF = {0.50: 1.3863, 0.68: 2.2789, 0.90: 4.6052, 0.95: 5.9915, 0.99: 9.2103}

# Below this relative variation in cloud spread there is no usable convergence signal.
FLAT_SPREAD_THRESHOLD = 0.05


@dataclass(frozen=True)
class TimestepCloud:
    """The particle cloud at one instant of the run."""

    time_s: float
    elapsed_h: float
    lons: np.ndarray
    lats: np.ndarray
    weights: np.ndarray
    centroid: tuple[float, float]
    spread_m: float
    ellipse: list[tuple[float, float]]      # closed lon/lat ring


@dataclass(frozen=True)
class OriginWindow:
    start_s: float
    peak_s: float
    end_s: float
    method: str


def density_weights(lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Per-particle weight in [0, 1] from local cloud density.

    A Gaussian kernel estimate normalised so the densest particle scores 1.0. Equal
    weights are returned when the cloud is degenerate (all particles coincident, or
    fewer than three of them), which keeps the contract's 0-1 range valid either way.
    """
    n = lons.size
    if n < 3:
        return np.ones(n)

    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    x, y = frame.to_metres(lons, lats)
    if np.allclose(x, x[0]) and np.allclose(y, y[0]):
        return np.ones(n)

    try:
        from scipy.stats import gaussian_kde

        density = gaussian_kde(np.vstack([x, y]))(np.vstack([x, y]))
    except Exception:                              # noqa: BLE001 - singular covariance
        return np.ones(n)

    peak = float(density.max())
    return np.ones(n) if peak <= 0 else np.clip(density / peak, 0.0, 1.0)


def confidence_ellipse(
    lons: np.ndarray, lats: np.ndarray, level: float = 0.9, points: int = 64
) -> list[tuple[float, float]]:
    """Closed lon/lat ring of the covariance ellipse containing ``level`` of the cloud.

    Semi-axes are ``sqrt(chi2(level, 2df) * eigenvalue)`` - the statistical convention,
    deliberately *not* the ``4*sqrt(eigenvalue)`` shape convention Engine A uses for a
    best-fit ellipse. The two answer different questions.
    """
    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    x, y = frame.to_metres(lons, lats)

    if x.size < 3:
        return []
    cov = np.cov(np.vstack([x, y]))
    if not np.all(np.isfinite(cov)):
        return []

    eigvals, eigvecs = np.linalg.eigh(cov)
    eigvals = np.clip(eigvals, 0.0, None)
    scale = math.sqrt(_CHI2_2DF.get(round(level, 2), _CHI2_2DF[0.90]))

    theta = np.linspace(0.0, 2.0 * math.pi, points, endpoint=False)
    unit = np.vstack([np.cos(theta), np.sin(theta)])
    axes = scale * np.sqrt(eigvals)
    ring = (eigvecs @ (unit * axes[:, None])) + np.array(
        [[float(np.mean(x))], [float(np.mean(y))]]
    )

    ring_lon, ring_lat = frame.to_lonlat(ring[0], ring[1])
    coords = [(round(float(lo), 6), round(float(la), 6)) for lo, la in zip(ring_lon, ring_lat)]
    coords.append(coords[0])                      # close the ring
    return coords


def advective_spread_m(spread_m: float, elapsed_h: float, diffusion_m2_s: float) -> float:
    """Strip the known diffusive variance out of a measured cloud spread.

    ``spread_m`` is an RMS radius, so its square is ``var_x + var_y``; a random walk
    with coefficient K adds ``2*K*t`` to each component. What remains is the spread the
    flow alone is responsible for.
    """
    if diffusion_m2_s <= 0:
        return spread_m
    diffusive_var = 4.0 * diffusion_m2_s * abs(elapsed_h) * 3600.0
    return math.sqrt(max(spread_m**2 - diffusive_var, 0.0))


def _spread_m(lons: np.ndarray, lats: np.ndarray) -> float:
    """RMS distance of particles from their centroid, in metres."""
    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    x, y = frame.to_metres(lons, lats)
    return float(np.sqrt(np.mean((x - x.mean()) ** 2 + (y - y.mean()) ** 2)))


def build_clouds(
    run, *, level: float = 0.9, every_h: float = 1.0
) -> list[TimestepCloud]:
    """Summarise a DriftRun at ``every_h`` intervals (plus its first and last step)."""
    elapsed = run.elapsed_hours()
    keep = {0, len(elapsed) - 1}
    if every_h > 0:
        target = 0.0
        for index, hours in enumerate(np.abs(elapsed)):
            if hours + 1e-9 >= target:
                keep.add(index)
                target += every_h

    clouds: list[TimestepCloud] = []
    for index in sorted(keep):
        lons, lats = run.lons[index], run.lats[index]
        clouds.append(
            TimestepCloud(
                time_s=float(run.times_s[index]),
                elapsed_h=round(float(elapsed[index]), 3),
                lons=lons,
                lats=lats,
                weights=density_weights(lons, lats),
                centroid=(float(np.mean(lons)), float(np.mean(lats))),
                spread_m=_spread_m(lons, lats),
                ellipse=confidence_ellipse(lons, lats, level),
            )
        )
    return clouds


def origin_window(
    clouds: list[TimestepCloud],
    *,
    age_hours_est: float | None = None,
    window_fraction: float = 0.10,
    diffusion_m2_s: float = 0.0,
) -> tuple[OriginWindow, list[str]]:
    """Locate the probable release time from where the backtracked cloud converges.

    Returns the window plus any warnings explaining a degraded derivation.
    """
    warnings: list[str] = []
    spreads = np.array(
        [advective_spread_m(c.spread_m, c.elapsed_h, diffusion_m2_s) for c in clouds],
        dtype=float,
    )
    times = np.array([c.time_s for c in clouds], dtype=float)

    floor = float(spreads.min())
    relative = (spreads - floor) / floor if floor > 0 else np.zeros_like(spreads)

    if relative.max() < FLAT_SPREAD_THRESHOLD:
        # Uniform advection: the cloud translates without changing shape, so there is
        # no convergence minimum to find. Say so rather than reporting a false peak.
        if age_hours_est is not None:
            target = times[0] - abs(age_hours_est) * 3600.0
            peak_index = int(np.argmin(np.abs(times - target)))
            method = "age_estimate"
            warnings.append(
                "the current field does not deform the cloud (uniform translation or "
                "rigid rotation), so the drift alone cannot localise the release time; "
                "the whole run is reported as the origin window and the peak falls "
                f"back to Engine A's age estimate ({age_hours_est:.1f} h), which is "
                "itself low-confidence"
            )
        else:
            peak_index = len(times) // 2
            method = "midpoint"
            warnings.append(
                "the current field does not deform the cloud and no age estimate was "
                "supplied; the whole run is reported as the origin window and the "
                "nominal peak is its midpoint, carrying no information"
            )
        # Without a convergence signal the window is the whole run: honest, if wide.
        lo, hi = 0, len(times) - 1
    else:
        peak_index = int(np.argmin(spreads))
        method = "cloud_convergence"
        within = relative <= window_fraction
        lo = hi = peak_index
        while lo > 0 and within[lo - 1]:
            lo -= 1
        while hi < len(times) - 1 and within[hi + 1]:
            hi += 1

    start_s, end_s = sorted((float(times[lo]), float(times[hi])))
    return OriginWindow(start_s, float(times[peak_index]), end_s, method), warnings
