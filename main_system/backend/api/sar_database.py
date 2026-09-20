"""SAR Image Database: every SAR scene this host actually holds, with the
metadata that makes it a scene rather than a picture -- and an upload path
that refuses to invent the metadata a user did not supply.

What is listed
    * the curated catalogue (config/scene_catalog.json),
    * every ``scene_meta*.json`` under data/scenes whose raster exists,
    * uploads (data/uploads/<id>/scene_meta.json),
  joined to the runs that have analysed each scene (data/runs/*/scene_meta.json).

The honesty this module exists to keep
    A Sentinel-1 product identifier carries its acquisition time, and its
    GeoTIFF carries its georeference: those scenes are MEASURED. The SAR corpus
    chips (Trujillo) are real backscatter but ship neither -- the rasters hold
    only AREA_OR_POINT -- so the bbox and ``acquired_utc`` in their scene_meta
    were ASSIGNED for demonstration. Everything downstream (the hindcast, the
    AIS window, where the slick draws on the map) inherits that. ``geo_basis``
    and ``time_basis`` say which is which on every row; the UI renders them
    verbatim. An upload is ``user_supplied`` unless the raster itself is
    georeferenced, in which case the raster's own bounds win.

Uploads never bypass the pipeline: a completed upload is a contract-clean
scene_meta.json (contracts/schemas/scene.py, extra keys forbidden) plus a
sidecar ``upload.json`` for everything the contract has no field for. The
caller then creates an investigation on it through the existing API, so the
existing detector, characteriser, drift and attribution engines run on it.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from backend.core.authz import current_user, require_role
from backend.core.config import get_settings

router = APIRouter()
settings = get_settings()
REPO_ROOT = settings.data_root.parent
SCENES_DIR = REPO_ROOT / "data" / "scenes"
UPLOADS_DIR = REPO_ROOT / "data" / "uploads"
THUMBS_DIR = REPO_ROOT / "data" / "cache" / "sar_thumbs"
RUNS_DIR = REPO_ROOT / "data" / "runs"
CATALOG_PATH = REPO_ROOT / "main_system" / "config" / "scene_catalog.json"

MAX_UPLOAD_BYTES = 1_500_000_000          # a full GRD scene in dB is ~1 GB
RASTER_SUFFIXES = {".tif", ".tiff"}
PICTURE_SUFFIXES = {".png", ".jpg", ".jpeg"}

# S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1
S1_PRODUCT = re.compile(
    r"^(S1[ABCD])_([A-Z0-9]{2})_([A-Z]{3})([FHM_])_(\d)S([SD])([HV])_(\d{8}T\d{6})_(\d{8}T\d{6})_(\d{6})")
PLATFORMS = {"S1A": "Sentinel-1A", "S1B": "Sentinel-1B", "S1C": "Sentinel-1C", "S1D": "Sentinel-1D"}

BASIS_NOTES = {
    "measured": "Read from the Sentinel-1 product: the identifier carries the acquisition time and the raster its georeference.",
    "assigned": "ASSIGNED for demonstration. This is real SAR backscatter from a research corpus that ships no georeference and "
                "no acquisition time; the position and time here were chosen, not measured, and everything derived from them "
                "(hindcast, AIS window, where the slick draws) inherits that.",
    "user_supplied": "Entered by the uploader. Not verified against the raster or any catalogue.",
    "raster": "Read from the uploaded raster's own georeference.",
    "synthetic": "Synthetic raster for pipeline smoke tests. Contains no oil and depicts no place.",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def _key(meta_path: Path) -> str:
    return hashlib.sha1(_relative(meta_path).encode("utf-8")).hexdigest()[:14]


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def product_facts(scene_id: Optional[str]) -> Dict[str, Any]:
    """What a Sentinel-1 product identifier states about itself, or nothing."""
    m = S1_PRODUCT.match(scene_id or "")
    if not m:
        return {}
    sat, mode, ptype, res, _lvl, dual, pol, start, _stop, orbit = m.groups()
    return {
        "platform": PLATFORMS.get(sat, sat),
        "mode": mode,
        "product_type": f"{ptype}{res if res != '_' else ''}",
        "polarisation_class": ("dual " if dual == "D" else "single ") + pol + "-transmit",
        "absolute_orbit": int(orbit),
        "sensing_start_utc": datetime.strptime(start, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def classify(meta: dict, meta_path: Path, catalog_entry: Optional[dict], upload: Optional[dict]) -> Dict[str, Any]:
    """provenance + the basis of the scene's time and place."""
    scene_id = str(meta.get("scene_id") or "")
    raster = str(meta.get("file_path") or "").replace("\\", "/").lower()
    parts = {p.lower() for p in meta_path.parts}
    if upload is not None:
        geo = "raster" if upload.get("georeferenced_raster") else "user_supplied"
        return {"provenance": "upload", "label": "UPLOADED", "geo_basis": geo, "time_basis": "user_supplied"}
    if "mocks" in parts or (catalog_entry or {}).get("provenance") == "mock" or str(meta.get("source")) == "synthetic":
        return {"provenance": "mock", "label": "SYNTHETIC", "geo_basis": "synthetic", "time_basis": "synthetic"}
    if product_facts(scene_id):
        return {"provenance": "sentinel1_real", "label": "REAL", "geo_basis": "measured", "time_basis": "measured"}
    corpus = "trujillo" in raster or "/raw/" in raster or (catalog_entry or {}).get("provenance", "").startswith("corpus")
    train = (catalog_entry or {}).get("provenance") == "corpus_train"
    if corpus or (catalog_entry or {}).get("time_basis") == "assigned":
        return {"provenance": "corpus_train" if train else "corpus", "label": "REFERENCE",
                "geo_basis": "assigned", "time_basis": "assigned"}
    # Nothing states where this came from: say so rather than guess "real".
    return {"provenance": "unclassified", "label": "UNVERIFIED", "geo_basis": "assigned", "time_basis": "assigned"}


