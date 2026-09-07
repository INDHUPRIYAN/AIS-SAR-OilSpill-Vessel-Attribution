"""Does the AIS on disk actually cover the origin window this run computed?

The question P14 turns on. `vessels_cover_origin` answers it as a yes/no gate
during the run; this reports the shape of the answer, because "covered" and
"covered except for the last quarter of an hour" are different claims and only
one of them is true here.

Everything is measured from the artefacts: the window comes from the run's own
origin cloud, the coverage from the parquet it actually used. Nothing is
assumed about which archives were fetched.

    python -m backend.ais_coverage --run data/runs/inv-gulf-flagship-20230108
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _iso(value) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def analyse(run_dir: Path) -> dict:
    import pandas as pd

    cloud_path = run_dir / "origin_cloud.geojson"
    if not cloud_path.exists():
        return {"ok": False,
                "reason": "no origin cloud: drift did not run, so there is no "
                          "window to test AIS against"}
    cloud = json.loads(cloud_path.read_text(encoding="utf-8"))
    md = cloud["metadata"]
    # Normalisation lifts start/end to the top level, but `method`, the peak and
    # the search radius stay on the engine's own origin_window feature. Reading
    # only the metadata would silently drop the one field that says whether the
    # window means anything -- `method`.
    window_feature = next(
        (f["properties"] for f in cloud.get("features", [])
         if f.get("properties", {}).get("kind") == "origin_window"), {})

    start = _iso(md.get("origin_window_start_utc") or window_feature.get("start_utc"))
    end = _iso(md.get("origin_window_end_utc") or window_feature.get("end_utc"))
    if start is None or end is None:
        return {"ok": False, "reason": "the origin cloud states no window"}

    vessels = run_dir / "vessels.parquet"
    if not vessels.exists():
        return {"ok": False, "reason": "the run has no vessels.parquet"}
    df = pd.read_parquet(vessels, columns=["mmsi", "timestamp_utc", "source"])
    ts = pd.to_datetime(df["timestamp_utc"], utc=True)

    inside = df[(ts >= start) & (ts <= end)]
    have_from, have_to = ts.min(), ts.max()

    # The gap that matters: window time with no AIS on either side of it.
    missing_head = max(0.0, (min(have_from, end) - start).total_seconds()) \
        if have_from > start else 0.0
    missing_tail = max(0.0, (end - max(have_to, start)).total_seconds()) \
        if have_to < end else 0.0
    window_s = (end - start).total_seconds()

    return {
        "ok": True,
        "origin_window_utc": [start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                              end.strftime("%Y-%m-%dT%H:%M:%SZ")],
        "origin_window_hours": round(window_s / 3600.0, 3),
        "origin_window_method": md.get("origin_window_method"),
        "peak_utc": md.get("origin_peak_utc"),
        "ais_span_utc": [have_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         have_to.strftime("%Y-%m-%dT%H:%M:%SZ")],
        "rows_total": int(len(df)),
        "rows_inside_window": int(len(inside)),
        "mmsi_inside_window": int(inside["mmsi"].nunique()),
        "sources": sorted(set(df["source"].astype(str).str.lower())),
        "uncovered_minutes_at_start": round(missing_head / 60.0, 1),
        "uncovered_minutes_at_end": round(missing_tail / 60.0, 1),
        "window_fully_covered": missing_head == 0.0 and missing_tail == 0.0,
        "covered_fraction": round(
            max(0.0, window_s - missing_head - missing_tail) / window_s, 4)
        if window_s else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default=None, help="write the report here as JSON")
    args = ap.parse_args(argv)

    report = analyse(Path(args.run))
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
