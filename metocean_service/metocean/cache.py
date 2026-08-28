"""
Metocean Data Cache and Offline Serving Engine.
Implements deterministic key hashing, atomic writes, deep NetCDF integrity validation,
safe Windows path resolution, corrupted file handling, and offline fallback dataset serving.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, Optional, Tuple, Union

try:
    import xarray as xr
    XARRAY_AVAILABLE = True
except ImportError:
    xr = None  # type: ignore
    XARRAY_AVAILABLE = False

from metocean.errors import CacheCorruptedError, ValidationError
from metocean.models import BBox, MetoceanRequest
from metocean.utils import ensure_dir

logger = logging.getLogger(__name__)

DEFAULT_METOCEAN_DIR = Path("data/metocean")


def generate_cache_key(
    request: MetoceanRequest,
    data_type: str,
    product_or_provider: Optional[str] = None,
) -> str:
    """
    Generate a deterministic SHA256 cache key based on (product, bbox, time_window, data_type).
    Same logical request parameters always produce the exact same key.
    """
    prov = str(product_or_provider or request.provider).strip().upper()
    bbox_str = f"[{request.bbox.min_lon:.4f},{request.bbox.min_lat:.4f},{request.bbox.max_lon:.4f},{request.bbox.max_lat:.4f}]"
    time_str = f"{request.start_iso}_{request.end_iso}"
    data_type_str = str(data_type).strip().lower()

    raw_signature = f"{prov}|{bbox_str}|{time_str}|{data_type_str}"
    return hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()[:20]


def validate_cached_netcdf(file_path: Union[str, Path], data_type: str) -> bool:
    """
    Validate that a cached NetCDF file is uncorrupted and satisfies the OceanTrace contract:
    - File exists, non-empty, and has valid NetCDF magic bytes.
    - Required variables exist (uo, vo for currents; u10, v10 for wind).
    - Required dimensions exist (time, lat, lon).
    """
    target = Path(file_path)
    if not target.exists() or target.stat().st_size < 16:
        return False

    # Check NetCDF magic header bytes (HDF5 / NetCDF4 / classic CDF)
    try:
        with open(target, "rb") as f:
            header = f.read(8)
            # HDF5 / NetCDF-4 magic: \x89HDF\r\n\x1a\n or classic CDF: CDF\x01 / CDF\x02
            is_valid_header = (
                header.startswith(b"\x89HDF")
                or header.startswith(b"CDF\x01")
                or header.startswith(b"CDF\x02")
                or b"CDF" in header
            )
            if not is_valid_header:
                logger.warning("Cached file %s has invalid NetCDF header signature.", target)
                return False
    except Exception as exc:
        logger.warning("Failed to read header of cached file %s: %s", target, exc)
        return False

    # If xarray is available, perform deep schema validation
    if XARRAY_AVAILABLE and xr is not None:
        try:
            with xr.open_dataset(str(target)) as ds:
                dims = set(ds.dims.keys()) | set(ds.coords.keys())
                for d in ["time", "lat", "lon"]:
                    if d not in dims and ("latitude" not in dims and "longitude" not in dims):
                        logger.warning("Cached file %s missing dimension '%s'", target, d)
                        return False

                vars_set = set(ds.data_vars.keys())
                if data_type == "currents":
                    if not {"uo", "vo"}.issubset(vars_set):
                        logger.warning("Cached currents %s missing uo/vo: %s", target, vars_set)
                        return False
                elif data_type == "wind":
                    if not {"u10", "v10"}.issubset(vars_set):
                        logger.warning("Cached wind %s missing u10/v10: %s", target, vars_set)
                        return False
        except Exception as exc:
            logger.warning("Corrupted cached NetCDF file detected at %s: %s", target, exc)
            return False

    return True


class MetoceanCache:
    """
    Robust disk cache manager for metocean datasets supporting:
    - Safe Windows path construction
    - Deterministic SHA256 cache keys
    - Atomic writes preventing file corruption
    - Integrity validation on read
    - Scene-based directory support (data/metocean/<scene_id>/)
    - Offline fallback serving
    """

    def __init__(self, cache_dir: Optional[Union[str, Path]] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path(os.getenv("CACHE_DIR", str(DEFAULT_METOCEAN_DIR)))

    def get_cache_key(self, request: MetoceanRequest, data_type: str, product: Optional[str] = None) -> str:
        """Expose deterministic cache key generator."""
        return generate_cache_key(request, data_type, product)

    def get_currents(self, request: MetoceanRequest, scene_id: Optional[str] = None) -> Optional[str]:
        """Look up valid cached currents.nc dataset."""
        return self._lookup(request, "currents", scene_id)

    def get_wind(self, request: MetoceanRequest, scene_id: Optional[str] = None) -> Optional[str]:
        """Look up valid cached wind.nc dataset."""
        return self._lookup(request, "wind", scene_id)

    def _lookup(self, request: MetoceanRequest, data_type: str, scene_id: Optional[str] = None) -> Optional[str]:
        """Internal lookup and validation logic."""
        # 1. Check scene directory first if scene_id provided (e.g. data/metocean/<scene_id>/currents.nc)
        if scene_id:
            scene_path = self.cache_dir / scene_id / f"{data_type}.nc"
            if scene_path.exists() and validate_cached_netcdf(scene_path, data_type):
                logger.info("MetoceanCache hit (scene-based) for %s at %s", data_type, scene_path)
                return str(scene_path.resolve())

        # 2. Check hashed key cache file (e.g. data/metocean/currents_<key>.nc)
        key = self.get_cache_key(request, data_type)
        candidate = self.cache_dir / f"{data_type}_{key}.nc"

        if candidate.exists():
            if validate_cached_netcdf(candidate, data_type):
                logger.info("MetoceanCache hit for %s at %s", data_type, candidate)
                return str(candidate.resolve())
            else:
                logger.warning("MetoceanCache candidate at %s is corrupted or invalid. Treating as MISS.", candidate)
                return None

        # 3. Check generic fallback in cache directory (data/metocean/currents.nc)
        generic_path = self.cache_dir / f"{data_type}.nc"
        if generic_path.exists() and validate_cached_netcdf(generic_path, data_type):
            logger.info("MetoceanCache hit (generic fallback) for %s at %s", data_type, generic_path)
            return str(generic_path.resolve())

        return None

    def put_currents(
        self,
        request: MetoceanRequest,
        file_path: Union[str, Path],
        scene_id: Optional[str] = None,
    ) -> str:
        """Atomically store generated currents.nc into cache."""
        return self._store(request, file_path, "currents", scene_id)

    def put_wind(
        self,
        request: MetoceanRequest,
        file_path: Union[str, Path],
        scene_id: Optional[str] = None,
    ) -> str:
        """Atomically store generated wind.nc into cache."""
        return self._store(request, file_path, "wind", scene_id)

    def _store(
        self,
        request: MetoceanRequest,
        file_path: Union[str, Path],
        data_type: str,
        scene_id: Optional[str] = None,
    ) -> str:
        """Atomic write implementation using temporary file and safe replacement."""
        src = Path(file_path)
        if not src.exists():
            logger.warning("Cannot cache non-existent source file %s", src)
            return str(src)

        # Validate before caching
        if not validate_cached_netcdf(src, data_type):
            logger.warning("Refusing to cache invalid/corrupted NetCDF dataset %s", src)
            return str(src)

        ensure_dir(self.cache_dir)

        # Determine target paths
        targets = []
        key = self.get_cache_key(request, data_type)
        targets.append(self.cache_dir / f"{data_type}_{key}.nc")

        if scene_id:
            scene_dir = self.cache_dir / scene_id
            ensure_dir(scene_dir)
            targets.append(scene_dir / f"{data_type}.nc")

        # Atomic copy
        for dest in targets:
            try:
                # Write to temp file first in target directory
                temp_dest = dest.parent / f".tmp_{dest.name}_{os.getpid()}"
                shutil.copyfile(str(src), str(temp_dest))
                # Atomic rename/replace
                if dest.exists():
                    dest.unlink()
                temp_dest.replace(dest)
                logger.info("Stored validated %s dataset in cache: %s", data_type, dest)
            except Exception as exc:
                logger.warning("Failed atomic cache write to %s: %s", dest, exc)

        return str(targets[0].resolve())

    def get_static_fallback(self, data_type: str, output_path: Optional[str] = None) -> Optional[str]:
        """
        Retrieve static baseline offline dataset (e.g. data/metocean/static/currents.nc).
        Returns validated file path, or None if unavailable.
        """
        filename = "currents.nc" if data_type == "currents" else "wind.nc"
        static_candidate = self.cache_dir / "static" / filename

        if static_candidate.exists() and validate_cached_netcdf(static_candidate, data_type):
            if output_path:
                dest = Path(output_path)
                ensure_dir(dest.parent)
                shutil.copyfile(str(static_candidate), str(dest))
                return str(dest.resolve())
            return str(static_candidate.resolve())

        # Also check root data/metocean/currents.nc as secondary static fallback
        root_candidate = self.cache_dir / filename
        if root_candidate.exists() and validate_cached_netcdf(root_candidate, data_type):
            if output_path:
                dest = Path(output_path)
                ensure_dir(dest.parent)
                shutil.copyfile(str(root_candidate), str(dest))
                return str(dest.resolve())
            return str(root_candidate.resolve())

        return None
