"""Engine A geometry: labelled mask -> per-slick shape measurements.

Everything metric is computed in a local east/north frame anchored at each slick's own
centroid (see ``engines.common.geo``). Handbook pitfall #3 is the reason: a degree of
longitude is ~10.84 m at the demo latitude while a degree of latitude is ~11.06 m, so
measuring axes or perimeter in raw lon/lat would skew them by ~2% and would skew them
differently at every latitude.

Pixel ground area also varies down the scene, so area is accumulated **per raster row**
rather than from a single centroid-latitude figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from rasterio.features import shapes as rio_shapes
from shapely.geometry import mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from skimage.measure import label as sk_label
from skimage.measure import regionprops

from ..common.geo import LocalFrame, covariance_ellipse, pixel_area_m2, polyline_length_m

# 8-connectivity: oil slicks are wispy and frequently touch only at a corner.
# 4-connectivity would report one physical slick as several.
_CONNECTIVITY = 2


@dataclass
class SlickGeometry:
    """Shape measurements for one connected component of the mask."""

    label: int
    pixel_count: int
    area_km2: float
    perimeter_km: float
    centroid_lonlat: tuple[float, float]
    major_axis_km: float
    minor_axis_km: float
    orientation_deg: float
    polygon: Any = field(repr=False)          # shapely Polygon / MultiPolygon
    frame: LocalFrame = field(repr=False)

    @property
    def geometry_geojson(self) -> dict[str, Any]:
        return mapping(self.polygon)


def _row_latitudes(rows: np.ndarray, transform) -> np.ndarray:
    """Latitude of the centre of each given raster row."""
    return transform.f + (rows + 0.5) * transform.e


def _col_longitudes(cols: np.ndarray, transform) -> np.ndarray:
    """Longitude of the centre of each given raster column."""
    return transform.c + (cols + 0.5) * transform.a


def _area_and_centroid(rows, cols, transform) -> tuple[float, float, float]:
    """Ground area (m^2) and area-weighted centroid (lon, lat) of a pixel set.

    Pixels are grouped by row because ground area depends only on latitude - one
    ``pixel_area_m2`` evaluation per row instead of one per pixel.
    """
    px_w = abs(transform.a)
    px_h = abs(transform.e)

    unique_rows, inverse = np.unique(rows, return_inverse=True)
    row_lats = _row_latitudes(unique_rows, transform)
    row_areas = np.array(
        [pixel_area_m2(px_w, px_h, float(lat)) for lat in row_lats], dtype=float
    )

    per_pixel_area = row_areas[inverse]
    total_area = float(per_pixel_area.sum())

    lons = _col_longitudes(cols, transform)
    lats = row_lats[inverse]
    centroid_lon = float((per_pixel_area * lons).sum() / total_area)
    centroid_lat = float((per_pixel_area * lats).sum() / total_area)
    return total_area, centroid_lon, centroid_lat


def _polygonise(component: np.ndarray, transform, simplify_deg: float):
    """Vectorise one boolean component into a (possibly multi-part) shapely polygon."""
    parts = [
        shape(geom)
        for geom, value in rio_shapes(
            component.astype(np.uint8), mask=component, transform=transform
        )
        if value == 1
    ]
    if not parts:
        return None

    poly = unary_union(parts)
    if simplify_deg > 0:
        simplified = poly.simplify(simplify_deg, preserve_topology=True)
        # Guard against a tolerance so large it collapses a thin slick.
        if not simplified.is_empty and simplified.is_valid and simplified.area > 0:
            poly = simplified

    if poly.geom_type == "Polygon":
        return orient(poly, sign=1.0)          # RFC 7946: exterior CCW, holes CW
    return unary_union([orient(p, sign=1.0) for p in poly.geoms])


def _perimeter_m(poly, frame: LocalFrame) -> float:
    """Total ring length of a polygon, measured in the local metric frame.

    Interior rings (holes) count toward the perimeter - a slick with a clear patch in
    the middle genuinely has more edge than a solid one.
    """
    polys = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
    total = 0.0
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            lons, lats = np.asarray(ring.coords).T
            x, y = frame.to_metres(lons, lats)
            total += polyline_length_m(np.column_stack([x, y]))
    return total


def extract_slicks(
    mask: np.ndarray,
    transform,
    *,
    min_area_km2: float = 0.05,
    simplify_tolerance_px: float = 1.5,
) -> tuple[list[SlickGeometry], list[str]]:
    """Measure every slick in a boolean mask.

    Returns ``(slicks, warnings)`` with slicks sorted largest-area first, so
    ``slick_01`` is always the dominant feature. Components below ``min_area_km2`` are
    dropped as speckle and reported in ``warnings``.
    """
    warnings: list[str] = []
    mask = np.asarray(mask).astype(bool)
    if not mask.any():
        return [], warnings

    labelled = sk_label(mask, connectivity=_CONNECTIVITY)
    simplify_deg = simplify_tolerance_px * max(abs(transform.a), abs(transform.e))

    slicks: list[SlickGeometry] = []
    dropped = 0
    for region in regionprops(labelled):
        rows = region.coords[:, 0]
        cols = region.coords[:, 1]

        area_m2, clon, clat = _area_and_centroid(rows, cols, transform)
        area_km2 = area_m2 / 1e6
        if area_km2 < min_area_km2:
            dropped += 1
            continue

        frame = LocalFrame(clat, clon)
        lons = _col_longitudes(cols, transform)
        lats = _row_latitudes(rows, transform)
        x, y = frame.to_metres(lons, lats)
        major_m, minor_m, orientation = covariance_ellipse(x, y)

        component = labelled == region.label
        poly = _polygonise(component, transform, simplify_deg)
        if poly is None:
            warnings.append(f"component {region.label} could not be polygonised; skipped")
            continue

        slicks.append(
            SlickGeometry(
                label=int(region.label),
                pixel_count=int(region.area),
                area_km2=area_km2,
                perimeter_km=_perimeter_m(poly, frame) / 1000.0,
                centroid_lonlat=(clon, clat),
                major_axis_km=major_m / 1000.0,
                minor_axis_km=minor_m / 1000.0,
                orientation_deg=orientation,
                polygon=poly,
                frame=frame,
            )
        )

    if dropped:
        warnings.append(
            f"{dropped} component(s) below the {min_area_km2} km2 minimum area were "
            "dropped as speckle"
        )

    slicks.sort(key=lambda s: s.area_km2, reverse=True)
    return slicks, warnings
