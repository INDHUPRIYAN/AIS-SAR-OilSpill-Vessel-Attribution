"""Engine 1 -- Satellite Ingest & Preprocess."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from backend.services.hindcast.engines.base import Engine, EngineError, EngineResult, PipelineContext
from backend.services.hindcast.geo import geojson_of, load_geometry, shape_stats


class SlickSegmenter(Protocol):
    """Where a segmentation model plugs in. Out of scope here: the pipeline is
    handed a polygon, and `ProvidedPolygonSegmenter` simply returns it."""

    def segment(self, scene_path: Optional[Path], meta: dict[str, Any]) -> BaseGeometry: ...


class ProvidedPolygonSegmenter:
    def __init__(self, polygon_geojson: dict[str, Any]) -> None:
        self._geojson = polygon_geojson

    def segment(self, scene_path: Optional[Path], meta: dict[str, Any]) -> BaseGeometry:
        return load_geometry(self._geojson)


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def damping_proxy(scene_path: Path, slick: BaseGeometry, out_path: Path) -> Optional[dict[str, float]]:
    """Per-pixel damping-ratio proxy, written as a 0..1 GeoTIFF over the slick's bbox.

    Damping ratio = clean-sea backscatter / slick backscatter. With sigma0 in dB
    that is a difference: background median minus the pixel. Thick oil damps
    more, so high values mark where most of the mass is and get more particles.
    """
    with rasterio.open(scene_path) as src:
        if src.crs is None or src.crs.to_epsg() != 4326:
            return None   # reprojection is the ingest of a real GRD product; not guessed here
        west, south, east, north = slick.buffer(0.02).bounds
        window = from_bounds(west, south, east, north, src.transform).round_offsets().round_lengths()
        band = src.read(1, window=window, boundless=True, fill_value=np.nan).astype(np.float32)
        transform = src.window_transform(window)
    if band.size == 0 or not np.isfinite(band).any():
        return None
    from rasterio import features

    inside = features.rasterize([(slick, 1)], out_shape=band.shape, transform=transform,
                                fill=0, dtype="uint8").astype(bool)
    if not inside.any() or inside.all():
        return None
    in_db = bool(np.nanmin(band) < 0)   # sigma0 in dB is negative over sea
    background = float(np.nanmedian(band[~inside]))
    ratio = (background - band) if in_db else (background / np.maximum(band, 1e-6))
    ratio = np.where(inside & np.isfinite(ratio), ratio, 0.0)
    top = float(np.percentile(ratio[inside], 98)) or 1.0
    norm = np.clip(ratio / top, 0.0, 1.0).astype(np.float32)
    profile = {"driver": "GTiff", "height": norm.shape[0], "width": norm.shape[1], "count": 1,
               "dtype": "float32", "crs": "EPSG:4326", "transform": transform, "compress": "deflate"}
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(norm, 1)
    return {"background": background, "mean_damping": float(norm[inside].mean())}


def damping_weight_fn(path: Optional[str]) -> Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]]:
    if not path or not Path(path).exists():
        return None
    with rasterio.open(path) as src:
        band, transform = src.read(1), src.transform

    def weight(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        # north-up raster: x = a*col + c, y = e*row + f
        col = (np.asarray(lon) - transform.c) / transform.a
        row = (np.asarray(lat) - transform.f) / transform.e
        col = np.clip(np.floor(col).astype(int), 0, band.shape[1] - 1)
        row = np.clip(np.floor(row).astype(int), 0, band.shape[0] - 1)
        return band[row, col]

    return weight


class IngestEngine(Engine):
    id = "ingest_engine"
    name = "Satellite Ingest & Preprocess"
    description = "Parses the Sentinel-1 scene metadata and slick polygon; extracts the damping-ratio proxy."
    stage = "Ingest"
    order = 1
    icon = "Satellite"
    metric_keys = [{"key": "scene_time", "label": "Scene time T"},
                   {"key": "area_km2", "label": "Polygon area", "unit": "km²"},
                   {"key": "fragments", "label": "Fragments"}]

    def run(self, ctx: PipelineContext) -> EngineResult:
        meta: dict[str, Any] = dict(ctx.request.get("scene_meta") or {})
        ctx.emit.step("reading scene metadata", 5)
        meta_path = meta.get("metadata_path")
        if meta_path:
            file_meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
            meta = {**file_meta, **{k: v for k, v in meta.items() if v is not None}}

        raw_time = meta.get("acquired_utc") or meta.get("capture_time")
        if not raw_time:
            raise EngineError("scene_meta has no 'acquired_utc': the capture time T anchors the whole hindcast")
        scene_time = parse_time(raw_time)

        ctx.emit.step("validating slick polygon", 25)
        polygon = ctx.request.get("slick_polygon_geojson")
        if not polygon:
            raise EngineError("no slick polygon supplied and no segmentation model is configured")
        try:
            slick = ProvidedPolygonSegmenter(polygon).segment(None, meta)
        except ValueError as exc:
            raise EngineError(str(exc)) from exc
        stats = shape_stats(slick, seed=ctx.config.seed)

        footprint = meta.get("footprint")
        if footprint:
            aoi = load_geometry(footprint) if isinstance(footprint, dict) else box(*footprint)
        else:
            aoi = box(*slick.buffer(0.25).bounds)
        if not aoi.intersects(slick):
            raise EngineError("the slick polygon lies outside the scene footprint")

        ctx.emit.step("extracting damping-ratio proxy", 60)
        damping_path: Optional[str] = None
        scene_path = meta.get("scene_path")
        if scene_path and Path(scene_path).exists():
            info = damping_proxy(Path(scene_path), slick, ctx.path("damping.tif"))
            if info:
                damping_path = str(ctx.path("damping.tif"))
                ctx.emit.log(f"damping proxy: background {info['background']:.2f}, "
                             f"mean in-slick damping {info['mean_damping']:.2f}")
            else:
                ctx.emit.log("scene raster is not EPSG:4326 or does not cover the slick; seeding uniformly")
        else:
            ctx.emit.log("no scene GeoTIFF supplied; particles will be seeded uniformly in the polygon")

        ctx.state.update({
            "scene_id": meta.get("scene_id"),
            "scene_time": scene_time.isoformat(),
            "incidence_angle_deg": meta.get("incidence_angle_deg"),
            "sar_wind": meta.get("sar_wind"),
            "slick_geojson": geojson_of(slick),
            "slick_stats": stats.to_dict(),
            "aoi_geojson": geojson_of(aoi),
            "damping_path": damping_path,
        })
        metrics = {"scene_time": scene_time.strftime("%Y-%m-%d %H:%MZ"),
                   "area_km2": round(stats.area_km2, 2), "fragments": stats.fragments,
                   "damping_raster": bool(damping_path)}
        return EngineResult(metrics=metrics,
                            summary=f"T={scene_time.isoformat()}, slick {stats.area_km2:.2f} km²")
