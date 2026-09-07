"""Register on-disk runs that never got a database row.

`python -m backend.services.pipeline.run` writes a complete run directory --
manifest, contract artefacts, hashes -- but only the API-driven path inserts
the `runs` row that `/api/runs` lists from. A CLI run (the Chennai demo, every
world-incident replay) therefore exists on disk and is invisible in the UI,
which reads like lost work rather than a missing insert.

This scans the runs root and registers anything missing. Existing rows are
left alone by default: the API records a run's real start time, while a
manifest is written at the END, so refreshing an API-created row would move
its start time to its finish time. `--refresh` opts into that rewrite for rows
whose summary has genuinely drifted.

    python -m backend.backfill_runs            # register missing runs only
    python -m backend.backfill_runs --dry-run
    python -m backend.backfill_runs --refresh  # also rewrite existing rows
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backend.core.config import get_settings          # noqa: E402
from backend.models.db import Run, SessionLocal, init_db  # noqa: E402


def _utc(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def summarise(manifest: dict) -> dict:
    stages = manifest.get("stages", [])
    detect = next((s for s in stages if s["stage"] == "detect"), {})
    started = _utc(manifest.get("generated_utc"))
    seconds = float(manifest.get("total_seconds") or 0.0)
    return {
        "scene_id": manifest.get("scene_id"),
        # A manifest exists only once the run finished; status reflects whether
        # every stage produced something usable.
        "status": "failed" if any(s["status"] == "failed" for s in stages) else "complete",
        "started_utc": started,
        "finished_utc": started,
        "seconds": seconds,
        "stages_total": len(stages),
        "stages_real": sum(s["status"] in ("ok", "fallback") for s in stages),
        "stages_mock": sum(s["status"] == "mock" for s in stages),
        "stages_failed": sum(s["status"] == "failed" for s in stages),
        "detect_engine": detect.get("engine_used"),
    }


def _denormalise(row: Run, run_dir: Path) -> None:
    """Fill the run's headline outcome from its own sealed artefacts.

    Shares its intent with `routes.summarise_outcome`, which does the same at
    seal time for new runs; this is the catch-up pass for the 90-odd runs that
    predate the columns. Silent on failure -- a summary that cannot be read
    stays null rather than being guessed, because a wrong top suspect on a
    listing is worse than a blank one.
    """
    try:
        suspects_path = run_dir / "suspects.json"
        if suspects_path.exists():
            payload = json.loads(suspects_path.read_text(encoding="utf-8"))
            top = (payload.get("suspects") or [None])[0]
            if top:
                row.top_suspect_mmsi = int(top["mmsi"])
                row.top_score = float(top.get("total_score") or 0.0)
    except Exception:                              # noqa: BLE001
        pass
    try:
        slick_path = run_dir / "slick.geojson"
        if slick_path.exists():
            slick = json.loads(slick_path.read_text(encoding="utf-8"))
            areas = [f["properties"].get("area_km2") or 0.0
                     for f in slick.get("features", [])]
            if areas:
                row.slick_area_km2 = round(float(sum(areas)), 4)
    except Exception:                              # noqa: BLE001
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="also rewrite existing rows from their manifest")
    args = ap.parse_args(argv)

    init_db()
    runs_root = get_settings().runs_root
    db = SessionLocal()
    added = updated = skipped = summarised = 0
    try:
        for manifest_path in sorted(runs_root.glob("*/manifest.json")):
            run_id = manifest_path.parent.name
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  SKIP {run_id}: unreadable manifest ({exc})")
                skipped += 1
                continue
            fields = summarise(manifest)
            row = db.get(Run, run_id)
            if row is None:
                row = Run(id=run_id, manifest_path=str(manifest_path), **fields)
                db.add(row)
                _denormalise(row, manifest_path.parent)
                added += 1
                print(f"  + {run_id:28} {fields['scene_id']} "
                      f"({fields['stages_real']}/{fields['stages_total']} real)")
            elif args.refresh and any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                row.manifest_path = str(manifest_path)
                _denormalise(row, manifest_path.parent)
                updated += 1
                print(f"  ~ {run_id:28} refreshed from manifest")
            elif row.top_suspect_mmsi is None and row.slick_area_km2 is None:
                # Rows that predate the denormalised columns: fill the summary
                # without touching anything the manifest would rewrite, so a
                # backfill for display cannot move an API run's start time.
                _denormalise(row, manifest_path.parent)
                summarised += 1
                print(f"  s {run_id:28} outcome summarised")
            else:
                skipped += 1
        if args.dry_run:
            db.rollback()
            print(f"\n--dry-run: would add {added}, refresh {updated}, "
                  f"summarise {summarised} ({skipped} already current)")
        else:
            db.commit()
            print(f"\nregistered {added}, refreshed {updated}, "
                  f"summarised {summarised} ({skipped} already current)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
