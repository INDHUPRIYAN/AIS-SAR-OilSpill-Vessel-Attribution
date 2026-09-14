"""Generate the globe's land geometry, offline, from a dataset already on disk.

WHY THIS EXISTS
---------------
The 3D globe is a deck.gl `_GlobeView`. A raster basemap does not survive that
projection: `BitmapLayer` reprojects each tile against the viewport, and on a
globe the tiles render as small skewed quads rather than curving onto the
sphere -- a lattice of diamonds over a bare planet. deck.gl's own globe example
draws land as VECTOR geometry for exactly this reason, and vector land is also
what the product needs: it is crisp at every zoom, it can be recoloured for the
light theme, and it doubles as the "country/coastline boundaries" layer.

WHY IT IS GENERATED RATHER THAN DOWNLOADED
------------------------------------------
`global_land_mask` is already an installed dependency of this project and ships
its own land/ocean raster (derived from public-domain GSHHG shoreline data), so
the coastline can be derived here with no new third-party asset committed to
the repo, no runtime fetch, and no licence question. The output is a build
artefact: re-run this script to regenerate it.

    python scripts/build_globe_land.py

Writes main_system/frontend/public/geo/land.json -- a GeoJSON FeatureCollection
of land polygons in WGS84, served as a static file and fetched once by the
globe.

The polygons are a COASTLINE, not a jurisdictional claim. They carry no country
identity and are used for orientation only; operational zones and their
protected boundaries are a separate, server-owned dataset.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from global_land_mask import globe
from shapely.geometry import Polygon, mapping
from skimage import measure

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "main_system" / "frontend" / "public" / "geo" / "land.json"

# Grid step in degrees. 0.2 resolves the Andaman & Nicobar chain and the
# Sundarbans -- both inside the Bay of Bengal theatre -- while keeping the
# emitted file small enough to serve as one static asset.
STEP = 0.2
LAT_LIMIT = 84.0          # the globe view clips near the poles anyway

# Simplification tolerance in degrees, and the smallest island kept. Together
# these decide the file size. 0.05 deg is ~5 km at the equator: coarse enough
# to halve the vertex count, fine enough that a coastline still reads as that
# coastline.
SIMPLIFY_DEG = 0.05
MIN_AREA_DEG2 = 0.02


def build_mask() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lons = np.arange(-180.0, 180.0 + STEP, STEP)
    lats = np.arange(-LAT_LIMIT, LAT_LIMIT + STEP, STEP)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    # is_land is vectorised over numpy arrays.
    mask = globe.is_land(lat_grid, lon_grid)
    return mask.astype(float), lons, lats


def contours_to_polygons(mask: np.ndarray, lons: np.ndarray, lats: np.ndarray):
    """Marching squares over the land mask -> simplified lon/lat polygons."""
    polygons = []
    for contour in measure.find_contours(mask, 0.5):
        # find_contours returns (row, col) in index space, fractional.
        rows, cols = contour[:, 0], contour[:, 1]
        lon = np.interp(cols, np.arange(len(lons)), lons)
        lat = np.interp(rows, np.arange(len(lats)), lats)
        ring = list(zip(lon.tolist(), lat.tolist()))
        if len(ring) < 4:
            continue
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        poly = poly.simplify(SIMPLIFY_DEG, preserve_topology=True)
        if poly.is_empty or poly.area < MIN_AREA_DEG2:
            continue
        # buffer(0) can turn one ring into a MultiPolygon; keep the parts.
        for part in (poly.geoms if poly.geom_type == "MultiPolygon" else [poly]):
            if part.area >= MIN_AREA_DEG2:
                polygons.append(part)
    return polygons


def main() -> None:
    mask, lons, lats = build_mask()
    polygons = contours_to_polygons(mask, lons, lats)
    polygons.sort(key=lambda p: p.area, reverse=True)

    features = [{
        "type": "Feature",
        "properties": {"rank": i},
        "geometry": mapping(p),
    } for i, p in enumerate(polygons)]

    payload = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "global_land_mask (public-domain GSHHG-derived land raster)",
            "generated_by": "scripts/build_globe_land.py",
            "grid_step_deg": STEP,
            "simplify_deg": SIMPLIFY_DEG,
            "min_area_deg2": MIN_AREA_DEG2,
            "lat_limit_deg": LAT_LIMIT,
            "note": ("Coastline for orientation on the 3D globe. Carries no "
                     "country identity and asserts nothing about maritime "
                     "jurisdiction; operational zones are a separate dataset."),
        },
        "features": features,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # separators keep the file tight; coordinates rounded to 3 dp (~110 m),
    # which is far finer than this geometry's own 0.2 deg source grid.
    text = json.dumps(payload, separators=(",", ":"))
    OUT.write_text(_round_coords(text), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}  "
          f"{len(features)} polygons  {OUT.stat().st_size / 1024:.0f} KB")


def _round_coords(text: str) -> str:
    """Round every number in the JSON to 3 decimals, textually.

    Cheaper and simpler than walking the structure, and safe here because the
    only numbers in the document are coordinates and the small integer
    metadata, which round to themselves.
    """
    import re

    return re.sub(r"-?\d+\.\d{4,}", lambda m: f"{float(m.group()):.3f}", text)


if __name__ == "__main__":
    main()
