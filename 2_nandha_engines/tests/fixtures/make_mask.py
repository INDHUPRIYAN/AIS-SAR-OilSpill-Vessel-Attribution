"""Generate Engine A's mock input: a hand-drawn slick mask with known geometry.

Handbook §5.4 / §8: the known-shape test needs a drawn ellipse whose axes and area the
engine must recover. This generator *is* that ground truth - it writes the raster and
returns the exact analytic values it drew, so the test never hard-codes numbers.

Products (default output dir ``tests/fixtures/data/``):

    mask.tif        1-band uint8 0/1 slick mask, EPSG:4326
    scene_db.tif    1-band float32 Sigma0 dB backscatter (sea bright, slick damped)
    scene_meta.json Pavitra-style scene metadata + the detection confidence

Analytic facts used by the tests
--------------------------------
For a filled ellipse with semi-axes a, b:
  * area                = pi * a * b
  * regionprops-style full axis lengths = 2a and 2b
    (4 * sqrt(lambda) where lambda are the second-moment eigenvalues a^2/4, b^2/4)
  * perimeter           ~ Ramanujan's approximation (raster staircase inflates the
    measured value, so the perimeter assertion carries a loose tolerance)

Run:  python -m tests.fixtures.make_mask [--out-dir DIR] [--seed N]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from engines.common.geo import m_per_deg_lat, m_per_deg_lon

# --- demo scene constants (Chennai / Ennore region, handbook §4 example) -------------
SCENE_ID = "S1A_IW_GRDH_20170202T0039_DEMO-A"
ACQUIRED_UTC = "2017-02-02T00:39:42Z"
DETECTION_CONFIDENCE = 0.91

CENTRE_LON = 80.312
CENTRE_LAT = 13.052

PIXEL_DEG = 0.0001          # ~11 m - Sentinel-1 GRD scale
PAD_DEG = 0.02              # sea margin around the slick, wide enough for a ring buffer

SEA_DB = -12.0              # typical Sigma0 over wind-roughened sea
SLICK_DB = -19.0            # damped return inside the slick -> 7 dB damping ratio
DB_NOISE = 0.6              # speckle-ish gaussian noise, 1 sigma


@dataclass(frozen=True)
class Ellipse:
    """One drawn slick. Lengths in km, orientation in bearing-from-north degrees."""

    lon: float
    lat: float
    semi_major_km: float
    semi_minor_km: float
    orientation_deg: float

    @property
    def area_km2(self) -> float:
        return math.pi * self.semi_major_km * self.semi_minor_km

    @property
    def major_axis_km(self) -> float:
        return 2.0 * self.semi_major_km

    @property
    def minor_axis_km(self) -> float:
        return 2.0 * self.semi_minor_km

    @property
    def perimeter_km(self) -> float:
        """Ramanujan's approximation - exact to ~1e-5 for these axis ratios."""
        a, b = self.semi_major_km, self.semi_minor_km
        return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))


# Primary slick: matches the handbook's example dimensions (7.9 x 2.4 km at 62 deg).
MAIN_SLICK = Ellipse(CENTRE_LON, CENTRE_LAT, 3.95, 1.20, 62.0)

# Second, smaller slick for the multi-slick test - clearly separated from the first.
SECOND_SLICK = Ellipse(CENTRE_LON - 0.030, CENTRE_LAT + 0.030, 0.90, 0.55, 150.0)

# Speck below any sane min-area threshold; must be dropped by the engine.
SPECK = Ellipse(CENTRE_LON + 0.033, CENTRE_LAT - 0.028, 0.045, 0.035, 0.0)


def _ellipse_mask(lon_grid, lat_grid, ell: Ellipse) -> np.ndarray:
    """Boolean mask of pixels whose centre falls inside ``ell``.

    Works in a local east/north metric frame anchored at the ellipse centre, then
    rotates into the ellipse's own axes. ``orientation_deg`` is a bearing from north
    (clockwise), so the major-axis unit vector is (sin t, cos t) in (east, north).
    """
    mx = m_per_deg_lon(ell.lat)
    my = m_per_deg_lat(ell.lat)
    east = (lon_grid - ell.lon) * mx
    north = (lat_grid - ell.lat) * my

    t = math.radians(ell.orientation_deg)
    u_e, u_n = math.sin(t), math.cos(t)      # along major axis
    v_e, v_n = math.cos(t), -math.sin(t)     # along minor axis

    along = east * u_e + north * u_n
    across = east * v_e + north * v_n

    a_m = ell.semi_major_km * 1000.0
    b_m = ell.semi_minor_km * 1000.0
    return (along / a_m) ** 2 + (across / b_m) ** 2 <= 1.0