_RUN_INDEX: Dict[str, Any] = {"at": 0.0, "by_scene": {}}


def runs_by_scene() -> Dict[str, List[dict]]:
    """scene_id -> runs that analysed it (newest first). Cached for 30 s: it
    is a directory walk, and the database page polls nothing."""
    if time.time() - _RUN_INDEX["at"] < 30:
        return _RUN_INDEX["by_scene"]
    by: Dict[str, List[dict]] = {}
    if RUNS_DIR.is_dir():
        for d in RUNS_DIR.iterdir():
            meta = _read_json(d / "scene_meta.json") if d.is_dir() else None
            if not meta or not meta.get("scene_id"):
                continue
            manifest = _read_json(d / "manifest.json") or {}
            by.setdefault(meta["scene_id"], []).append({
                "run_id": d.name,
                "sealed": bool(manifest.get("artefact_digest")),
                "finished_utc": manifest.get("finished_utc") or manifest.get("generated_utc"),
                "mtime": d.stat().st_mtime,
            })
    for rows in by.values():
        rows.sort(key=lambda r: (r["sealed"], r["mtime"]), reverse=True)
    _RUN_INDEX.update(at=time.time(), by_scene=by)
    return by


def _row(meta_path: Path, catalog_entry: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    meta = _read_json(meta_path)
    if not meta or not meta.get("scene_id"):
        return None
    upload = _read_json(meta_path.parent / "upload.json") if meta_path.parent.parent == UPLOADS_DIR else None
    raster = _resolve(str(meta.get("file_path") or "")) if meta.get("file_path") else None
    available = bool(raster and raster.exists())
    bbox = meta.get("bbox") if isinstance(meta.get("bbox"), list) and len(meta["bbox"]) == 4 else None
    facts = product_facts(meta.get("scene_id"))
    cls = classify(meta, meta_path, catalog_entry, upload)
    runs = runs_by_scene().get(meta["scene_id"], [])
    key = _key(meta_path)
    return {
        "key": key,
        "scene_id": meta["scene_id"],
        "label": (catalog_entry or {}).get("label") or (upload or {}).get("label"),
        "scene_meta_path": _relative(meta_path),
        "acquired_utc": meta.get("acquired_utc"),
        "bbox": bbox,
        "center": [round((bbox[0] + bbox[2]) / 2, 5), round((bbox[1] + bbox[3]) / 2, 5)] if bbox else None,
        "crs": meta.get("crs"),
        "platform": facts.get("platform"),
        "mode": facts.get("mode"),
        "product_type": facts.get("product_type") or (upload or {}).get("product_type"),
        "absolute_orbit": facts.get("absolute_orbit"),
        "orbit_direction": (upload or {}).get("orbit_direction"),
        "polarisation": meta.get("polarisation"),
        "pixel_spacing_m": meta.get("pixel_spacing_m"),
        "db_range": meta.get("db_range"),
        "provider_used": meta.get("provider_used"),
        "source": meta.get("source"),
        **cls,
        "geo_basis_note": BASIS_NOTES.get(cls["geo_basis"]),
        "time_basis_note": BASIS_NOTES.get(cls["time_basis"]),
        "trained_on": cls["provenance"] == "corpus_train",
        "notes": (catalog_entry or {}).get("notes") or (upload or {}).get("notes"),
        "caveats": (upload or {}).get("caveats") or [],
        "available": available,
        "unavailable_reason": None if available else ("scene_meta has no file_path" if not raster else f"raster missing: {_relative(raster)}"),
        "raster_bytes": raster.stat().st_size if available else None,
        "thumb_url": f"/api/sar/scenes/{key}/thumb" if available else None,
        "runs": len(runs),
        "latest_run_id": runs[0]["run_id"] if runs else None,
        "latest_run_sealed": runs[0]["sealed"] if runs else None,
        "uploaded_by": (upload or {}).get("uploaded_by"),
        "uploaded_utc": (upload or {}).get("uploaded_utc"),
    }


def all_rows() -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}

    def add(path: Path, entry: Optional[dict] = None):
        row = _row(path, entry)
        if row is None:
            return
        # One row per scene: a catalogue entry and a cached copy of the same
        # scene are the same scene. Prefer the one whose raster is present.
        old = seen.get(row["scene_id"])
        if old is None or (row["available"] and not old["available"]) or (entry and not old.get("label")):
            if old and not row.get("label"):
                row["label"] = old.get("label")
            seen[row["scene_id"]] = row

    catalog = (_read_json(CATALOG_PATH) or {}).get("scenes", [])
    for entry in catalog:
        if entry.get("scene_meta_path"):
            add(_resolve(entry["scene_meta_path"]), entry)
    if SCENES_DIR.is_dir():
        for p in sorted(list(SCENES_DIR.glob("**/scene_meta*.json")) + list(SCENES_DIR.glob("*.meta.json"))):
            add(p)
    if UPLOADS_DIR.is_dir():
        for p in sorted(UPLOADS_DIR.glob("*/scene_meta.json")):
            add(p)
    return sorted(seen.values(), key=lambda r: (r["acquired_utc"] or ""), reverse=True)


