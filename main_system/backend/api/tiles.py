"""XYZ tiles of a run's calibrated Sigma0 raster.

    GET /api/tiles/{run_id}/{z}/{x}/{y}.png?db_min=&db_max=

The workspace could show a slick outline over a basemap but never the SAR
itself. A polygon over OpenStreetMap is a claim; the backscatter is the
evidence, and an analyst who cannot see the pixels cannot check the outline
against them.

**On rio-tiler.** PROMPT 18 names it as the dependency. It is not installable
here: `rio-tiler` requires `color-operations`, which publishes no wheel for this
Python/platform and has no compiler toolchain available. Rather than leave the
capability out, this implements the same thing on `rasterio` -- already a
dependency, and what rio-tiler wraps. `WarpedVRT` does the web-mercator
reprojection, numpy does the dB stretch, Pillow encodes the PNG. The deviation
is recorded in dev_evidence/P18 rather than hidden behind an import guard.

Three properties this holds to.

**The stretch is honest and adjustable.** Defaults come from the frozen
`normalisation.yaml` clip range -- the same [-35, 0] dB the model was trained
on -- so what an analyst sees is what the segmenter saw. `db_min`/`db_max` can
be overridden for inspection, and the response says which values were used, so
a screenshot cannot silently misrepresent contrast.

**Nodata is transparent, never black.** Land and scene edges are alpha 0. A
black fill reads as "very dark water", which is exactly what an oil slick looks
like in SAR -- the one confusion this system cannot afford to introduce.

**A missing scene is a 404 with a reason.** Runs that never had a raster on
this host, and runs whose raster has been moved, are different problems.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.core.authz import authenticated
from backend.core.config import get_settings

router = APIRouter(dependencies=[Depends(authenticated)])

TILE_SIZE = 256
WEB_MERCATOR = "EPSG:3857"
# Half the equatorial circumference in metres; the web-mercator world is
# [-ORIGIN, ORIGIN] on both axes.
ORIGIN = 20037508.342789244

# A fully transparent 256x256 PNG, returned for tiles outside the scene. Cached
# as bytes because an empty tile is by far the most requested one.
_EMPTY_PNG: Optional[bytes] = None


def _clip_range() -> Tuple[float, float]:
    """The dB range the model was trained on, from the frozen config.

    Defaulting to the training range rather than to the scene's own min/max is
    deliberate: a per-scene autostretch makes every scene look equally
    contrasty and hides that one of them is mostly noise.
    """
    try:
        from ml.config import load_config

        cfg = load_config()
        return float(cfg.sar.db_min), float(cfg.sar.db_max)
    except Exception:                                   # noqa: BLE001
        return -35.0, 0.0


def _tile_bounds(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Web-mercator bounds of an XYZ tile."""
    span = 2.0 * ORIGIN / (2 ** z)
    west = -ORIGIN + x * span
    east = west + span
    north = ORIGIN - y * span
    south = north - span
    return west, south, east, north


def _empty_tile() -> bytes:
    global _EMPTY_PNG
    if _EMPTY_PNG is None:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0)).save(buf, "PNG")
        _EMPTY_PNG = buf.getvalue()
    return _EMPTY_PNG


def _scene_path(run_id: str) -> Optional[Path]:
    """The calibrated raster this run was produced from.

    Read from the run's own scene_meta, so a tile always comes from the scene
    the run actually used rather than from whatever is newest on disk.
    """
    import json

    settings = get_settings()
    root = settings.runs_root.resolve()
    run_dir = (root / run_id).resolve()
    if not str(run_dir).startswith(str(root)):
        raise HTTPException(400, "invalid run id")

    meta_file = run_dir / "scene_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            candidate = meta.get("file_path")
            if candidate:
                path = Path(candidate)
                if not path.is_absolute():
                    path = Path(settings.data_root).parent / path
                if path.exists():
                    return path
        except (json.JSONDecodeError, OSError):
            pass

    local = run_dir / "scene_sigma0_db.tif"
    return local if local.exists() else None


