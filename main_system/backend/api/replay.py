"""Incident-replay data endpoints.

Everything the cinematic replay renders comes from these routes, and every
route reads the same files the pipeline wrote -- the replay is an accelerated
visualisation of the actual run, never a parallel animation with its own data.

Three things live here:

  * scene_png / mask_png -- the SAR scene and its detection mask rendered as
    web images on the scene's own bbox, so the UI can show the real imagery
    the detector saw and reveal the real mask over it.
  * forcing_field       -- the wind / current grids the drift engine used,
    downsampled for browser particle animation. Resolved with the SAME
    resolver the pipeline uses, so the arrows on screen are the forcing that
    actually moved the particles, not a decorative flow field.
  * vessel enrichment lives in analytics.vessels_geojson (per-point times,
    geodesic distances) -- see that module.
"""
from __future__ import annotations

import io
import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Response

from backend.core.config import get_settings

router = APIRouter()
settings = get_settings()
REPO_ROOT = settings.data_root.parent


def _run_dir(run_id: str) -> Path:
    root = settings.runs_root.resolve()
    d = (root / run_id).resolve()
    if not str(d).startswith(str(root)) or not d.is_dir():
        raise HTTPException(404, "unknown run")
    return d


def _scene_raster(run_id: str) -> Path:
    """The raster this run actually detected on, from its own manifest."""
    d = _run_dir(run_id)
    manifest = d / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        p = Path(str(m.get("scene_path", "")))
        if p.exists():
            return p
    raise HTTPException(404, "scene raster not recorded in this run's manifest")


# --------------------------------------------------------------------------
# SAR imagery
# --------------------------------------------------------------------------


PNG_HEADERS = {"Cache-Control": "public, max-age=86400"}


@router.get("/runs/{run_id}/scene_png")
def scene_png(run_id: str, size: int = 1024):
    """The scene's Sigma0 dB as an 8-bit greyscale PNG.

    Normalised with the shared training constants, the same mapping the model
    sees -- so what the analyst looks at IS the model's input, not a prettier
    re-stretch of it.
    """
    import rasterio
    from PIL import Image

    from ml.config import load_config, db_to_uint8

    cfg = load_config()
    src_path = _scene_raster(run_id)
    with rasterio.open(src_path) as src:
        band = min(int(cfg.sar.primary_band), src.count)
        # Decimated read: a 2048px scene at 1024 is plenty for a basemap layer.
        scale = max(1, math.ceil(max(src.width, src.height) / max(size, 64)))
        db = src.read(band, out_shape=(src.height // scale, src.width // scale)
                      ).astype(np.float32)

    grey = db_to_uint8(np.where(np.isfinite(db), db, cfg.sar.db_min), cfg)
    buf = io.BytesIO()
    Image.fromarray(grey, mode="L").save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers=PNG_HEADERS)


@router.get("/runs/{run_id}/mask_png")
def mask_png(run_id: str):
    """The run's detection mask as a transparent PNG (amber where oil).

    Rendered from raw_mask.tif -- the pixels the segmenter actually flagged --
    so the reveal animation shows the model's output, not a redrawn shape.
    """
    import rasterio
    from PIL import Image

    d = _run_dir(run_id)
    path = d / "raw_mask.tif"
    if not path.exists():
        raise HTTPException(404, "no raw_mask.tif in this run")
    with rasterio.open(path) as src:
        mask = src.read(1) > 0

    h, w = mask.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[mask] = [245, 158, 11, 185]
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers=PNG_HEADERS)


# --------------------------------------------------------------------------
# forcing field
# --------------------------------------------------------------------------


# Variable-name pairs seen across the providers in the chain: CMEMS writes
# uo/vo, ERA5 and Open-Meteo u10/v10, the engine sample fixtures plain u/v.
UV_NAMES = [("uo", "vo"), ("u10", "v10"), ("u", "v"),
            ("water_u", "water_v"),
            ("eastward_sea_water_velocity", "northward_sea_water_velocity")]


def _field_json(path: Path, kind: str,
                max_cells: int = 16, max_times: int = 10) -> Optional[dict]:
    """One NetCDF grid -> a compact JSON field for browser animation."""
    try:
        import xarray as xr

        with xr.open_dataset(path) as ds:
            uvar = vvar = None
            for u_, v_ in UV_NAMES:
                if u_ in ds and v_ in ds:
                    uvar, vvar = u_, v_
                    break
            if uvar is None:
                return None
            lat_n = "lat" if "lat" in ds.coords else "latitude"
            lon_n = "lon" if "lon" in ds.coords else "longitude"
            ds = ds.sortby(lat_n)
            import math as _m
            t_stride = max(1, _m.ceil(ds.sizes.get("time", 1) / max_times))
            y_stride = max(1, _m.ceil(ds.sizes[lat_n] / max_cells))
            x_stride = max(1, _m.ceil(ds.sizes[lon_n] / max_cells))
            sub = ds.isel(time=slice(None, None, t_stride),
                          **{lat_n: slice(None, None, y_stride),
                             lon_n: slice(None, None, x_stride)})
            u = np.nan_to_num(sub[uvar].values.astype(np.float32))
            v = np.nan_to_num(sub[vvar].values.astype(np.float32))
            if u.ndim == 2:                       # no time axis
                u, v = u[None], v[None]
                times = []
            else:
                times = [str(t)[:19] + "Z" for t in sub["time"].values]
            speed = np.hypot(u, v)
            return {
                "file": path.name,
                "times_utc": times,
                "lats": [round(float(x), 4) for x in sub[lat_n].values],
                "lons": [round(float(x), 4) for x in sub[lon_n].values],
                "u": np.round(u, 3).tolist(),
                "v": np.round(v, 3).tolist(),
                "mean_speed": round(float(speed.mean()), 3),
                "max_speed": round(float(speed.max()), 3),
            }
    except Exception:
        return None


