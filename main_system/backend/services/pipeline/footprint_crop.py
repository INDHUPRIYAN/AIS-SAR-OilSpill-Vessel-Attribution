"""Crop a full scene down to the detection footprint before Engine A.

Engine A reads the whole mask and the whole float32 scene into memory and
labels / distance-transforms the entire raster. On a 28k x 21k Sentinel-1
scene that is ~600 Mpx -> 5+ GB and tens of minutes for 0.01% oil pixels.
Its geometry, damping ring and age estimate only need the slick and a sea
margin around it, so the pipeline hands it a georeferenced window instead:
the same pixels, the same transform, a few thousand pixels on a side.

The engine is untouched; the crop is recorded in the stage warnings so the
manifest says exactly what Engine A saw.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import numpy as np

CROP_ABOVE_MPX = 64          # leave chips and small scenes alone
MARGIN_PX = 1500             # ~15 km at 10 m: room for the damping sea ring


def positive_bbox(mask_path: Path) -> Optional[Tuple[int, int, int, int]]:
    """(row_min, row_max, col_min, col_max) of mask>0, via block windows."""
    import rasterio
    r0 = c0 = None
    r1 = c1 = -1
    with rasterio.open(mask_path) as ds:
        for _, win in ds.block_windows(1):
            a = ds.read(1, window=win)
            if not a.any():
                continue
            rows, cols = np.nonzero(a)
            rmin, rmax = win.row_off + rows.min(), win.row_off + rows.max()
            cmin, cmax = win.col_off + cols.min(), win.col_off + cols.max()
            r0 = rmin if r0 is None else min(r0, rmin)
            c0 = cmin if c0 is None else min(c0, cmin)
            r1, c1 = max(r1, rmax), max(c1, cmax)
    if r0 is None:
        return None
    return int(r0), int(r1), int(c0), int(c1)


def _write_window(src_path: Path, dst_path: Path, window) -> None:
    import rasterio
    from rasterio.windows import transform as win_transform
    with rasterio.open(src_path) as src:
        data = src.read(window=window)
        profile = src.profile.copy()
        profile.update(height=int(window.height), width=int(window.width),
                       transform=win_transform(window, src.transform),
                       tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate")
        with rasterio.open(dst_path, "w", **profile) as dst:
            dst.write(data)


def crop_for_engine_a(mask_path: Path, scene_path: Optional[Path],
                      work_dir: Path) -> Tuple[Path, Optional[Path], Optional[str]]:
    """Return (mask, scene, note). Unchanged inputs and note=None when no crop."""
    import rasterio
    from rasterio.windows import Window
    try:
        with rasterio.open(mask_path) as ds:
            h, w = ds.height, ds.width
        if h * w < CROP_ABOVE_MPX * 1_000_000:
            return mask_path, scene_path, None
        bbox = positive_bbox(mask_path)
        if bbox is None:
            return mask_path, scene_path, None
        r0, r1, c0, c1 = bbox
        r0, c0 = max(r0 - MARGIN_PX, 0), max(c0 - MARGIN_PX, 0)
        r1, c1 = min(r1 + MARGIN_PX, h - 1), min(c1 + MARGIN_PX, w - 1)
        win = Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)
        if win.height * win.width >= 0.5 * h * w:
            return mask_path, scene_path, None      # crop would not help
        work_dir.mkdir(parents=True, exist_ok=True)
        mask_out = work_dir / "mask_footprint.tif"
        _write_window(mask_path, mask_out, win)
        scene_out = None
        if scene_path and Path(scene_path).exists():
            scene_out = work_dir / "scene_footprint.tif"
            _write_window(Path(scene_path), scene_out, win)
        note = (f"Engine A ran on a {int(win.width)}x{int(win.height)} px window "
                f"around the detections (margin {MARGIN_PX} px) instead of the "
                f"full {w}x{h} scene; geometry is georeferenced identically")
        return mask_out, scene_out or scene_path, note
    except Exception as exc:  # noqa: BLE001 -- crop is an optimisation, never a gate
        return mask_path, scene_path, f"footprint crop skipped: {type(exc).__name__}: {exc}"
