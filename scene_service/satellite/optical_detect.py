"""Sentinel-2 L2A -> georeferenced slick polygons on the frozen `slick.geojson` contract.

This is the optical arm of the detection stage. It reads the reflectance bands out of a
Sentinel-2 SAFE product, computes the optical slick index, thresholds it robustly, and
polygonises the result into exactly the same artifact the SAR arm produces - so
everything downstream (Engine A geometry, Engine B drift, Engine C attribution, the UI)
consumes it without knowing which sensor it came from.

What is claimed, and what is not
--------------------------------
CLAIMED: a Sentinel-2 product can be acquired, read, preprocessed, turned into a
georeferenced slick polygon set, and fed into the existing pipeline.

NOT CLAIMED: optical detection accuracy. There is no labelled optical oil-spill dataset
in this repository, so this is a physically-motivated **index**, not a trained detector.
Every feature it emits carries ``engine: "optical_index"`` and ``model_version:
"s2-optical-index-v1"`` precisely so it can never be confused with the trained SAR
segmenter, whose accuracy *is* measured (docs/eval/).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .s2_adapter import optical_slick_index, threshold_index

# Sentinel-2 L2A band file suffixes at 20 m (B11 exists only at 20/60 m).
_BAND_PATTERNS = {
    "B03": r"_B03_20m\.jp2$",
    "B04": r"_B04_20m\.jp2$",
    "B08": r"_B8A_20m\.jp2$",     # B8A is the 20 m narrow NIR
    "B11": r"_B11_20m\.jp2$",
}

# L2A surface reflectance is stored as int16 scaled by 10000.
_REFLECTANCE_SCALE = 10000.0


def find_band_files(safe_zip: str | Path) -> dict[str, str]:
    """Locate the four bands inside a .SAFE zip, returned as zip-internal paths."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(safe_zip) as z:
        names = z.namelist()
    for band, pattern in _BAND_PATTERNS.items():
        hits = [n for n in names if re.search(pattern, n)]
        if hits:
            out[band] = sorted(hits)[0]
    return out


def read_bands(
    safe_zip: str | Path, decimate: int = 4
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Read the bands as reflectance (0..1) plus the raster profile.

    ``decimate`` reads a reduced-resolution overview: a full 20 m tile is 5490x5490 per
    band and the index does not need full resolution to find a km-scale slick.
    """
    import rasterio

    bands = find_band_files(safe_zip)
    if "B03" not in bands or "B04" not in bands or "B08" not in bands:
        raise FileNotFoundError(
            f"Sentinel-2 product is missing required bands; found {sorted(bands)}"
        )

    data: dict[str, np.ndarray] = {}
    profile: dict[str, Any] = {}
    for band, member in bands.items():
        with rasterio.open(f"zip://{Path(safe_zip).as_posix()}!/{member}") as src:
            h, w = src.height // decimate, src.width // decimate
            arr = src.read(1, out_shape=(h, w)).astype(np.float32) / _REFLECTANCE_SCALE
            data[band] = arr
            if not profile:
                profile = {
                    "crs": src.crs,
                    "transform": src.transform * src.transform.scale(
                        src.width / w, src.height / h),
                    "height": h, "width": w,
                }
    return data, profile


def detect(
    safe_zip: str | Path,
    *,
    decimate: int = 4,
    k_sigma: float = 3.0,
    min_pixels: int = 40,
    scene_id: Optional[str] = None,
    acquired_utc: Optional[str] = None,
) -> dict[str, Any]:
    """Sentinel-2 product -> `slick.geojson`-shaped FeatureCollection (WGS84)."""
    from rasterio.features import shapes as rio_shapes
    from rasterio.warp import transform_geom
    from shapely.geometry import mapping, shape

    data, profile = read_bands(safe_zip, decimate=decimate)
    index, water = optical_slick_index(
        data["B03"], data["B04"], data["B08"], data.get("B11")
    )
    mask = threshold_index(index, water, k=k_sigma)

    feats: list[dict[str, Any]] = []
    for geom, value in rio_shapes(mask, mask=mask.astype(bool),
                                  transform=profile["transform"]):
        if value != 1:
            continue
        poly = shape(geom)
        # crude pixel count from area / pixel area, to drop specks
        px_area = abs(profile["transform"].a * profile["transform"].e)
        if px_area > 0 and poly.area / px_area < min_pixels:
            continue
        wgs = transform_geom(profile["crs"], "EPSG:4326", mapping(poly))
        feats.append(shape(wgs))

    feats.sort(key=lambda g: g.area, reverse=True)
    features = []
    for i, g in enumerate(feats, start=1):
        c = g.centroid
        features.append({
            "type": "Feature",
            "geometry": json.loads(json.dumps(mapping(g))),
            "properties": {
                "slick_id": f"s2-slick-{i:03d}",
                "centroid": [round(c.x, 6), round(c.y, 6)],
                "engine": "optical_index",
                "model_version": "s2-optical-index-v1",
                "sensor": "Sentinel-2 MSI",
                "modality": "EO/optical",
                "source": "real",
                "confidence": None,
                "confidence_note": (
                    "No optical detection accuracy is claimed: this is a physically "
                    "motivated index, not a trained and validated detector."
                ),
            },
        })

    water_px = int(water.sum())
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": scene_id or Path(safe_zip).stem,
            "acquired_utc": acquired_utc,
            "crs": "EPSG:4326",
            "sensor": "Sentinel-2 MSI",
            "modality": "EO/optical",
            "engine": "optical_index",
            "model_version": "s2-optical-index-v1",
            "decimate": decimate,
            "k_sigma": k_sigma,
            "water_pixels": water_px,
            "water_fraction": round(water_px / max(water.size, 1), 4),
            "slick_pixels": int(mask.sum()),
            "bands_used": sorted(data),
            "data_source": "sensor",
            "honesty_note": (
                "Optical detection is a corroborating sensor: Sentinel-2 sees nothing at "
                "night or through cloud, and the optical slick signature is more ambiguous "
                "than the SAR damping signature. Accuracy is unquantified."
            ),
        },
        "features": features,
    }