def build_scene(
    out_dir: Path,
    *,
    seed: int = 26143,
    slicks: tuple[Ellipse, ...] = (MAIN_SLICK, SECOND_SLICK, SPECK),
    empty: bool = False,
) -> dict:
    """Write mask.tif + scene_db.tif + scene_meta.json; return the ground truth dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    # The empty-mask variant writes its own file names so it can never clobber the
    # main fixture (it would otherwise overwrite scene_db.tif with a slick-free scene).
    suffix = "_empty" if empty else ""

    # --- raster extent: bbox of every drawn slick, padded with open sea -------------
    span_deg = max(
        s.semi_major_km * 1000.0 / min(m_per_deg_lon(s.lat), m_per_deg_lat(s.lat))
        for s in slicks
    )
    west = min(s.lon for s in slicks) - span_deg - PAD_DEG
    east_edge = max(s.lon for s in slicks) + span_deg + PAD_DEG
    south = min(s.lat for s in slicks) - span_deg - PAD_DEG
    north_edge = max(s.lat for s in slicks) + span_deg + PAD_DEG

    width = int(round((east_edge - west) / PIXEL_DEG))
    height = int(round((north_edge - south) / PIXEL_DEG))
    transform = from_origin(west, north_edge, PIXEL_DEG, PIXEL_DEG)

    # Pixel-centre coordinates (north-up raster: row 0 is the top / highest latitude).
    lons = west + (np.arange(width) + 0.5) * PIXEL_DEG
    lats = north_edge - (np.arange(height) + 0.5) * PIXEL_DEG
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    mask = np.zeros((height, width), dtype=bool)
    if not empty:
        for ell in slicks:
            mask |= _ellipse_mask(lon_grid, lat_grid, ell)

    db = np.full((height, width), SEA_DB, dtype=np.float32)
    db[mask] = SLICK_DB
    db += rng.normal(0.0, DB_NOISE, size=db.shape).astype(np.float32)

    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
    }

    mask_path = out_dir / f"mask{suffix}.tif"
    with rasterio.open(mask_path, "w", dtype="uint8", nodata=None, **profile) as dst:
        dst.write(mask.astype(np.uint8), 1)
        dst.set_band_description(1, "oil_mask_0_1")

    db_path = out_dir / f"scene_db{suffix}.tif"
    with rasterio.open(db_path, "w", dtype="float32", nodata=None, **profile) as dst:
        dst.write(db, 1)
        dst.set_band_description(1, "Sigma0_VV_db")

    meta = {
        "scene_id": SCENE_ID,
        "platform": "Sentinel-1A",
        "acquisition_time": ACQUIRED_UTC,
        "bbox": [round(west, 6), round(south, 6), round(east_edge, 6), round(north_edge, 6)],
        "crs": "EPSG:4326",
        "db_range": [-35.0, 0.0],
        "file_path": db_path.name,
        "provider_used": "mock",
        # Produced by Indhu's /detect, carried through to slick.geojson properties.
        "confidence": DETECTION_CONFIDENCE,
        "synthetic": True,
    }
    meta_path = out_dir / f"scene_meta{suffix}.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    truth = {
        "mask_path": str(mask_path),
        "db_path": str(db_path),
        "scene_meta_path": str(meta_path),
        "scene_id": SCENE_ID,
        "acquired_utc": ACQUIRED_UTC,
        "confidence": DETECTION_CONFIDENCE,
        "pixel_deg": PIXEL_DEG,
        "expected_damping_db": SEA_DB - SLICK_DB,
        "slicks": [
            {
                **asdict(e),
                "area_km2": e.area_km2,
                "major_axis_km": e.major_axis_km,
                "minor_axis_km": e.minor_axis_km,
                "perimeter_km": e.perimeter_km,
            }
            for e in ([] if empty else slicks)
        ],
    }
    (out_dir / f"ground_truth{suffix}.json").write_text(
        json.dumps(truth, indent=2) + "\n", encoding="utf-8"
    )
    return truth


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Engine A mock inputs")
    parser.add_argument("--out-dir", default="tests/fixtures/data", type=Path)
    parser.add_argument("--seed", default=26143, type=int)
    parser.add_argument("--empty", action="store_true", help="also write an empty mask")
    args = parser.parse_args()

    truth = build_scene(args.out_dir, seed=args.seed)
    print(json.dumps(truth, indent=2))
    if args.empty:
        build_scene(args.out_dir, seed=args.seed, empty=True)
        print(f"empty mask written to {args.out_dir / 'mask_empty.tif'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
