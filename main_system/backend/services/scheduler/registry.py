"""The AOI registry, backed by the database instead of only a YAML file.

`aoi.py` still owns parsing and validating `config/aois.yaml`, and that file is
still the way an AOI is defined in a fixed deployment. What it cannot do is let
an operator draw a box on a map: the server reads the YAML at import time, a UI
cannot edit a file on the server's disk, and two people editing the same file
have no way to merge.

So the table is the source of truth and the YAML is migrated into it once, on
first use. Consequences worth stating plainly:

* an existing deployment keeps every AOI it had, with `source: "yaml"` recorded
  so it is obvious where a row came from;
* the migration is idempotent and never overwrites a row that already exists --
  editing an AOI through the API and then restarting must not silently revert
  it to whatever the YAML said;
* deleting an AOI removes the definition but NOT its `AoiWatch` state. That is
  deliberate: the watch high-water mark is what stops a re-registered id from
  re-opening investigations for scenes already handled, and discarding it would
  turn a delete-then-recreate into a duplicate-investigation storm.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.models.db import Aoi

from .aoi import AOI, AOIConfigError, load_aois


def _to_aoi(row: Aoi) -> AOI:
    bbox = json.loads(row.bbox_json)
    return AOI(
        id=row.id,
        name=row.name,
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        ais_region=row.ais_region,
        poll_minutes=int(row.poll_minutes),
        lookback_hours=int(row.lookback_hours),
        auto_run=bool(row.auto_run),
        enabled=bool(row.enabled),
        notes=row.notes or "",
    )


def row_dict(row: Aoi) -> Dict[str, Any]:
    """The API shape: the AOI plus the drawn geometry and its provenance."""
    payload = _to_aoi(row).to_dict()
    payload["geometry"] = json.loads(row.geometry_json) if row.geometry_json else None
    payload["source"] = row.source
    payload["created_utc"] = row.created_utc
    payload["updated_utc"] = row.updated_utc
    return payload


def migrate_yaml(db, path=None) -> Dict[str, Any]:
    """Copy YAML-defined AOIs into the table. Idempotent; never overwrites.

    A missing or malformed YAML is not fatal here. Once the table is populated
    the file is history, and refusing to serve a working registry because an
    old config file has a typo in it would be the wrong failure.
    """
    try:
        defined = load_aois(path)
    except AOIConfigError as exc:
        return {"migrated": 0, "skipped": 0,
                "note": f"no YAML migration: {exc}"}

    migrated, skipped = 0, 0
    for aoi in defined:
        if db.get(Aoi, aoi.id) is not None:
            skipped += 1
            continue
        db.add(Aoi(
            id=aoi.id, name=aoi.name,
            bbox_json=json.dumps(list(aoi.bbox)),
            # No geometry: the YAML only ever had a bbox. Writing a rectangle
            # here would claim the operator drew one, which they did not.
            geometry_json=None,
            ais_region=aoi.ais_region, poll_minutes=aoi.poll_minutes,
            lookback_hours=aoi.lookback_hours, auto_run=aoi.auto_run,
            enabled=aoi.enabled, notes=aoi.notes, source="yaml"))
        migrated += 1
    if migrated:
        db.commit()
    return {"migrated": migrated, "skipped": skipped}


def ensure_migrated(db) -> None:
    """Populate the table from YAML the first time it is empty."""
    if db.query(Aoi).first() is None:
        migrate_yaml(db)


def list_aois(db, enabled_only: bool = False) -> List[AOI]:
    ensure_migrated(db)
    query = db.query(Aoi)
    if enabled_only:
        query = query.filter(Aoi.enabled.is_(True))
    return [_to_aoi(r) for r in query.order_by(Aoi.id).all()]


def list_rows(db, enabled_only: bool = False) -> List[Aoi]:
    ensure_migrated(db)
    query = db.query(Aoi)
    if enabled_only:
        query = query.filter(Aoi.enabled.is_(True))
    return query.order_by(Aoi.id).all()


def get_row(db, aoi_id: str) -> Optional[Aoi]:
    ensure_migrated(db)
    return db.get(Aoi, aoi_id)


def get_aoi(db, aoi_id: str) -> Optional[AOI]:
    row = get_row(db, aoi_id)
    return _to_aoi(row) if row is not None else None
