"""Geodesy helpers shared by all three engines.

Handbook pitfall #3: *degrees are not metres*. A degree of longitude shrinks with the
cosine of latitude while a degree of latitude barely changes, so every geometric
quantity (area, perimeter, ellipse axes) must be computed in a metric frame, never in
raw lon/lat. Everything here exists to make that easy and to make it the default.

Approach: a local tangent-plane ("equirectangular") frame anchored at the feature's
own centroid. Over a slick a few tens of km across the distortion is far below the
uncertainty of the mask itself, and it avoids dragging a UTM-zone dependency into
every engine.

Orientation convention (frozen for this project, since no handbook section fixes it):

    orientation_deg = bearing of the ellipse major axis from TRUE NORTH,
                      degrees clockwise, folded to [0, 180).

North-south major axis -> 0; north-east -> 45; east-west -> 90. The axis is undirected,
hence the fold at 180. This matches AIS COG/heading, which are also compass bearings
from north, so Engine C's trajectory factor can subtract the two directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# WGS84 degree lengths (metres) as a function of latitude - standard series expansion,
# accurate to well under a metre, and cheaper than a full geodesic solve per pixel.
_LAT_SERIES = (111132.92, -559.82, 1.175, -0.0023)
_LON_SERIES = (111412.84, -93.5, 0.118)


def m_per_deg_lat(lat_deg: float) -> float:
    """Metres per degree of latitude at ``lat_deg``."""
    phi = math.radians(lat_deg)
    a, b, c, d = _LAT_SERIES
    return a + b * math.cos(2 * phi) + c * math.cos(4 * phi) + d * math.cos(6 * phi)


def m_per_deg_lon(lat_deg: float) -> float:
    """Metres per degree of longitude at ``lat_deg`` (shrinks as cos(lat))."""
    phi = math.radians(lat_deg)
    a, b, c = _LON_SERIES
    return a * math.cos(phi) + b * math.cos(3 * phi) + c * math.cos(5 * phi)


def km_to_deg_lat(km: float, lat_deg: float = 0.0) -> float:
    return km * 1000.0 / m_per_deg_lat(lat_deg)


def km_to_deg_lon(km: float, lat_deg: float) -> float:
    return km * 1000.0 / m_per_deg_lon(lat_deg)


def deg_lat_to_km(deg: float, lat_deg: float = 0.0) -> float:
    return deg * m_per_deg_lat(lat_deg) / 1000.0


def deg_lon_to_km(deg: float, lat_deg: float) -> float:
    return deg * m_per_deg_lon(lat_deg) / 1000.0


@dataclass(frozen=True)
class LocalFrame:
    """Local east/north metric frame anchored at (``lat0``, ``lon0``).

    ``x`` is metres east of the anchor, ``y`` is metres north of it.
    """

    lat0: float
    lon0: float

    @property
    def m_per_deg_lat(self) -> float:
        return m_per_deg_lat(self.lat0)

    @property
    def m_per_deg_lon(self) -> float:
        return m_per_deg_lon(self.lat0)

    def to_metres(self, lon, lat):
        """lon/lat (deg, array-like) -> x/y (metres east/north of the anchor)."""
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        x = (lon - self.lon0) * self.m_per_deg_lon
        y = (lat - self.lat0) * self.m_per_deg_lat
        return x, y

    def to_lonlat(self, x, y):
        """x/y (metres east/north) -> lon/lat (deg)."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        lon = self.lon0 + x / self.m_per_deg_lon
        lat = self.lat0 + y / self.m_per_deg_lat
        return lon, lat


def pixel_area_m2(pixel_width_deg: float, pixel_height_deg: float, lat_deg: float) -> float:
    """Ground area of one pixel of a lon/lat raster, at latitude ``lat_deg``.

    ``pixel_height_deg`` is the latitude extent (positive), ``pixel_width_deg`` the
    longitude extent. Both are taken as magnitudes - a north-up GeoTIFF has a negative
    y-step in its transform.
    """
    return (
        abs(pixel_width_deg) * m_per_deg_lon(lat_deg)
        * abs(pixel_height_deg) * m_per_deg_lat(lat_deg)
    )


def bearing_deg(east: float, north: float) -> float:
    """Compass bearing (deg clockwise from true north, [0, 360)) of a vector."""
    return math.degrees(math.atan2(east, north)) % 360.0


def axis_bearing_deg(east: float, north: float) -> float:
    """Bearing of an *undirected* axis: as :func:`bearing_deg`, folded to [0, 180)."""
    return bearing_deg(east, north) % 180.0


def covariance_ellipse(x, y, weights=None) -> tuple[float, float, float]:
    """Best-fit ellipse of a metric point cloud.

    Returns ``(major_length, minor_length, orientation_deg)`` where the lengths are
    full axis lengths in the same units as ``x``/``y`` and orientation follows the
    module convention (bearing from north, [0, 180)).

    Axis length is ``4 * sqrt(eigenvalue)`` of the coordinate covariance - the same
    definition scikit-image uses for ``axis_major_length``, so results stay comparable
    with ``regionprops`` output.
    """
    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    if x.size < 2:
        return 0.0, 0.0, 0.0

    if weights is None:
        cx, cy = x.mean(), y.mean()
        cov = np.cov(np.vstack([x - cx, y - cy]), bias=True)
    else:
        w = np.asarray(weights, dtype=float).ravel()
        wsum = w.sum()
        if wsum <= 0:
            return 0.0, 0.0, 0.0
        cx = float((w * x).sum() / wsum)
        cy = float((w * y).sum() / wsum)
        dx, dy = x - cx, y - cy
        cov = np.array(
            [
                [(w * dx * dx).sum() / wsum, (w * dx * dy).sum() / wsum],
                [(w * dx * dy).sum() / wsum, (w * dy * dy).sum() / wsum],
            ]
        )

    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]          # major first
    eigvals = np.clip(eigvals[order], 0.0, None)
    major_vec = eigvecs[:, order[0]]           # (east, north) components

    major = 4.0 * math.sqrt(float(eigvals[0]))
    minor = 4.0 * math.sqrt(float(eigvals[1]))
    orientation = axis_bearing_deg(float(major_vec[0]), float(major_vec[1]))
    return major, minor, orientation


def polyline_length_m(coords_xy) -> float:
    """Length of a metric polyline given as a sequence of (x, y) pairs."""
    pts = np.asarray(coords_xy, dtype=float)
    if pts.shape[0] < 2:
        return 0.0
    d = np.diff(pts, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())
