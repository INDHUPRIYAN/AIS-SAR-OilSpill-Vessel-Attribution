"""Forward drift: predicted slick extent at +6 / +12 / +24 h.

Handbook §4.3 asks for "one predicted-extent Polygon per horizon with
``{horizon_h, uncertainty_growth, ...}``". Three decisions the contract leaves open, and
what this module does about them:

**Which particles bound the extent.** All 300, and one fluke that wandered off drags the
polygon kilometres past where the oil plausibly is. Instead the cloud is trimmed to its
90% confidence region (Mahalanobis distance against the cloud covariance) before
hulling, so the polygon means "90% predicted extent" and matches the hindcast's
confidence ellipses.

**Convex or concave hull.** Concave (``shapely.concave_hull`` at a configurable ratio).
A convex hull never understates, but it fills in concavities, which badly overstates the
threatened area once shear bends the cloud. Convex is kept as the fallback when the
concave result degenerates.

**What ``uncertainty_growth`` means.** The contract writes it as "...". Here it is the
ratio of the 90% ellipse *area* at the horizon to the same area at seeding, so it starts
near 1 and reads as "the forecast is N times more uncertain by +24 h".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely import concave_hull
from shapely.geometry import MultiPoint

from ..common.geo import LocalFrame
from .cloud import _CHI2_2DF

DEFAULT_HORIZONS = (6.0, 12.0, 24.0)


@dataclass
class ForecastHorizon:
    """Predicted extent at one horizon."""

    horizon_h: float
    time_s: float
    polygon: Any = field(repr=False)
    area_km2: float = 0.0
    ellipse_area_km2: float = 0.0
    uncertainty_growth: float = 1.0
    particles_used: int = 0
    particles_total: int = 0
    hull_method: str = "concave"


def ellipse_area_m2(lons: np.ndarray, lats: np.ndarray, level: float = 0.9) -> float:
    """Area of the covariance ellipse containing ``level`` of the cloud.

    Closed form: ``pi * chi2 * sqrt(det(cov))`` - no eigen-decomposition needed, since
    the product of the semi-axes is ``chi2 * sqrt(lambda1 * lambda2)``.
    """
    if lons.size < 3:
        return 0.0
    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    x, y = frame.to_metres(lons, lats)
    cov = np.cov(np.vstack([x, y]))
    determinant = float(np.linalg.det(cov))
    if not np.isfinite(determinant) or determinant <= 0:
        return 0.0
    chi2 = _CHI2_2DF.get(round(level, 2), _CHI2_2DF[0.90])
    return math.pi * chi2 * math.sqrt(determinant)


def confidence_mask(lons: np.ndarray, lats: np.ndarray, level: float = 0.9) -> np.ndarray:
    """Boolean mask of the particles inside the ``level`` confidence region.

    Uses squared Mahalanobis distance against the cloud's own covariance, compared with
    the chi-square quantile at 2 degrees of freedom - the same statistic that defines
    the confidence ellipse, so the trimmed set and the ellipse agree by construction.
    """
    n = lons.size
    if n < 4:
        return np.ones(n, dtype=bool)

    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    x, y = frame.to_metres(lons, lats)
    points = np.vstack([x - x.mean(), y - y.mean()])
    cov = np.cov(points)

    try:
        inverse = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return np.ones(n, dtype=bool)

    distance2 = np.einsum("ij,jk,ik->i", points.T, inverse, points.T)
    mask = distance2 <= _CHI2_2DF.get(round(level, 2), _CHI2_2DF[0.90])
    # Never trim so hard that no polygon can be built.
    return mask if mask.sum() >= 4 else np.ones(n, dtype=bool)


def _polygon_area_km2(polygon, lons: np.ndarray, lats: np.ndarray) -> float:
    """Area of a lon/lat polygon, measured in a local metric frame."""
    if polygon is None or polygon.is_empty:
        return 0.0
    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    ring = np.asarray(polygon.exterior.coords)
    x, y = frame.to_metres(ring[:, 0], ring[:, 1])
    from shapely.geometry import Polygon as ShapelyPolygon

    return ShapelyPolygon(np.column_stack([x, y])).area / 1e6


def extent_polygon(
    lons: np.ndarray, lats: np.ndarray, *, ratio: float = 0.3
) -> tuple[Any, str]:
    """Hull the given points; concave if it works, convex if it does not."""
    points = MultiPoint(list(zip(lons.tolist(), lats.tolist())))
    try:
        hull = concave_hull(points, ratio=ratio)
        if hull.geom_type == "Polygon" and hull.is_valid and not hull.is_empty:
            return hull, "concave"
    except Exception:                       # noqa: BLE001 - fall back rather than fail
        pass

    convex = points.convex_hull
    if convex.geom_type != "Polygon":
        # Fewer than three distinct positions: give the cloud a nominal footprint.
        convex = convex.buffer(1e-6)
    return convex, "convex"


def build_forecast(
    run,
    *,
    horizons=DEFAULT_HORIZONS,
    level: float = 0.9,
    ratio: float = 0.3,
) -> tuple[list[ForecastHorizon], list[str]]:
    """Predicted extents at each horizon of a forward run."""
    warnings: list[str] = []
    elapsed = run.elapsed_hours()
    baseline_area = ellipse_area_m2(run.lons[0], run.lats[0], level)

    results: list[ForecastHorizon] = []
    for horizon in sorted(float(h) for h in horizons):
        index = int(np.argmin(np.abs(elapsed - horizon)))
        reached = float(elapsed[index])
        if abs(reached - horizon) > 0.5:
            warnings.append(
                f"the +{horizon:g} h horizon is outside the run (which reaches "
                f"{reached:+.1f} h) and was skipped"
            )
            continue

        lons, lats = run.lons[index], run.lats[index]
        mask = confidence_mask(lons, lats, level)
        polygon, method = extent_polygon(lons[mask], lats[mask], ratio=ratio)
        ellipse_area = ellipse_area_m2(lons, lats, level)

        results.append(
            ForecastHorizon(
                horizon_h=horizon,
                time_s=float(run.times_s[index]),
                polygon=polygon,
                area_km2=_polygon_area_km2(polygon, lons[mask], lats[mask]),
                ellipse_area_km2=ellipse_area / 1e6,
                uncertainty_growth=(
                    ellipse_area / baseline_area if baseline_area > 0 else 1.0
                ),
                particles_used=int(mask.sum()),
                particles_total=int(lons.size),
                hull_method=method,
            )
        )

    if not results:
        warnings.append("no forecast horizon fell inside the run length")
    return results, warnings
