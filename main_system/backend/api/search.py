"""One search box across runs, incidents, vessels and scenes.

    GET /api/search?q=367653160
    GET /api/search?q=gulf&kinds=incident,run

Backs the command palette. The design constraint is narrow and worth stating:
**a search result must be able to say where it came from.** Every row carries
`kind`, the id a route can use, and a `context` line built from real fields --
never a summary invented to make the list read nicely.

Two things it deliberately does not do.

**No fuzzy matching.** An MMSI is nine digits and a run id is a slug; a
Levenshtein match on either produces confident nonsense. Matching is substring,
case-insensitive, and if nothing matches the answer is an empty list rather
than the nearest thing.

**No cross-kind ranking beyond a stated rule.** Exact id matches come first,
then prefix, then substring; within a tier, kinds keep a fixed order. A
relevance score would imply a judgement the system has no basis for.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.db import Incident, Investigation, Run, Vessel, get_db

router = APIRouter()

KINDS = ("run", "incident", "investigation", "vessel", "scene")
# Fixed order within a match tier. Not a relevance judgement -- just a stable
# sort so the palette does not reshuffle between identical queries.
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}


def _tier(needle: str, *fields: Optional[str]) -> Optional[int]:
    """0 exact, 1 prefix, 2 substring, None no match."""
    best = None
    for field in fields:
        if not field:
            continue
        value = str(field).lower()
        if value == needle:
            return 0
        if value.startswith(needle):
            best = 1 if best is None else min(best, 1)
        elif needle in value:
            best = 2 if best is None else min(best, 2)
    return best


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=120),
           kinds: Optional[str] = Query(
               None, description="comma-separated subset of "
                                 "run,incident,investigation,vessel,scene"),
           limit: int = Query(20, ge=1, le=100),
           db: Session = Depends(get_db)):
    needle = q.strip().lower()
    wanted = set(KINDS)
    if kinds:
        wanted = {k.strip() for k in kinds.split(",") if k.strip() in KINDS}

    hits: List[Dict[str, Any]] = []

    if "run" in wanted:
        rows = (db.query(Run)
                .filter(or_(Run.id.ilike(f"%{needle}%"),
                            Run.scene_id.ilike(f"%{needle}%")))
                .order_by(Run.started_utc.desc()).limit(limit * 2).all())
        for row in rows:
            tier = _tier(needle, row.id, row.scene_id)
            if tier is None:
                continue
            hits.append({
                "kind": "run", "id": row.id, "label": row.id,
                "context": (f"{row.status}"
                            + (f" · {row.stages_real}/{row.stages_total} real"
                               if row.stages_total else "")
                            + (f" · {row.scene_id[:34]}" if row.scene_id else "")),
                "route": f"/investigation?run={row.id}", "tier": tier,
            })

    if "incident" in wanted:
        rows = (db.query(Incident)
                .filter(or_(Incident.id.ilike(f"%{needle}%"),
                            Incident.title.ilike(f"%{needle}%")))
                .limit(limit * 2).all())
        for row in rows:
            tier = _tier(needle, row.id, row.title)
            if tier is None:
                continue
            hits.append({"kind": "incident", "id": row.id, "label": row.title,
                         "context": f"{row.id} · {row.status}",
                         "route": f"/incidents?incident={row.id}", "tier": tier})

    if "investigation" in wanted:
        rows = (db.query(Investigation)
                .filter(or_(Investigation.id.ilike(f"%{needle}%"),
                            Investigation.name.ilike(f"%{needle}%")))
                .limit(limit * 2).all())
        for row in rows:
            tier = _tier(needle, row.id, row.name)
            if tier is None:
                continue
            hits.append({"kind": "investigation", "id": row.id,
                         "label": row.name,
                         "context": f"{row.id}"
                                    + (f" · {row.scene_id[:34]}" if row.scene_id else ""),
                         "route": f"/investigation?inv={row.id}", "tier": tier})

    if "vessel" in wanted:
        # mmsi is an Integer column: cast before matching, or the LIKE is
        # dialect-dependent and silently returns nothing on some backends.
        rows = (db.query(Vessel)
                .filter(or_(cast(Vessel.mmsi, String).ilike(f"%{needle}%"),
                            Vessel.name.ilike(f"%{needle}%")))
                .limit(limit * 2).all())
        for row in rows:
            tier = _tier(needle, str(row.mmsi), row.name)
            if tier is None:
                continue
            hits.append({
                "kind": "vessel", "id": str(row.mmsi),
                # A vessel with no name shows its MMSI, not a placeholder.
                # MarineCadastre often carries no static identity, and
                # inventing "Unknown Vessel" would read like a record.
                "label": row.name or str(row.mmsi),
                "context": (f"MMSI {row.mmsi}"
                            + (f" · {row.vessel_type}" if row.vessel_type else "")
                            + ("" if row.name else " · no name in the source data")),
                "route": f"/vessels?mmsi={row.mmsi}", "tier": tier})

    if "scene" in wanted:
        for entry in _scenes():
            tier = _tier(needle, entry["id"], entry.get("label"))
            if tier is None:
                continue
            hits.append({"kind": "scene", "id": entry["id"],
                         "label": entry.get("label") or entry["id"],
                         "context": entry.get("context", ""),
                         "route": f"/investigation?scene={entry['id']}",
                         "tier": tier})

    hits.sort(key=lambda h: (h["tier"], KIND_ORDER.get(h["kind"], 9),
                             str(h["label"]).lower()))
    return {"query": q, "count": len(hits[:limit]), "results": hits[:limit],
            "matching": "substring, case-insensitive. Exact ids rank first, "
                        "then prefix, then substring. No fuzzy matching: a "
                        "near-miss on a nine-digit MMSI is not a result."}


def _scenes() -> List[Dict[str, Any]]:
    """Scenes held locally, from the curated catalogue and the scene cache."""
    settings = get_settings()
    root = Path(settings.data_root)
    out: List[Dict[str, Any]] = []
    seen = set()

    catalogue = root.parent / "main_system" / "config" / "scene_catalog.json"
    if catalogue.exists():
        try:
            payload = json.loads(catalogue.read_text(encoding="utf-8"))
            entries = payload if isinstance(payload, list) else payload.get("scenes", [])
            for entry in entries:
                if entry.get("id") and entry["id"] not in seen:
                    seen.add(entry["id"])
                    out.append({"id": entry["id"], "label": entry.get("label"),
                                "context": (f"{entry.get('provenance', 'unknown')}"
                                            f" · {entry.get('time_basis', '')}").strip(" ·")})
        except (json.JSONDecodeError, OSError):
            pass

    scenes_dir = root / "scenes"
    if scenes_dir.is_dir():
        for meta_file in sorted(scenes_dir.glob("*/scene_meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            scene_id = meta.get("scene_id")
            if scene_id and scene_id not in seen:
                seen.add(scene_id)
                out.append({"id": scene_id, "label": meta_file.parent.name,
                            "context": f"{meta.get('source', 'unknown')} · "
                                       f"{meta.get('acquired_utc', '')}".strip(" ·")})
    return out
