"""Build the cross-run vessel index from a run's sealed artefacts.

The question this answers is "what else do we know about this MMSI", which no
single run can. It runs after a run is sealed and reads only that run's own
files, so re-indexing is deterministic and cannot invent history.

Two rules shape everything here:

* **Identity is never fabricated.** `vessels.parquet` is a frozen 14-column
  contract with no name, IMO or call sign in it, and `validate_vessels_df`
  rejects extras -- so identity comes from a side channel (the MarineCadastre
  ingest, before the contract projection drops it) or it stays null. A vessel
  the system may rank as a suspect must never carry a name somebody guessed.

* **Exclusions are recorded, not hidden.** A vessel the gates filtered out gets
  an appearance row with the gate that excluded it. A dossier showing only the
  runs where a vessel scored well would be a prosecution file, not a record.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from backend.models.db import Run, Vessel, VesselAppearance, utcnow

IDENTITY_FIELDS = ("name", "imo", "call_sign", "flag")


def _naive_utc(value):
    """Comparable timestamp, whichever side it came from.

    Parquet hands back tz-aware timestamps; SQLite has no timestamp type and
    returns naive ones. Comparing the two raises, so both are normalised to
    naive UTC before any min/max. (The same mismatch broke the audit hash
    chain in PROMPT-08 -- it is worth recognising on sight.)
    """
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        from datetime import timezone

        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _identity_sidecar(run_dir: Path) -> Dict[int, dict]:
    """Identity captured beside the contract file, if this run has any.

    Written by the AIS ingest for real archives. Absent for synthetic runs,
    which is the normal case and not an error.
    """
    path = run_dir / "vessel_identities.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[int, dict] = {}
    for key, value in (raw or {}).items():
        try:
            out[int(key)] = value or {}
        except (TypeError, ValueError):
            continue
    return out


def _vessel_rows(run_dir: Path) -> Dict[int, dict]:
    """Per-MMSI shape and timing from the run's own vessels.parquet."""
    path = run_dir / "vessels.parquet"
    if not path.exists():
        return {}
    try:
        import pandas as pd

        df = pd.read_parquet(path)
    except Exception:                              # noqa: BLE001 - optional enrichment
        return {}
    if df.empty or "mmsi" not in df.columns:
        return {}

    out: Dict[int, dict] = {}
    for mmsi, group in df.groupby("mmsi"):
        row: Dict[str, Any] = {}
        for field in ("vessel_type", "length_m", "width_m", "draught_m", "source"):
            if field in group.columns:
                values = group[field].dropna()
                if not values.empty:
                    row[field] = values.iloc[0]
        if "timestamp_utc" in group.columns:
            stamps = group["timestamp_utc"].dropna()
            if not stamps.empty:
                row["first_seen_utc"] = stamps.min().to_pydatetime()
                row["last_seen_utc"] = stamps.max().to_pydatetime()
        out[int(mmsi)] = row
    return out


def _suspects(run_dir: Path) -> Optional[dict]:
    path = run_dir / "suspects.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def index_run(db: Session, run_id: str, run_dir: Path) -> dict:
    """Record every vessel this run considered. Idempotent per run."""
    payload = _suspects(run_dir)
    if payload is None:
        return {"run_id": run_id, "indexed": 0, "reason": "no suspects.json"}

    identities = _identity_sidecar(run_dir)
    shapes = _vessel_rows(run_dir)
    run = db.get(Run, run_id)
    incident_id = run.incident_id if run is not None else None

    # Re-indexing replaces this run's rows rather than appending, so running
    # the backfill twice cannot double a vessel's appearance count.
    db.query(VesselAppearance).filter(VesselAppearance.run_id == run_id).delete()

    entries: list[tuple[dict, bool]] = [(s, False) for s in (payload.get("suspects") or [])]
    entries += [(f, True) for f in (payload.get("filtered_out") or [])]

    seen = 0
    for entry, filtered in entries:
        try:
            mmsi = int(entry["mmsi"])
        except (KeyError, TypeError, ValueError):
            continue

        shape = shapes.get(mmsi, {})
        source = str(entry.get("source") or shape.get("source")
                     or payload.get("source") or "synthetic").lower()
        source = "real" if source == "real" else "synthetic"

        vessel = db.get(Vessel, mmsi)
        if vessel is None:
            vessel = Vessel(mmsi=mmsi, source=source)
            db.add(vessel)

        # Only ever fill a blank. A later synthetic scenario reusing this MMSI
        # must not overwrite what a real archive said about it.
        identity = identities.get(mmsi, {})
        for field in IDENTITY_FIELDS:
            key = "vessel_name" if field == "name" else field
            value = identity.get(key) or identity.get(field)
            if value and not getattr(vessel, field):
                setattr(vessel, field, str(value)[:120])

        for field in ("vessel_type", "length_m", "width_m", "draught_m"):
            value = shape.get(field)
            if value is not None and getattr(vessel, field) in (None, ""):
                setattr(vessel, field, value)

        for field in ("first_seen_utc", "last_seen_utc"):
            value = _naive_utc(shape.get(field))
            if value is None:
                continue
            current = _naive_utc(getattr(vessel, field))
            if current is None:
                setattr(vessel, field, value)
            elif field == "first_seen_utc" and value < current:
                vessel.first_seen_utc = value
            elif field == "last_seen_utc" and value > current:
                vessel.last_seen_utc = value

        # A vessel seen in a real archive stays real even if a scenario later
        # borrows its MMSI; downgrading it would mislabel actual evidence.
        if source == "real":
            vessel.source = "real"

        evidence = entry.get("evidence") or {}
        db.add(VesselAppearance(
            mmsi=mmsi, run_id=run_id, incident_id=incident_id,
            rank=entry.get("rank"),
            total_score=entry.get("total_score"),
            filtered=filtered,
            filter_reason=(entry.get("filter_reason") or None) if filtered else None,
            ais_gap_minutes=evidence.get("ais_gap_minutes"),
            source=source, seen_utc=utcnow(),
        ))
        seen += 1

    db.commit()
    return {"run_id": run_id, "indexed": seen,
            "ranked": sum(1 for _, f in entries if not f),
            "filtered": sum(1 for _, f in entries if f)}


def backfill(db: Session, runs_root: Path,
             run_ids: Optional[Iterable[str]] = None) -> dict:
    """Index every sealed run, or the ones named."""
    directories = ([runs_root / r for r in run_ids] if run_ids
                   else sorted(p.parent for p in runs_root.glob("*/suspects.json")))
    total, runs = 0, 0
    for directory in directories:
        if not (directory / "suspects.json").exists():
            continue
        result = index_run(db, directory.name, directory)
        total += result.get("indexed", 0)
        runs += 1
    return {"runs": runs, "appearances": total}