def basis_for_meta(meta: dict) -> Dict[str, Any]:
    """The basis of a run's scene time and place, for surfaces outside this page.

    A run carries a copy of its scene_meta but not where the scene came from,
    so the workspace and the printed report drew an ASSIGNED corpus position
    exactly like a measured Sentinel-1 one. The database row is the authority
    when the scene is still on this host; otherwise the meta is classified on
    its own, which can only ever be more cautious.
    """
    row = next((r for r in all_rows() if r["scene_id"] == meta.get("scene_id")), None)
    if row is None:
        cls = classify(meta, Path(str(meta.get("file_path") or "")), None, None)
        row = {**cls, "geo_basis_note": BASIS_NOTES.get(cls["geo_basis"]),
               "time_basis_note": BASIS_NOTES.get(cls["time_basis"]),
               "trained_on": cls["provenance"] == "corpus_train"}
    return {k: row.get(k) for k in ("provenance", "label", "geo_basis", "time_basis",
                                    "geo_basis_note", "time_basis_note", "trained_on")}


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


@router.get("/sar/scenes")
def list_scenes(
    q: Optional[str] = None,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    radius_km: float = Query(500, gt=0, le=20000),
    start: Optional[str] = None,
    end: Optional[str] = None,
    polarisation: Optional[str] = None,
    provider: Optional[str] = None,
    provenance: Optional[str] = None,
    available: Optional[bool] = None,
) -> Dict[str, Any]:
    """Scenes held on this host. Every filter is applied to real rows; an
    empty result is an empty result."""
    rows = all_rows()
    total = len(rows)

    def keep(r: dict) -> bool:
        if q:
            hay = " ".join(str(r.get(k) or "") for k in ("scene_id", "label", "notes", "provider_used")).lower()
            if q.lower() not in hay:
                return False
        if lat is not None and lon is not None:
            if not r["center"]:
                return False
            if _haversine_km(lat, lon, r["center"][1], r["center"][0]) > radius_km:
                return False
        if start and (r["acquired_utc"] or "") < start:
            return False
        if end and (r["acquired_utc"] or "9") > end:
            return False
        if polarisation and polarisation.upper() not in str(r.get("polarisation") or "").upper():
            return False
        if provider and provider.lower() not in str(r.get("provider_used") or "").lower():
            return False
        if provenance and r["provenance"] != provenance and r["label"].lower() != provenance.lower():
            return False
        if available is not None and r["available"] != available:
            return False
        return True

    out = [r for r in rows if keep(r)]
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    return {"scenes": out, "count": len(out), "total": total, "by_label": counts,
            "basis_notes": BASIS_NOTES}


