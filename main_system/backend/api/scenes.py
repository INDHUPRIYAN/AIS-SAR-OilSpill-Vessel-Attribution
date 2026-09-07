"""Scene selection for launching investigations.

Audit N-14: the Dashboard hardcoded `contracts/mocks/scene_meta.json`, so every
run a user started from the browser was a smoke test -- 1 of 5 stages real, the
other four served from mocks. The scene was never a choice, so nobody could see
it was the wrong one.

This module makes the choice explicit and, more importantly, makes it honest.
Each catalog entry declares:

  provenance  where the pixels came from, and whether the deployed segmenter
              was trained on them -- confidence on a training scene is
              memorisation, not accuracy, and must never be quoted as the
              latter;
  time_basis  whether `acquired_utc` was measured (read from a Sentinel-1
              product identifier) or assigned for the demo. The hindcast
              integrates backward from that timestamp, so an assigned time
              anchors the entire temporal chain -- including the window the
              AIS query uses.

Entries whose metadata or raster is missing are returned with
`available: false` and a reason rather than filtered out silently: a scene
that has gone missing is something the operator needs to see.

`GET /api/scenes/search` (PROMPT-12) wraps the provider chain below. It
reports which chain member answered rather than merging providers, because
"CDSE answered" and "both failed" are different situations.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.core.config import get_settings

router = APIRouter()
settings = get_settings()
REPO_ROOT = settings.data_root.parent
CATALOG_PATH = REPO_ROOT / "main_system" / "config" / "scene_catalog.json"

# How each provenance class is labelled in the UI. The wording is the contract:
# these strings are rendered verbatim, so they say what the data is, not what
# we would like it to be.
PROVENANCE_LABELS: Dict[str, str] = {
    "sentinel1_real": "REAL (Sentinel-1 acquisition)",
    "corpus_heldout": "REAL (SAR corpus · held out)",
    "corpus_train": "REAL (SAR corpus · TRAINING IMAGE)",
    "mock": "MOCK (pipeline smoke test)",
}

# Caveats attached automatically, so a new catalog entry cannot be added
# without the warning that its class implies.
PROVENANCE_CAVEATS: Dict[str, List[str]] = {
    "sentinel1_real": [],
    "corpus_heldout": [
        "Held out from segmenter training; detection here is a fair measurement.",
    ],
    "corpus_train": [
        "The deployed segmenter TRAINED on this scene. Detection confidence "
        "reflects memorisation and is not evidence of accuracy.",
    ],
    "mock": [
        "Synthetic raster containing no oil. Produces a 1-of-5-real run by "
        "design; use only to exercise pipeline wiring.",
    ],
}

TIME_BASIS_CAVEAT = (
    "acquired_utc was ASSIGNED for the demo, not measured -- this corpus ships "
    "no acquisition metadata. The hindcast integrates backward from it, so "
    "every derived time (origin window, AIS query window) inherits that basis."
)


def _resolve(path_value: str) -> Path:
    """Repo-anchored resolution, matching how investigations resolve scenes."""
    p = Path(path_value)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return path.name


def _load_catalog() -> List[Dict[str, Any]]:
    if not CATALOG_PATH.exists():
        return []
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8")).get("scenes", [])
    except (json.JSONDecodeError, OSError):
        return []


def _describe(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One catalog row, with its metadata resolved and its caveats attached."""
    provenance = entry.get("provenance", "mock")
    caveats = list(PROVENANCE_CAVEATS.get(provenance, []))
    if entry.get("time_basis") == "assigned":
        caveats.append(TIME_BASIS_CAVEAT)

    row: Dict[str, Any] = {
        "id": entry.get("id"),
        "label": entry.get("label"),
        "scene_meta_path": entry.get("scene_meta_path"),
        "provenance": provenance,
        "source": PROVENANCE_LABELS.get(provenance, provenance),
        "time_basis": entry.get("time_basis"),
        "is_default": bool(entry.get("default")),
        "notes": entry.get("notes"),
        "caveats": caveats,
        "available": False,
        "unavailable_reason": None,
        "scene_id": None,
        "acquired_utc": None,
        "bbox": None,
        "pixel_spacing_m": None,
        "polarisation": None,
        "raster_path": None,
    }

    meta_path = _resolve(str(entry.get("scene_meta_path", "")))
    if not meta_path.exists():
        row["unavailable_reason"] = f"scene_meta not found: {entry.get('scene_meta_path')}"
        return row
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        row["unavailable_reason"] = f"scene_meta unreadable: {exc}"
        return row

    row.update({
        "scene_id": meta.get("scene_id"),
        "acquired_utc": meta.get("acquired_utc"),
        "bbox": meta.get("bbox"),
        "pixel_spacing_m": meta.get("pixel_spacing_m"),
        "polarisation": meta.get("polarisation"),
        "provider_used": meta.get("provider_used"),
    })

    raster = meta.get("file_path")
    if not raster:
        row["unavailable_reason"] = "scene_meta has no file_path"
        return row
    raster_path = _resolve(raster)
    if not raster_path.exists():
        row["unavailable_reason"] = f"raster missing: {_relative(raster_path)}"
        return row

    row["raster_path"] = _relative(raster_path)
    row["available"] = True
    return row


