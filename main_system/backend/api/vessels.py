"""Vessel dossier: everything the system knows about one MMSI.

Reading a dossier is role-gated because with real AIS this is personal and
commercial data about an identifiable operator, and because the page exists to
support a decision about whether that operator polluted.

Every payload carries `source`. A synthetic vessel exists only inside a
scenario, and a dossier that did not say so would read as a record of a real
ship. Identity fields are null when the archive did not supply them -- absent,
never blank-filled, so "unknown" and "none" stay distinguishable.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.db import Run, Vessel, VesselAppearance, get_db

router = APIRouter()
settings = get_settings()


def _vessel_dict(v: Vessel, appearances: int = 0) -> dict:
    return {
        "mmsi": v.mmsi,
        "name": v.name,
        "imo": v.imo,
        "call_sign": v.call_sign,
        "flag": v.flag,
        "vessel_type": v.vessel_type,
        "length_m": v.length_m,
        "width_m": v.width_m,
        "draught_m": v.draught_m,
        "source": v.source,
        "first_seen_utc": v.first_seen_utc,
        "last_seen_utc": v.last_seen_utc,
        "appearances": appearances,
        # Stated rather than implied by nulls: the contract file carries no
        # identity, so a synthetic vessel HAS no name to show.
        "identity_available": bool(v.name or v.imo or v.call_sign),
    }


@router.get("/vessels")
def list_vessels(db: Session = Depends(get_db),
                 q: Optional[str] = None,
                 source: Optional[str] = Query(None, pattern="^(real|synthetic)$"),
                 offset: int = Query(0, ge=0),
                 limit: int = Query(50, ge=1, le=500)):
    query = db.query(Vessel)
    if source:
        query = query.filter(Vessel.source == source)
    if q:
        like = f"%{q}%"
        conditions = [Vessel.name.ilike(like), Vessel.imo.ilike(like),
                      Vessel.call_sign.ilike(like)]
        # An MMSI is the only identifier a synthetic vessel has, so a numeric
        # query has to match it or those vessels are unsearchable.
        if q.isdigit():
            conditions.append(Vessel.mmsi == int(q))
        query = query.filter(or_(*conditions))

    total = query.count()
    rows = query.order_by(Vessel.mmsi).offset(offset).limit(limit).all()

    counts = dict(db.query(VesselAppearance.mmsi, func.count(VesselAppearance.id))
                  .filter(VesselAppearance.mmsi.in_([r.mmsi for r in rows]))
                  .group_by(VesselAppearance.mmsi).all()) if rows else {}

    return {"total": total, "offset": offset, "limit": limit,
            "items": [_vessel_dict(r, counts.get(r.mmsi, 0)) for r in rows]}


@router.get("/vessels/{mmsi}")
def get_vessel(mmsi: int, db: Session = Depends(get_db)):
    """Identity, plus every run this vessel appeared in -- ranked or excluded."""
    vessel = db.get(Vessel, mmsi)
    if vessel is None:
        raise HTTPException(404, f"no vessel {mmsi} in the index")

    rows: List[VesselAppearance] = (
        db.query(VesselAppearance)
        .filter(VesselAppearance.mmsi == mmsi)
        .order_by(VesselAppearance.seen_utc.desc()).all())

    runs = {r.id: r for r in db.query(Run).filter(
        Run.id.in_([a.run_id for a in rows])).all()} if rows else {}

    payload = _vessel_dict(vessel, len(rows))
    payload["appearance_list"] = [{
        "run_id": a.run_id,
        "incident_id": a.incident_id,
        "rank": a.rank,
        "total_score": a.total_score,
        "filtered": a.filtered,
        "filter_reason": a.filter_reason,
        "ais_gap_minutes": a.ais_gap_minutes,
        "source": a.source,
        "scene_id": getattr(runs.get(a.run_id), "scene_id", None),
        "started_utc": getattr(runs.get(a.run_id), "started_utc", None),
    } for a in rows]
    # Both halves are stated so a reader is not left inferring the exclusions
    # from a shorter-than-expected list.
    payload["ranked_in"] = sum(1 for a in rows if not a.filtered)
    payload["filtered_in"] = sum(1 for a in rows if a.filtered)
    return payload


@router.get("/vessels/{mmsi}/tracks")
def vessel_tracks(mmsi: int, db: Session = Depends(get_db)):
    """Where this vessel's track can be read from, per run.

    References rather than geometry: a track lives in its run's own
    `vessels.parquet`, and copying it here would create a second copy that
    could drift from the sealed artefact it claims to represent.
    """
    rows = (db.query(VesselAppearance)
            .filter(VesselAppearance.mmsi == mmsi)
            .order_by(VesselAppearance.seen_utc.desc()).all())
    if not rows:
        raise HTTPException(404, f"no vessel {mmsi} in the index")

    tracks = []
    for a in rows:
        run_dir = settings.runs_root / a.run_id
        tracks.append({
            "run_id": a.run_id,
            "filtered": a.filtered,
            "source": a.source,
            "vessels_geojson": f"/api/runs/{a.run_id}/vessels_geojson",
            "available": (run_dir / "vessels.parquet").exists(),
        })
    return {"mmsi": mmsi, "count": len(tracks), "tracks": tracks}