@router.get("/tiles/{run_id}/{z}/{x}/{y}.png")
def scene_tile(run_id: str, z: int, x: int, y: int,
               db_min: Optional[float] = Query(None),
               db_max: Optional[float] = Query(None),
               band: int = Query(0, ge=0, le=4,
                                 description="0 = the band the model reads")):
    """One 256x256 PNG tile of the run's Sigma0 raster."""
    if not 0 <= z <= 22:
        raise HTTPException(400, "zoom out of range")
    if not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(400, "tile index outside the world at this zoom")

    path = _scene_path(run_id)
    if path is None:
        raise HTTPException(
            404,
            f"run {run_id} has no calibrated raster on this host. A run "
            "produced elsewhere carries its artefacts but not its scene; "
            "fetch and calibrate the scene to view pixels.")

    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import from_bounds

    lo, hi = _clip_range()
    lo = db_min if db_min is not None else lo
    hi = db_max if db_max is not None else hi
    if hi <= lo:
        raise HTTPException(400, "db_max must be greater than db_min")

    west, south, east, north = _tile_bounds(z, x, y)

    try:
        with rasterio.open(path) as src:
            index = band or int(_primary_band(src))
            with WarpedVRT(src, crs=WEB_MERCATOR, resampling=Resampling.bilinear) as vrt:
                # Outside the scene entirely: transparent, not an error. A map
                # requests a grid of tiles and most of them miss.
                if (east <= vrt.bounds.left or west >= vrt.bounds.right
                        or north <= vrt.bounds.bottom or south >= vrt.bounds.top):
                    return Response(_empty_tile(), media_type="image/png",
                                    headers={"Cache-Control": "public, max-age=3600"})

                # WarpedVRT refuses boundless reads, so the overlap is read
                # and composited into the tile by hand. A tile that only
                # partly covers the scene must show the covered part and leave
                # the rest transparent -- clamping the read to the raster and
                # stretching it across the whole tile would smear the edge
                # pixels across open water.
                ov_west = max(west, vrt.bounds.left)
                ov_east = min(east, vrt.bounds.right)
                ov_south = max(south, vrt.bounds.bottom)
                ov_north = min(north, vrt.bounds.top)

                span_x = east - west
                span_y = north - south
                col_off = int(round((ov_west - west) / span_x * TILE_SIZE))
                col_end = int(round((ov_east - west) / span_x * TILE_SIZE))
                row_off = int(round((north - ov_north) / span_y * TILE_SIZE))
                row_end = int(round((north - ov_south) / span_y * TILE_SIZE))
                out_w = max(col_end - col_off, 1)
                out_h = max(row_end - row_off, 1)

                window = from_bounds(ov_west, ov_south, ov_east, ov_north,
                                     vrt.transform)
                patch = vrt.read(index, window=window,
                                 out_shape=(out_h, out_w),
                                 resampling=Resampling.bilinear,
                                 masked=True)
                patch = np.ma.filled(patch.astype("float32"), np.nan)

                data = np.full((TILE_SIZE, TILE_SIZE), np.nan, dtype="float32")
                data[row_off:row_off + out_h, col_off:col_off + out_w] = patch
    except HTTPException:
        raise
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(500, f"could not read the raster: "
                                 f"{type(exc).__name__}: {exc}")

    data = data.astype("float32")
    # Nodata is anything non-finite, the calibration's -999 sentinel, or an
    # exact zero (an unimaged border, not very bright sea).
    invalid = ~np.isfinite(data) | (data <= -999.0) | (data == 0.0)

    scaled = (data - lo) / (hi - lo)
    np.clip(scaled, 0.0, 1.0, out=scaled)
    grey = (scaled * 255.0).astype("uint8")

    alpha = np.where(invalid, 0, 255).astype("uint8")
    rgba = np.dstack([grey, grey, grey, alpha])

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, "PNG", optimize=True)
    payload = buf.getvalue()

    return Response(
        payload, media_type="image/png",
        headers={
            # Tiles are immutable for a sealed run: the raster does not change.
            "Cache-Control": "public, max-age=86400",
            "X-Stretch-Db-Min": str(lo),
            "X-Stretch-Db-Max": str(hi),
            "X-Tile-Bytes": str(len(payload)),
        })


def _primary_band(src) -> int:
    """The band the segmenter reads, or band 1 on a single-band scene."""
    try:
        from ml.config import load_config

        wanted = int(load_config().sar.primary_band)
    except Exception:                                   # noqa: BLE001
        wanted = 1
    return wanted if wanted <= src.count else 1


@router.get("/tiles/{run_id}/info")
def tile_info(run_id: str):
    """What a client needs to add this raster as a map source."""
    path = _scene_path(run_id)
    if path is None:
        raise HTTPException(404, f"run {run_id} has no calibrated raster on this host")

    import rasterio

    lo, hi = _clip_range()
    with rasterio.open(path) as src:
        bounds = src.bounds
        crs = str(src.crs)
        shape = [src.height, src.width]
        band_count = src.count
        try:
            from rasterio.warp import transform_bounds

            wgs84 = list(transform_bounds(src.crs, "EPSG:4326", *bounds))
        except Exception:                               # noqa: BLE001
            wgs84 = None

    return {
        "run_id": run_id,
        "tile_url": f"/api/tiles/{run_id}/{{z}}/{{x}}/{{y}}.png",
        "tile_size": TILE_SIZE,
        "bounds_wgs84": wgs84,
        "native_crs": crs,
        "shape": shape,
        "bands": band_count,
        "default_stretch_db": [lo, hi],
        "stretch_note": ("Defaults are the frozen normalisation clip range -- "
                         "the same values the segmenter was trained on, so the "
                         "pixels an analyst sees are the pixels the model saw. "
                         "Override with ?db_min=&db_max= for inspection; the "
                         "response headers report what was applied."),
        "nodata_note": "Land and scene edges are transparent, never black: a "
                       "black fill reads as very dark water, which is what a "
                       "slick looks like in SAR.",
    }
