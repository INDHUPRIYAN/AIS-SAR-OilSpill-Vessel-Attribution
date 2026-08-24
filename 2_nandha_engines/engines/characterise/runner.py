"""Engine A orchestration: mask GeoTIFF + scene metadata -> ``slick.geojson``.

Contract: handbook §4.2 (output) and §7 (CLI). This module is the only place that
touches files; ``features``/``damping``/``age`` stay pure so they can be tested on
arrays alone.

Failure policy (handbook §4.5): every declared failure comes back as a status object,
never as a traceback. ``EngineError`` is raised freely inside and caught exactly once,
here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject
from shapely.geometry import mapping
from skimage.measure import label as sk_label

from ..common.errors import EngineError, empty_mask, missing_input
from ..common.io import read_json, read_yaml, require_file, write_json
from ..common.status import PRIMARY, Status
from ..common.timeutil import format_utc, parse_utc
from ..schemas.slick import validate_slick
from .age import AGE_CONFIDENCE, FayParams, estimate_age
from .damping import compute_damping
from .features import extract_slicks

WGS84 = "EPSG:4326"
DEFAULT_CONFIG = Path("config/characterise.yaml")

# Keys a scene_meta.json might carry the acquisition time under. Pavitra's contract
# says "acquisition time (UTC)"; the shared mock uses `acquisition_time`.
_TIME_KEYS = ("acquisition_time", "acquired_utc", "acquisition_utc", "detected_utc")


# ----------------------------------------------------------------- raster input ----
def _read_mask(path: Path) -> tuple[np.ndarray, Any, list[str]]:
    """Read a 0/1 mask, reprojecting to WGS84 at the ingest boundary if needed."""
    warnings: list[str] = []
    with rasterio.open(path) as src:
        if src.crs is None:
            warnings.append(
                f"{path.name} declares no CRS; assuming EPSG:4326 as per the contract"
            )
            band, transform = src.read(1), src.transform
        elif src.crs.to_epsg() != 4326:
            warnings.append(f"{path.name} is {src.crs}; reprojected to EPSG:4326 on read")
            with WarpedVRT(src, crs=WGS84, resampling=Resampling.nearest) as vrt:
                band, transform = vrt.read(1), vrt.transform
        else:
            band, transform = src.read(1), src.transform

        nodata = src.nodata

    mask = np.isfinite(band) & (band > 0) if band.dtype.kind == "f" else band > 0
    if nodata is not None:
        mask &= band != nodata
    return mask, transform, warnings


def _read_db_band(
    path: Path, mask_shape: tuple[int, int], mask_transform
) -> tuple[np.ndarray | None, float | None, list[str]]:
    """Read the Sigma0 dB band, resampled onto the mask's grid when it differs."""
    warnings: list[str] = []
    with rasterio.open(path) as src:
        src_crs = src.crs or WGS84
        aligned = (
            src.shape == mask_shape
            and src.transform.almost_equals(mask_transform)
            and (src.crs is None or src.crs.to_epsg() == 4326)
        )
        if aligned:
            return src.read(1).astype("float32"), src.nodata, warnings

        warnings.append(
            f"{path.name} is not on the mask grid; resampled onto it for the "
            "damping-ratio calculation"
        )
        dest = np.full(mask_shape, np.nan, dtype="float32")
        reproject(
            source=rasterio.band(src, 1),
            destination=dest,
            src_transform=src.transform,
            src_crs=src_crs,
            dst_transform=mask_transform,
            dst_crs=WGS84,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        return dest, None, warnings


def _resolve_db_path(
    scene_meta: dict[str, Any], meta_path: Path, override: str | Path | None
) -> Path | None:
    """Locate the calibrated dB scene: explicit override, else scene_meta['file_path']."""
    if override:
        return require_file(override, what="dB scene GeoTIFF")

    declared = scene_meta.get("file_path") or scene_meta.get("scene_path")
    if not declared:
        return None

    candidate = Path(declared)
    if not candidate.is_absolute():
        candidate = meta_path.parent / candidate
    return candidate if candidate.is_file() else None


# ------------------------------------------------------------------- metadata -----
def _scene_id(scene_meta: dict[str, Any]) -> str:
    scene_id = scene_meta.get("scene_id")
    if not scene_id:
        raise missing_input("scene metadata has no 'scene_id'", keys=sorted(scene_meta))
    return str(scene_id)


def _detected_utc(scene_meta: dict[str, Any]) -> str:
    for key in _TIME_KEYS:
        if scene_meta.get(key):
            try:
                return format_utc(parse_utc(scene_meta[key], field=key))
            except ValueError as exc:
                # A naive local timestamp is the handbook's named IST trap - reject it
                # as a declared input error rather than letting it crash the run.
                raise missing_input(str(exc), key=key, value=scene_meta[key]) from exc
    raise missing_input(
        "scene metadata has no acquisition time", expected_any_of=list(_TIME_KEYS)
    )


def _confidence(
    scene_meta: dict[str, Any], override: float | None, status: Status
) -> float | None:
    """scene metadata -> --confidence -> null + warning.

    Engine A does not compute confidence; it originates in Indhu's /detect response
    ({scene_id, mask_path, confidence, ...}) and is carried through to the contract.
    """
    value = scene_meta.get("confidence")
    if value is None:
        value = override
    if value is None:
        status.warn(
            "no detection confidence supplied (absent from scene metadata and no "
            "--confidence given); 'confidence' emitted as null"
        )
        return None
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise missing_input(f"confidence {value!r} is not a number") from exc
    if not 0.0 <= value <= 1.0:
        raise missing_input(f"confidence {value} is outside [0, 1]", confidence=value)
    return value


def _slick_id_prefix(scene_id: str, override: str | None) -> str:
    """Default prefix is the scene id's trailing token, matching the handbook example
    (``S1A_IW_GRDH_20170202T0039_DEMO-A`` -> ``DEMO-A_slick_01``). Override it with
    ``--slick-id-prefix`` when ids must be unique across scenes.
    """
    if override:
        return override
    tail = scene_id.rsplit("_", 1)[-1]
    return tail or scene_id


# --------------------------------------------------------------------- feature ----
def _geometry(polygon, status: Status) -> dict[str, Any]:
    """Shapely geometry -> GeoJSON dict, coordinates rounded to ~0.1 m."""
    if polygon.geom_type == "MultiPolygon":
        parts = sorted(polygon.geoms, key=lambda p: p.area, reverse=True)
        status.warn(
            f"slick vectorised into {len(parts)} disjoint parts; the contract carries a "
            "single Polygon, so only the largest part is written"
        )
        polygon = parts[0]

    geom = mapping(polygon)
    geom["coordinates"] = [
        [[round(float(x), 6), round(float(y), 6)] for x, y in ring]
        for ring in geom["coordinates"]
    ]
    return geom


def _properties(
    slick,
    *,
    slick_id: str,
    scene_id: str,
    detected_utc: str,
    confidence: float | None,
    damping_db: float | None,
    age,
) -> dict[str, Any]:
    return {
        "slick_id": slick_id,
        "scene_id": scene_id,
        "detected_utc": detected_utc,
        "confidence": confidence,
        "area_km2": round(slick.area_km2, 3),
        "perimeter_km": round(slick.perimeter_km, 3),
        "centroid": [
            round(slick.centroid_lonlat[0], 6),
            round(slick.centroid_lonlat[1], 6),
        ],
        "major_axis_km": round(slick.major_axis_km, 3),
        "minor_axis_km": round(slick.minor_axis_km, 3),
        "orientation_deg": round(slick.orientation_deg, 1),
        "damping_ratio_db": None if damping_db is None else round(damping_db, 2),
        "age_hours_est": age.age_hours,
        "age_method": age.method,
        "age_confidence": AGE_CONFIDENCE,
    }


# ---------------------------------------------------------------------- engine ----
def characterise(
    mask_path: str | Path,
    scene_meta_path: str | Path,
    out_path: str | Path,
    *,
    scene_db_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    confidence: float | None = None,
    slick_id_prefix: str | None = None,
) -> dict[str, Any]:
    """Run Engine A. Returns the status object of handbook §4.5.

    Writes ``out_path`` only on success; on a declared failure the status carries the
    error class and no file is produced.
    """
    status = Status(PRIMARY)
    try:
        mask_file = require_file(mask_path, what="mask GeoTIFF")
        meta_file = require_file(scene_meta_path, what="scene metadata JSON")
        scene_meta = read_json(meta_file, what="scene metadata JSON")

        config = read_yaml(config_path, what="characterise config") if Path(
            config_path
        ).is_file() else {}
        if not config:
            status.warn(f"config {config_path} not found; using built-in defaults")

        scene_id = _scene_id(scene_meta)
        detected_utc = _detected_utc(scene_meta)
        conf = _confidence(scene_meta, confidence, status)
        prefix = _slick_id_prefix(scene_id, slick_id_prefix)

        mask, transform, mask_warnings = _read_mask(mask_file)
        for w in mask_warnings:
            status.warn(w)

        if not mask.any():
            raise empty_mask(
                "mask contains no oil pixels", path=str(mask_file), scene_id=scene_id
            )

        geometry_cfg = dict(config.get("geometry", {}))
        slicks, geom_warnings = extract_slicks(mask, transform, **geometry_cfg)
        for w in geom_warnings:
            status.warn(w)

        if not slicks:
            raise empty_mask(
                "no slick survived the minimum-area threshold",
                path=str(mask_file),
                scene_id=scene_id,
                min_area_km2=geometry_cfg.get("min_area_km2", 0.05),
            )

        # dB band is optional: without it the damping ratio is null and the age method
        # degrades from "damping+fay" to "fay".
        db, db_nodata = None, None
        db_file = _resolve_db_path(scene_meta, meta_file, scene_db_path)
        if db_file is None:
            status.warn(
                "no dB backscatter band available; damping ratio omitted and the age "
                "estimate falls back to area-only Fay"
            )
        else:
            db, db_nodata, db_warnings = _read_db_band(db_file, mask.shape, transform)
            for w in db_warnings:
                status.warn(w)

        labelled = sk_label(mask, connectivity=2) if db is not None else None
        fay_params = FayParams.from_config(config.get("fay"))
        damping_cfg = dict(config.get("damping", {}))

        features = []
        for index, slick in enumerate(slicks, start=1):
            damping_db = None
            if db is not None:
                result, damp_warnings = compute_damping(
                    db, labelled == slick.label, mask, nodata=db_nodata, **damping_cfg
                )
                for w in damp_warnings:
                    status.warn(f"slick {index:02d}: {w}")
                if result is not None:
                    damping_db = result.damping_db

            age, age_warnings = estimate_age(slick.area_km2, damping_db, fay_params)
            for w in age_warnings:
                status.warn(f"slick {index:02d}: {w}")

            features.append(
                {
                    "type": "Feature",
                    "geometry": _geometry(slick.polygon, status),
                    "properties": _properties(
                        slick,
                        slick_id=f"{prefix}_slick_{index:02d}",
                        scene_id=scene_id,
                        detected_utc=detected_utc,
                        confidence=conf,
                        damping_db=damping_db,
                        age=age,
                    ),
                }
            )

        document = {"type": "FeatureCollection", "features": features}
        validate_slick(document)          # contract breaks fail here, not at integration
        written = write_json(out_path, document)
        status.add_output("slick", str(written))
        return status.to_dict()

    except RasterioIOError as exc:
        return status.fail(
            missing_input(f"raster could not be opened: {exc}")
        ).to_dict()
    except EngineError as err:
        return status.fail(err).to_dict()