@router.get("/sar/scenes/{key}")
def get_scene(key: str) -> Dict[str, Any]:
    for r in all_rows():
        if r["key"] == key:
            return r
    raise HTTPException(404, f"no scene with key {key}")


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


@router.get("/sar/scenes/{key}/thumb")
def scene_thumb(key: str, size: int = Query(320, ge=64, le=2048)):
    """The scene's sigma0 dB as a greyscale PNG, decimated on read -- never the
    full raster -- and normalised with the constants the model sees."""
    row = next((r for r in all_rows() if r["key"] == key), None)
    if row is None or not row["available"]:
        raise HTTPException(404, "no raster for this scene")
    headers = {"Cache-Control": "private, max-age=3600"}
    # A full Sentinel-1 scene is ~30k x 20k px with no overviews: one decimated
    # read costs ~20 s. Rendered once per (scene, size, raster mtime) and kept.
    size = 256 if size <= 256 else 640 if size <= 640 else 1024
    raster_path = _resolve((_read_json(_resolve(row["scene_meta_path"])) or {})["file_path"])
    cache = THUMBS_DIR / f"{key}_{size}_{int(raster_path.stat().st_mtime)}.png"
    if cache.exists():
        return Response(cache.read_bytes(), media_type="image/png", headers=headers)
    import numpy as np
    import rasterio
    from PIL import Image

    from ml.config import db_to_uint8, load_config

    cfg = load_config()
    with rasterio.open(raster_path) as src:
        band = min(int(cfg.sar.primary_band), src.count)
        scale = max(1, math.ceil(max(src.width, src.height) / size))
        db = src.read(band, out_shape=(max(1, src.height // scale), max(1, src.width // scale))).astype("float32")
    grey = db_to_uint8(np.where(np.isfinite(db), db, cfg.sar.db_min), cfg)
    buf = io.BytesIO()
    Image.fromarray(grey, mode="L").save(buf, format="PNG", optimize=True)
    try:
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(buf.getvalue())
    except OSError:
        pass                                   # a read-only store still serves
    return Response(buf.getvalue(), media_type="image/png", headers=headers)


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------

REQUIRED = ("acquired_utc", "bbox", "polarisation")


def _parse_bbox(text: Optional[str]) -> Optional[List[float]]:
    if not text:
        return None
    try:
        vals = [float(v) for v in re.split(r"[,\s]+", text.strip()) if v]
    except ValueError:
        raise HTTPException(422, "bbox must be four numbers: min_lon, min_lat, max_lon, max_lat")
    if len(vals) != 4:
        raise HTTPException(422, "bbox must be four numbers: min_lon, min_lat, max_lon, max_lat")
    w, s, e, n = vals
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(422, "bbox is not a valid WGS84 box (min_lon < max_lon, min_lat < max_lat, in range)")
    return vals


def _parse_time(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "acquired_utc must be ISO-8601, e.g. 2023-01-08T00:10:08Z")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if t > datetime.now(timezone.utc):
        raise HTTPException(422, "acquired_utc is in the future")
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inspect_raster(path: Path) -> Dict[str, Any]:
    """What the raster says about itself. Nothing here is a guess."""
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds

    facts: Dict[str, Any] = {"georeferenced_raster": False}
    with rasterio.open(path) as src:
        facts.update(width=src.width, height=src.height, bands=src.count, dtype=str(src.dtypes[0]))
        has_geo = src.crs is not None and not src.transform.is_identity
        if has_geo:
            w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
            facts.update(georeferenced_raster=True, raster_crs=str(src.crs),
                         bbox=[round(w, 6), round(s, 6), round(e, 6), round(n, 6)])
            if src.crs.is_projected:
                facts["pixel_spacing_m"] = round(abs(src.transform.a), 3)
            else:
                mid = math.radians((s + n) / 2)
                facts["pixel_spacing_m"] = round(abs(src.transform.a) * 111_320 * math.cos(mid), 2)
        scale = max(1, math.ceil(max(src.width, src.height) / 512))
        sample = src.read(1, out_shape=(max(1, src.height // scale), max(1, src.width // scale))).astype("float32")
    finite = sample[np.isfinite(sample)]
    if finite.size:
        lo, hi = float(np.percentile(finite, 1)), float(np.percentile(finite, 99))
        facts["value_p01_p99"] = [round(lo, 3), round(hi, 3)]
        # Sigma0 in dB over water sits well below zero; a raster that never
        # goes negative is linear power or an 8-bit picture, and the detector's
        # dB normalisation will not mean the same thing on it.
        facts["looks_like_db"] = lo < -3.0 and hi <= 15.0
    return facts


def _write_scene(up_dir: Path, state: dict) -> Dict[str, Any]:
    """Write the contract-clean scene_meta.json once nothing is missing."""
    missing = [k for k in REQUIRED if not state["metadata"].get(k)]
    if state.get("kind") != "raster":
        state["status"] = "unsupported"
    elif missing:
        state["status"] = "metadata_required"
    state["missing"] = missing
    if state.get("kind") == "raster" and not missing:
        md, rf = state["metadata"], state["raster_facts"]
        raster = up_dir / state["raster_name"]
        if not rf.get("georeferenced_raster"):
            raster = _georeference_copy(raster, md["bbox"])
            state["raster_name"] = raster.name
        from contracts.schemas.scene import SceneMeta

        meta = {
            "scene_id": md["scene_id"],
            "acquired_utc": md["acquired_utc"],
            "bbox": md["bbox"],
            "crs": "EPSG:4326",
            "db_range": [-35.0, 0.0],
            "file_path": _relative(raster),
            "provider_used": "UserUpload",
            # `cached`: bytes served from this host's store. The contract has
            # no "unverified" flag, and "real" would claim a provenance nobody
            # checked; upload.json carries the rest.
            "source": "cached",
            "polarisation": md["polarisation"],
        }
        if md.get("pixel_spacing_m"):
            meta["pixel_spacing_m"] = float(md["pixel_spacing_m"])
        SceneMeta.model_validate(meta)          # a contract breach is a 500, loudly
        (up_dir / "scene_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        state["status"] = "ready"
        state["scene_meta_path"] = _relative(up_dir / "scene_meta.json")
        state["key"] = _key(up_dir / "scene_meta.json")
        _RUN_INDEX["at"] = 0.0
    (up_dir / "upload.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _georeference_copy(raster: Path, bbox: List[float]) -> Path:
    """A raster with no georeference, placed at the bbox the uploader gave.
    The original is kept; the copy records where its position came from."""
    import rasterio
    from rasterio.transform import from_bounds

    out = raster.with_name(raster.stem + "_georef.tif")
    with rasterio.open(raster) as src:
        profile = src.profile.copy()
        profile.update(driver="GTiff", crs="EPSG:4326",
                       transform=from_bounds(*bbox, src.width, src.height))
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(src.read())
            dst.update_tags(OCEANTRACE_GEOREF="user-supplied bbox; the source raster carried none")
    return out


@router.post("/sar/upload", status_code=201,
             dependencies=[Depends(require_role("investigator", "analyst"))])
async def upload_scene(
    raster: UploadFile = File(...),
    scene_id: Optional[str] = Form(None),
    label: Optional[str] = Form(None),
    acquired_utc: Optional[str] = Form(None),
    bbox: Optional[str] = Form(None),
    polarisation: Optional[str] = Form(None),
    product_type: Optional[str] = Form(None),
    orbit_direction: Optional[str] = Form(None),
    pixel_spacing_m: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    user=Depends(current_user),
) -> Dict[str, Any]:
    """Accept a SAR raster and whatever metadata came with it.

    Answers ``ready`` (a scene the pipeline can run), ``metadata_required``
    (with the list of what is missing -- never filled in here), or
    ``unsupported`` (a picture: no backscatter calibration to analyse)."""
    name = Path(raster.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if suffix not in RASTER_SUFFIXES | PICTURE_SUFFIXES:
        raise HTTPException(415, f"unsupported file type '{suffix}'. Upload a GeoTIFF (.tif) of calibrated sigma0.")

    upload_id = f"up-{uuid.uuid4().hex[:10]}"
    up_dir = UPLOADS_DIR / upload_id
    up_dir.mkdir(parents=True, exist_ok=False)
    dest = up_dir / ("raster" + suffix)
    size = 0
    with dest.open("wb") as fh:
        while chunk := await raster.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                up_dir.rmdir()
                raise HTTPException(413, "file exceeds the 1.5 GB upload limit")
            fh.write(chunk)

    state: Dict[str, Any] = {
        "upload_id": upload_id, "original_name": name, "bytes": size, "raster_name": dest.name,
        "uploaded_by": getattr(user, "email", None), "label": label, "notes": notes,
        "uploaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "product_type": product_type, "orbit_direction": orbit_direction,
        "caveats": [], "raster_facts": {}, "metadata": {}, "metadata_basis": {},
    }

    if suffix in PICTURE_SUFFIXES:
        state.update(kind="picture")
        state["caveats"].append(
            "An 8-bit picture carries no backscatter calibration: the detector is trained on sigma0 in dB and "
            "its output on a rendered image would not be a measurement. Upload the GeoTIFF this picture was made from.")
        return _write_scene(up_dir, state)

    try:
        facts = _inspect_raster(dest)
    except Exception as exc:                      # rasterio raises many types
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"could not read the raster: {exc}")
    state.update(kind="raster", raster_facts=facts)
    if facts.get("looks_like_db") is False:
        state["caveats"].append(
            f"Values span {facts.get('value_p01_p99')} and never go clearly negative: this does not look like sigma0 in dB. "
            "The detector will still run, but its confidence is not comparable with calibrated scenes.")

    md, basis = state["metadata"], state["metadata_basis"]
    sid = (scene_id or "").strip() or Path(name).stem
    md["scene_id"] = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:120]
    if len(md["scene_id"]) < 3:                 # the contract's minimum; "in.tif" is a legal file name
        md["scene_id"] = f"UPLOAD_{md['scene_id']}_{upload_id[3:]}"
    # A scene id identifies one scene. The same file uploaded twice is two
    # uploads, and must not shadow (or be shadowed by) a scene already held.
    if any(r["scene_id"] == md["scene_id"] for r in all_rows()):
        md["scene_id"] = f"{md['scene_id'][:108]}_{upload_id[3:]}"
    prod = product_facts(md["scene_id"])
    # The raster's own georeference outranks anything typed in a form.
    if facts.get("bbox"):
        md["bbox"], basis["bbox"] = facts["bbox"], "raster"
    elif bbox:
        md["bbox"], basis["bbox"] = _parse_bbox(bbox), "user_supplied"
    if acquired_utc:
        md["acquired_utc"], basis["acquired_utc"] = _parse_time(acquired_utc), "user_supplied"
    elif prod.get("sensing_start_utc"):
        md["acquired_utc"], basis["acquired_utc"] = prod["sensing_start_utc"], "product_identifier"
    if polarisation:
        md["polarisation"], basis["polarisation"] = polarisation.strip().upper()[:8], "user_supplied"
    md["pixel_spacing_m"] = pixel_spacing_m or facts.get("pixel_spacing_m")
    return _write_scene(up_dir, state)


@router.post("/sar/upload/{upload_id}/metadata",
             dependencies=[Depends(require_role("investigator", "analyst"))])
def complete_upload(upload_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Supply what the first request lacked. Only fields that are still
    missing, or were user-supplied, can be set: a raster's own georeference
    is not overridable from a form."""
    if not re.fullmatch(r"up-[0-9a-f]{10}", upload_id):
        raise HTTPException(404, "no such upload")
    up_dir = UPLOADS_DIR / upload_id
    state = _read_json(up_dir / "upload.json")
    if state is None:
        raise HTTPException(404, "no such upload")
    if state.get("kind") != "raster":
        raise HTTPException(409, "this upload is a picture, not a raster; metadata cannot make it analysable")
    md, basis = state["metadata"], state["metadata_basis"]
    if body.get("bbox") is not None and basis.get("bbox") != "raster":
        value = body["bbox"]
        md["bbox"] = _parse_bbox(value if isinstance(value, str) else ",".join(str(v) for v in value))
        basis["bbox"] = "user_supplied"
    if body.get("acquired_utc"):
        md["acquired_utc"], basis["acquired_utc"] = _parse_time(str(body["acquired_utc"])), "user_supplied"
    if body.get("polarisation"):
        md["polarisation"], basis["polarisation"] = str(body["polarisation"]).strip().upper()[:8], "user_supplied"
    if body.get("pixel_spacing_m"):
        md["pixel_spacing_m"] = float(body["pixel_spacing_m"])
    for k in ("product_type", "orbit_direction", "label", "notes"):
        if body.get(k):
            state[k] = str(body[k])[:400]
    return _write_scene(up_dir, state)


@router.get("/sar/upload/{upload_id}")
def upload_state(upload_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"up-[0-9a-f]{10}", upload_id):
        raise HTTPException(404, "no such upload")
    state = _read_json(UPLOADS_DIR / upload_id / "upload.json")
    if state is None:
        raise HTTPException(404, "no such upload")
    return state
