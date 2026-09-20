"""Geometry helpers: local metric frame, rasters, shape descriptors, HDR contours.

Everything is WGS84 lon/lat at the boundaries. Anything that needs metres
(areas, distances, shape moments) goes through `LocalFrame`, an equirectangular
tangent plane centred on the slick. Over the tens of kilometres a hindcast
spans, its error is far below the forcing uncertainty.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from rasterio import features as rio_features
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON_EQ = 111_320.0


@dataclass(frozen=True)
class LocalFrame:
    lon0: float
    lat0: float

    @property
    def m_per_deg_lon(self) -> float:
        return M_PER_DEG_LON_EQ * math.cos(math.radians(self.lat0))

    def to_xy(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return ((np.asarray(lon) - self.lon0) * self.m_per_deg_lon,
                (np.asarray(lat) - self.lat0) * M_PER_DEG_LAT)

    def to_lonlat(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (np.asarray(x) / self.m_per_deg_lon + self.lon0,
                np.asarray(y) / M_PER_DEG_LAT + self.lat0)


def load_geometry(geojson: dict[str, Any] | str) -> BaseGeometry:
    """Accept a Geometry, a Feature or a FeatureCollection; return one geometry."""
    obj = json.loads(geojson) if isinstance(geojson, str) else geojson
    kind = obj.get("type")
    if kind == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in obj.get("features", []) if f.get("geometry")]
        if not geoms:
            raise ValueError("FeatureCollection contains no geometry")
        geom = unary_union(geoms)
    elif kind == "Feature":
        geom = shape(obj["geometry"])
    else:
        geom = shape(obj)
    if geom.is_empty:
        raise ValueError("slick polygon is empty")
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"slick geometry must be polygonal, got {geom.geom_type}")
    return geom


def polygon_parts(geom: BaseGeometry) -> list[Polygon]:
    return list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]  # type: ignore[list-item]


def project(geom: BaseGeometry, frame: LocalFrame) -> BaseGeometry:
    from shapely import transform

    def fn(coords: np.ndarray) -> np.ndarray:
        x, y = frame.to_xy(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return transform(geom, fn)


def geojson_of(geom: BaseGeometry) -> dict[str, Any]:
    return json.loads(json.dumps(mapping(geom)))


# --------------------------------------------------------------------------
# rasters
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RasterGrid:
    """A north-up lon/lat grid. Row 0 is the northern edge, as in a GeoTIFF."""

    west: float
    north: float
    dlon: float
    dlat: float
    nx: int
    ny: int

    @classmethod
    def around(cls, west: float, south: float, east: float, north: float,
               max_cells: int, min_cell_m: float = 250.0) -> "RasterGrid":
        lat_mid = 0.5 * (south + north)
        m_lon = M_PER_DEG_LON_EQ * math.cos(math.radians(lat_mid))
        width_m, height_m = (east - west) * m_lon, (north - south) * M_PER_DEG_LAT
        cell_m = max(min_cell_m, max(width_m, height_m) / max_cells)
        nx = max(8, int(math.ceil(width_m / cell_m)))
        ny = max(8, int(math.ceil(height_m / cell_m)))
        return cls(west=west, north=north, dlon=cell_m / m_lon, dlat=cell_m / M_PER_DEG_LAT, nx=nx, ny=ny)

    @property
    def transform(self) -> Affine:
        return Affine(self.dlon, 0.0, self.west, 0.0, -self.dlat, self.north)

    @property
    def east(self) -> float:
        return self.west + self.nx * self.dlon

    @property
    def south(self) -> float:
        return self.north - self.ny * self.dlat

    @property
    def cell_area_km2(self) -> float:
        lat_mid = 0.5 * (self.south + self.north)
        return (self.dlon * M_PER_DEG_LON_EQ * math.cos(math.radians(lat_mid))
                * self.dlat * M_PER_DEG_LAT) / 1e6

    def index(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        col = np.floor((np.asarray(lon) - self.west) / self.dlon).astype(np.int64)
        row = np.floor((self.north - np.asarray(lat)) / self.dlat).astype(np.int64)
        inside = (col >= 0) & (col < self.nx) & (row >= 0) & (row < self.ny)
        return row, col, inside

    def histogram(self, lon: np.ndarray, lat: np.ndarray,
                  weights: Optional[np.ndarray] = None) -> np.ndarray:
        row, col, inside = self.index(lon, lat)
        flat = row[inside] * self.nx + col[inside]
        w = None if weights is None else np.asarray(weights)[inside]
        return np.bincount(flat, weights=w, minlength=self.nx * self.ny).reshape(self.ny, self.nx)

    def center(self, row: np.ndarray | int, col: np.ndarray | int) -> tuple[Any, Any]:
        return (self.west + (np.asarray(col) + 0.5) * self.dlon,
                self.north - (np.asarray(row) + 0.5) * self.dlat)

    def rasterize(self, geom: BaseGeometry) -> np.ndarray:
        return rio_features.rasterize([(geom, 1)], out_shape=(self.ny, self.nx),
                                      transform=self.transform, fill=0, dtype="uint8").astype(bool)

    def to_dict(self) -> dict[str, float | int]:
        return {"west": self.west, "north": self.north, "dlon": self.dlon,
                "dlat": self.dlat, "nx": self.nx, "ny": self.ny}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RasterGrid":
        return cls(west=float(d["west"]), north=float(d["north"]), dlon=float(d["dlon"]),
                   dlat=float(d["dlat"]), nx=int(d["nx"]), ny=int(d["ny"]))


def smooth_density(counts: np.ndarray, sigma_cells: float) -> np.ndarray:
    """Histogram + Gaussian kernel = a KDE on the grid, normalised to sum 1."""
    dens = ndimage.gaussian_filter(counts.astype(np.float64), sigma=sigma_cells, mode="constant")
    total = dens.sum()
    return dens / total if total > 0 else dens


def hdr_mask(p: np.ndarray, mass: float) -> np.ndarray:
    """Highest-density region: the smallest set of cells holding `mass` of p."""
    flat = p.ravel()
    total = flat.sum()
    if total <= 0:
        return np.zeros_like(p, dtype=bool)
    order = np.argsort(flat)[::-1]
    cum = np.cumsum(flat[order]) / total
    n_keep = int(np.searchsorted(cum, mass, side="left")) + 1
    mask = np.zeros(flat.shape, dtype=bool)
    mask[order[:n_keep]] = True
    return mask.reshape(p.shape)


def mask_to_geometry(mask: np.ndarray, grid: RasterGrid, smooth: bool = True) -> Optional[BaseGeometry]:
    if not mask.any():
        return None
    polys = [shape(geom) for geom, value in
             rio_features.shapes(mask.astype("uint8"), mask=mask, transform=grid.transform) if value == 1]
    geom = unary_union(polys)
    if smooth:
        # Round the staircase left by polygonising a raster, without changing area much.
        r = 0.6 * max(grid.dlon, grid.dlat)
        geom = geom.buffer(r).buffer(-r * 0.85).simplify(0.2 * r)
    return geom if not geom.is_empty else None


# --------------------------------------------------------------------------
# shape descriptors
# --------------------------------------------------------------------------

@dataclass
class ShapeStats:
    area_km2: float
    centroid_lon: float
    centroid_lat: float
    orientation_deg: float      # major axis, degrees CCW from east, in [0, 180)
    elongation: float           # sqrt(lambda_major / lambda_minor), >= 1
    length_m: float             # ~ full length along the major axis (4 sigma)
    bend_deg: float             # signed turn of the centreline along +major axis
    fragments: int

    def to_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def moments(x: np.ndarray, y: np.ndarray, w: Optional[np.ndarray] = None,
            toward: Optional[tuple[float, float]] = None) -> tuple[float, float, float, float]:
    """(orientation_deg, elongation, length_m, bend_deg) of a weighted point set.

    `toward` orients the otherwise sign-ambiguous major axis (used so that the
    sign of the bend means the same thing for an observed and a predicted shape).
    """
    w = np.ones_like(x, dtype=np.float64) if w is None else np.asarray(w, dtype=np.float64)
    sw = w.sum()
    if sw <= 0 or x.size < 3:
        return 0.0, 1.0, 0.0, 0.0
    mx, my = (w * x).sum() / sw, (w * y).sum() / sw
    dx, dy = x - mx, y - my
    cov = np.array([[(w * dx * dx).sum(), (w * dx * dy).sum()],
                    [(w * dx * dy).sum(), (w * dy * dy).sum()]]) / sw
    vals, vecs = np.linalg.eigh(cov)
    major = vecs[:, 1]
    if toward is not None and (major[0] * toward[0] + major[1] * toward[1]) < 0:
        major = -major
    minor = np.array([-major[1], major[0]])
    lam_major, lam_minor = max(vals[1], 1e-9), max(vals[0], 1e-9)
    u, v = dx * major[0] + dy * major[1], dx * minor[0] + dy * minor[1]
    length = 4.0 * math.sqrt(lam_major)
    # Weighted least squares v = a u^2 + b u + c; the centreline turns by 2*atan(a*L).
    a_mat = np.column_stack([u * u, u, np.ones_like(u)]) * np.sqrt(w)[:, None]
    coef, *_ = np.linalg.lstsq(a_mat, v * np.sqrt(w), rcond=None)
    bend = math.degrees(2.0 * math.atan(coef[0] * length))
    orientation = math.degrees(math.atan2(major[1], major[0])) % 180.0
    return orientation, math.sqrt(lam_major / lam_minor), length, bend


def shape_stats(geom: BaseGeometry, toward: Optional[tuple[float, float]] = None,
                n_samples: int = 4000, seed: int = 0) -> ShapeStats:
    c = geom.centroid
    frame = LocalFrame(c.x, c.y)
    local = project(geom, frame)
    rng = np.random.default_rng(seed)
    lon, lat = sample_in_polygon(geom, n_samples, rng)
    x, y = frame.to_xy(lon, lat)
    orientation, elong, length, bend = moments(x, y, toward=toward)
    return ShapeStats(area_km2=local.area / 1e6, centroid_lon=c.x, centroid_lat=c.y,
                      orientation_deg=orientation, elongation=elong, length_m=length,
                      bend_deg=bend, fragments=len(polygon_parts(geom)))


def sample_in_polygon(geom: BaseGeometry, n: int, rng: np.random.Generator,
                      weight_fn: Any = None) -> tuple[np.ndarray, np.ndarray]:
    """n points inside `geom` by rejection sampling.

    `weight_fn(lon, lat) -> weights in [0, 1]` thins the accepted points, which
    is how the damping-ratio raster concentrates seeds on the thick oil.
    """
    from shapely import contains_xy

    west, south, east, north = geom.bounds
    lons: list[np.ndarray] = []
    lats: list[np.ndarray] = []
    have = 0
    for _ in range(200):
        batch = max(1024, 4 * (n - have))
        lo = rng.uniform(west, east, batch)
        la = rng.uniform(south, north, batch)
        keep = contains_xy(geom, lo, la)
        if weight_fn is not None and keep.any():
            wts = np.clip(np.asarray(weight_fn(lo, la), dtype=np.float64), 0.02, 1.0)
            keep &= rng.uniform(0, 1, batch) < wts
        lons.append(lo[keep])
        lats.append(la[keep])
        have += int(keep.sum())
        if have >= n:
            break
    lon, lat = np.concatenate(lons)[:n], np.concatenate(lats)[:n]
    if lon.size < n:   # a sliver polygon: top up from the centroid rather than fail
        c = geom.representative_point()
        pad = n - lon.size
        lon, lat = np.concatenate([lon, np.full(pad, c.x)]), np.concatenate([lat, np.full(pad, c.y)])
    return lon, lat


def edge_roughness(geom: BaseGeometry) -> float:
    """0 = smooth convex outline (fresh), towards 1 = ragged/feathered (weathered).

    The polygon-only stand-in for SAR edge sharpness: how much longer the real
    boundary is than its convex hull, and how much hull area the slick has lost.
    """
    hull = geom.convex_hull
    if hull.area <= 0 or hull.length <= 0:
        return 0.0
    perimeter_excess = max(0.0, geom.length / hull.length - 1.0)
    solidity_loss = 1.0 - geom.area / hull.area
    return float(np.clip(0.5 * np.tanh(perimeter_excess) + 0.5 * solidity_loss, 0.0, 1.0))


def angle_diff_axis(a_deg: float, b_deg: float) -> float:
    """Smallest difference between two undirected axes, in [0, 90]."""
    d = abs(a_deg - b_deg) % 180.0
    return min(d, 180.0 - d)


def point_in(geom: BaseGeometry, lon: float, lat: float) -> bool:
    return bool(geom.covers(Point(lon, lat)))