@router.get("/scenes/local")
def local_scenes() -> Dict[str, Any]:
    """Demo scenes that can be run with no network at all.

    The default is a real Sentinel-1 acquisition. The mock raster stays in the
    list -- it is genuinely useful for checking wiring -- but it is labelled
    for what it is and is never the default.
    """
    rows = [_describe(e) for e in _load_catalog()]
    available = [r for r in rows if r["available"]]

    # The declared default only counts if it actually resolved; otherwise fall
    # back to the first available REAL scene rather than silently offering the
    # mock, which is the failure mode this endpoint exists to remove.
    default_id: Optional[str] = None
    for row in available:
        if row["is_default"]:
            default_id = row["id"]
            break
    if default_id is None:
        for row in available:
            if row["provenance"] != "mock":
                default_id = row["id"]
                break
    for row in rows:
        row["is_default"] = row["id"] == default_id

    return {
        "scenes": rows,
        "count": len(rows),
        "available": len(available),
        "default_id": default_id,
    }


@router.get("/scenes/local/{scene_id}")
def local_scene(scene_id: str) -> Dict[str, Any]:
    for entry in _load_catalog():
        if entry.get("id") == scene_id:
            return _describe(entry)
    raise HTTPException(404, f"no catalog scene with id {scene_id!r}")


# --------------------------------------------------------------------------
# search  (PROMPT-12)
#
# Wraps `SceneRetrievalChain`, which is already proven from the CLI. Nothing
# about provider selection is reimplemented here: the chain owns CDSE -> ASF
# -> LocalCache, and this route reports which member actually answered.
# --------------------------------------------------------------------------

# Sentinel-2 has a real, tested adapter (`scene_service/satellite/s2_adapter.py`,
# 11 tests, one 809 MB acquisition on record) that is wired into nothing. There
# is no labelled optical training data, so no honest accuracy claim can be made
# and the pipeline does not consume it. Returning an empty S2 result would read
# as "we looked and found none"; returning 501 says what is true.
# The chain's own order, mirrored so a search can say which members were
# passed over. Kept beside the route rather than imported: the chain builds
# its members in __init__ and does not publish the sequence.
CHAIN_ORDER = ("CDSE", "ASF", "LocalCache")

NOT_DEPLOYED_SOURCES = {
    "S2": {
        "status": "NOT_DEPLOYED",
        "detail": "Sentinel-2 / EO is not deployed. The adapter exists and is "
                  "tested, but no optical data is wired into the pipeline and "
                  "no accuracy has been measured for it, so this endpoint will "
                  "not return EO results.",
        "adapter": "scene_service/satellite/s2_adapter.py",
    },
}