def _recorded_forcing(d: Path) -> Dict[str, dict]:
    """What the drift stage recorded about its forcing, from the run's own
    origin cloud (else forecast) metadata: provider, file name and the
    grid's normalisation stamp -- the NetCDF ``history`` attribute."""
    for name in ("origin_cloud.geojson", "forecast.geojson"):
        f = d / name
        if not f.exists():
            continue
        try:
            forcing = (json.loads(f.read_text(encoding="utf-8"))
                       .get("metadata", {}).get("forcing") or {})
        except Exception:
            continue
        out = {k: forcing[k] for k in ("currents", "wind")
               if isinstance(forcing.get(k), dict)}
        if out:
            return out
    return {}


def _history(path: Path) -> Optional[str]:
    try:
        import xarray as xr
        with xr.open_dataset(path) as ds:
            return ds.attrs.get("history")
    except Exception:
        return None


def _find_recorded_grid(pattern: str, stamp: Optional[str], d: Path,
                        scene_id: str) -> Optional[Path]:
    """The grid file whose normalisation stamp equals the one the drift
    recorded -- i.e. the exact field it integrated -- or None."""
    if not stamp:
        return None
    from backend.services.pipeline.run import METOCEAN_CACHE, REPO_ROOT
    roots = [d, REPO_ROOT / "data" / "metocean" / scene_id,
             REPO_ROOT / "data" / "metocean", METOCEAN_CACHE]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.glob(f"**/{pattern}")):
            key = f.resolve()
            if key in seen:
                continue
            seen.add(key)
            if _history(f) == stamp:
                return key
    return None


@router.get("/runs/{run_id}/forcing_field")
def forcing_field(run_id: str):
    """Wind and current grids for this run.

    The grid served is the one the drift INTEGRATED whenever that can be
    established: the run records each grid's normalisation stamp, and the
    file carrying the same stamp is found and served (``matches_run: true``).
    Only when no file carries it does this fall back to resolving forcing the
    way the pipeline would today -- which can pick a different grid if the
    cache has changed since the run -- and then says so (``matches_run:
    false``, or ``null`` when the run recorded nothing to compare against).
    A null section means no forcing was available at all; the UI states that
    instead of animating a fiction.
    """
    # HDF5 is not thread-safe in this build. Pipeline runs are serialised by
    # routes._pipeline_gate for exactly that reason, but this endpoint opened
    # the same NetCDF grids outside it: asked for while a run's drift stage
    # was integrating (the workspace does that the moment a live run is handed
    # to it), the process died with a segmentation fault. So it takes the gate
    # too -- without waiting, because a run can hold it for minutes -- and
    # answers 503 instead, which the workspace retries.
    from backend.api.routes import _pipeline_gate

    if not _pipeline_gate.acquire(blocking=False):
        raise HTTPException(
            503, "a pipeline run is reading the forcing grids; they are served once it finishes",
            headers={"Retry-After": "5"})
    try:
        with _FORCING_LOCK:
            return _forcing_field_now(run_id)
    finally:
        _pipeline_gate.release()


_FORCING_LOCK = threading.Lock()


def _forcing_field_now(run_id: str):
    from backend.services.pipeline.run import resolve_metocean

    d = _run_dir(run_id)
    meta = None
    mp = d / "scene_meta.json"
    if mp.exists():
        meta = json.loads(mp.read_text(encoding="utf-8"))
    scene_id = (meta or {}).get("scene_id", "")
    recorded = _recorded_forcing(d)
    resolved_currents, resolved_wind = resolve_metocean(meta, d)

    def section(kind: str, pattern: str, resolved: Optional[Path]):
        rec = recorded.get(kind) or {}
        stamp = rec.get("normalised")
        exact = _find_recorded_grid(pattern, stamp, d, scene_id)
        path = exact or resolved
        if path is None:
            return None
        field = _field_json(path, kind)
        if field is None:
            return None
        if exact is not None:
            matches = True
        elif stamp:
            matches = _history(path) == stamp
        else:
            matches = None
        field.update({
            "provider": rec.get("provider"),
            "recorded_normalised": stamp,
            "matches_run": matches,
        })
        return field

    return {
        "run_id": run_id,
        "wind": section("wind", "wind*.nc", resolved_wind),
        "currents": section("currents", "currents*.nc", resolved_currents),
    }
