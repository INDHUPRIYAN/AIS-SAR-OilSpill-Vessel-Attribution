"""STAND-IN for Nandha's Engine A (characterisation). Not the real thing.

Nandha owns the characterisation maths. This exists only so the POC pipeline is
*coherent*: without it the demo would show a detected mask and then a completely
unrelated slick polygon from the static mock, which looks broken to anyone
watching closely.

It computes the geometry that follows directly from the mask -- area, perimeter,
centroid, best-fit ellipse, orientation -- plus a damping ratio measured against
the surrounding sea. It deliberately does NOT attempt the Fay spreading-law age
estimate, which is genuinely Nandha's domain; `age_hours_estimate` is emitted as
null with a low confidence, which the contract permits and which is more honest
than a number nobody has validated.

When Nandha's Engine A lands, the orchestrator calls his CLI instead and this
module stops being used. Output validates against the same `SlickCollection`
contract either way.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np


def _ring_background_db(db: np.ndarray, blob: np.ndarray, valid: np.ndarray,
                        dilation: int = 12) -> Optional[float]:
    """Mean dB in a ring just outside the slick -- the local sea reference.

    A ring rather than the whole scene, because Sigma0 varies across a scene
    with wind and incidence angle; comparing a slick against distant water
    would measure that gradient instead of the damping.
    """
    from skimage.morphology import binary_dilation, disk

    grown = binary_dilation(blob, disk(dilation))
    ring = grown & ~binary_dilation(blob, disk(2)) & valid
    if ring.sum() < 50:
        return None
    return float(np.mean(db[ring]))


def _pixel_area_km2(profile, n_pixels: int, centre_lat: float) -> float:
    """Convert a pixel count to km2, accounting for latitude.

    Degrees are not metres: at 13 deg N a degree of longitude is ~108 km, not
    111 km, and ignoring the cosine inflates every area estimate.
    """
    transform = profile["transform"]
    import rasterio
    crs = profile.get("crs")
    is_geographic = crs is None or rasterio.crs.CRS.from_user_input(crs).is_geographic

    if is_geographic:
        deg_x, deg_y = abs(transform.a), abs(transform.e)
        m_x = deg_x * 111_320.0 * math.cos(math.radians(centre_lat))
        m_y = deg_y * 110_540.0
    else:
        m_x, m_y = abs(transform.a), abs(transform.e)
    return n_pixels * m_x * m_y / 1e6


def _pixel_len_m(profile, centre_lat: float) -> float:
    """Approximate ground length of one pixel, for axis lengths."""
    import rasterio
    transform = profile["transform"]
    crs = profile.get("crs")
    if crs is None or rasterio.crs.CRS.from_user_input(crs).is_geographic:
        m_x = abs(transform.a) * 111_320.0 * math.cos(math.radians(centre_lat))
        m_y = abs(transform.e) * 110_540.0
        return (m_x + m_y) / 2.0
    return (abs(transform.a) + abs(transform.e)) / 2.0


def _polygon_lonlat(blob: np.ndarray, profile, simplify_px: float = 2.0):
    """Trace the blob boundary and convert to WGS84 lon/lat ring."""
    import rasterio
    from rasterio.warp import transform as warp_transform
    from skimage.measure import find_contours

    contours = find_contours(blob.astype(float), 0.5)
    if not contours:
        return None
    contour = max(contours, key=len)  # outer boundary

    # Douglas-Peucker in pixel space keeps the polygon light for the UI without
    # visibly changing its shape.
    try:
        from shapely.geometry import Polygon
        poly = Polygon([(c, r) for r, c in contour])
        if poly.is_valid and poly.area > 0:
            poly = poly.simplify(simplify_px, preserve_topology=True)
            cols, rows = poly.exterior.coords.xy
        else:
            rows, cols = contour[:, 0], contour[:, 1]
    except Exception:
        rows, cols = contour[:, 0], contour[:, 1]

    tr = profile["transform"]
    xs, ys = [], []
    for c, r in zip(cols, rows):
        x, y = tr * (c, r)
        xs.append(x)
        ys.append(y)

    crs = profile.get("crs")
    if crs is not None and rasterio.crs.CRS.from_user_input(crs).to_epsg() != 4326:
        xs, ys = warp_transform(crs, "EPSG:4326", xs, ys)

    ring = [[round(float(x), 6), round(float(y), 6)] for x, y in zip(xs, ys)]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else None


def characterise(mask: np.ndarray, db: np.ndarray, valid: np.ndarray, profile,
                 scene_id: str, acquired_utc: datetime, model_version: str,
                 engine: str, mask_path: str, min_area_px: int = 200) -> dict:
    """mask + scene -> a dict matching the SlickCollection contract."""
    from skimage.measure import label, regionprops

    features: List[dict] = []
    labels = label(mask.astype(bool))

    for idx, r in enumerate(regionprops(labels), start=1):
        if r.area < min_area_px:
            continue
        blob = labels == r.label

        row, col = r.centroid
        tr = profile["transform"]
        cx, cy = tr * (col, row)
        import rasterio
        crs = profile.get("crs")
        if crs is not None and rasterio.crs.CRS.from_user_input(crs).to_epsg() != 4326:
            from rasterio.warp import transform as warp_transform
            (cx,), (cy,) = warp_transform(crs, "EPSG:4326", [cx], [cy])
        centroid = [round(float(cx), 6), round(float(cy), 6)]

        ring = _polygon_lonlat(blob, profile)
        if ring is None:
            continue

        px_m = _pixel_len_m(profile, centroid[1])
        area_km2 = _pixel_area_km2(profile, int(r.area), centroid[1])
        perimeter_km = float(r.perimeter) * px_m / 1000.0

        # regionprops `orientation` is radians CCW from the ROW axis, in
        # [-pi/2, pi/2]. In a north-up raster the row axis already points
        # north-south, so orientation 0 is already a bearing of 0. The only
        # correction needed is the sign: image rows increase downward, so a
        # counter-clockwise angle in (row, col) space is clockwise on the
        # ground. Negate, then wrap into the contract's [0, 180).
        #
        # Getting this wrong by the intuitive `90 - angle` puts every slick 90
        # degrees off, which silently inverts the trajectory-correlation factor
        # in attribution -- it would score the wrong vessels as suspects.
        # Verified against contracts/mocks/slick.geojson: declared bearing 62.0.
        bearing = (-math.degrees(r.orientation)) % 180.0

        bg = _ring_background_db(db, blob, valid)
        damping = round(float(bg - np.mean(db[blob])), 3) if bg is not None else None

        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "slick_id": f"{scene_id}_slick_{idx:02d}",
                "confidence": 0.0,          # filled in by the caller from /detect
                "area_km2": round(area_km2, 4),
                "perimeter_km": round(perimeter_km, 4),
                "centroid": centroid,
                "major_axis_m": round(float(r.axis_major_length) * px_m, 2),
                "minor_axis_m": round(float(r.axis_minor_length) * px_m, 2),
                "orientation_deg": round(bearing, 2),
                "damping_ratio": damping,
                # Fay spreading-law age is Nandha's to implement; emitting null
                # is honest, emitting an unvalidated number is not.
                "age_hours_estimate": None,
                "age_confidence": None,
                "engine": engine,
                "source": "real",
            },
        })

    features.sort(key=lambda f: f["properties"]["area_km2"], reverse=True)
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": scene_id,
            "detected_utc": (acquired_utc + timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "acquired_utc": acquired_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model_version": model_version,
            "mask_path": mask_path,
            "crs": "EPSG:4326",
        },
        "features": features,
    }