def _bbox(raw: str) -> List[float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise HTTPException(422, "bbox must be 'min_lon,min_lat,max_lon,max_lat'")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        raise HTTPException(422, "bbox values must be numbers")
    if not (-180 <= values[0] <= 180 and -180 <= values[2] <= 180):
        raise HTTPException(422, "longitude must be between -180 and 180")
    if not (-90 <= values[1] <= 90 and -90 <= values[3] <= 90):
        raise HTTPException(422, "latitude must be between -90 and 90")
    if values[0] >= values[2] or values[1] >= values[3]:
        raise HTTPException(422, "bbox must be min_lon,min_lat,max_lon,max_lat "
                                 "with min < max on both axes")
    return values


def _product(scene: Any) -> Dict[str, Any]:
    """One search hit, normalised for the wire."""
    bbox = getattr(scene, "bbox", None)
    if hasattr(bbox, "min_lon"):
        bbox = [bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat]
    acquired = getattr(scene, "acquisition_time", None)
    return {
        "product_id": getattr(scene, "scene_id", None),
        "platform": getattr(scene, "platform", None),
        "acquired_utc": acquired,
        "bbox": bbox,
        "product_type": getattr(scene, "product_type", None),
        "polarisation": getattr(scene, "polarisation", None),
        "orbit_direction": getattr(scene, "orbit_direction", None),
        "size_bytes": getattr(scene, "file_size_bytes", None),
        "download_url": getattr(scene, "download_url", None),
        # Present only for a product already on disk. A search hit is a
        # catalogue entry, not a file, and conflating the two is how a UI ends
        # up offering "run this" for something nobody has downloaded.
        "cached_path": getattr(scene, "file_path", None),
    }


@router.get("/scenes/search")
def search_scenes(bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
                  start: Optional[datetime] = None,
                  end: Optional[datetime] = None,
                  source: str = Query("S1", pattern="^(S1|S2)$"),
                  product_type: str = Query("GRD", pattern="^(GRD|SLC|OCN)$"),
                  top: int = Query(10, ge=1, le=50)):
    """Search the provider chain for candidate scenes.

    Per-provider attempts are reported rather than merged, so a caller can see
    that CDSE answered and ASF was never needed -- or that both failed, which
    is a different situation from "no scenes exist over this box".
    """
    if source in NOT_DEPLOYED_SOURCES:
        return JSONResponse(status_code=501, content=NOT_DEPLOYED_SOURCES[source])

    box = _bbox(bbox)
    attempts: List[Dict[str, Any]] = []
    try:
        from satellite.chain import SceneRetrievalChain
    except ImportError as exc:                     # pragma: no cover - path issue
        raise HTTPException(503, f"scene service unavailable: {exc}")

    started = time.perf_counter()
    try:
        result = SceneRetrievalChain().search_scenes(
            bbox=box, start_time=start, end_time=end,
            product_type=product_type, top=top)
    except Exception as exc:                       # noqa: BLE001
        # The provider's own words, not a generic 502: an expired credential
        # and an unreachable host need different fixes.
        attempts.append({"provider": "chain", "ok": False,
                         "error_class": type(exc).__name__, "detail": str(exc)[:300]})
        return {"query": {"bbox": box, "start": start, "end": end,
                          "source": source, "product_type": product_type},
                "provider": None, "total": 0, "scenes": [],
                "attempts": attempts,
                "elapsed_s": round(time.perf_counter() - started, 3)}

    provider = getattr(result, "provider", None)
    scenes = list(getattr(result, "scenes", []) or [])

    # The chain tries CDSE, then ASF, then the local cache, and returns only
    # the member that answered -- earlier failures are logged inside it and
    # never surface. Rather than report a single provider as though nothing
    # else was tried, the members ahead of the winner are listed as
    # `not_reached`, marked `inferred` because that is a deduction from the
    # chain's order, not something this route observed. Claiming to have seen
    # a CDSE error we never received would be worse than saying so.
    for member in CHAIN_ORDER:
        if member == provider:
            break
        attempts.append({"provider": member, "ok": False,
                         "result": "not_reached", "inferred": True,
                         "note": "the chain moved past this member; its own "
                                 "error is in the service log, not here"})
    attempts.append({"provider": provider or "none", "ok": bool(scenes),
                     "returned": len(scenes), "inferred": False})

    return {
        "query": {"bbox": box, "start": start, "end": end,
                  "source": source, "product_type": product_type},
        "provider": provider,
        "total": int(getattr(result, "total_count", len(scenes)) or 0),
        "scenes": [_product(s) for s in scenes],
        "attempts": attempts,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }
