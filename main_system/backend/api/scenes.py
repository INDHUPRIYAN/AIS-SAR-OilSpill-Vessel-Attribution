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

`GET /api/scenes/search` and scene acquisition arrive in PROMPT-12; this module
is where they will live.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

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
