"""Route attribution's vessel set through the partitioned AIS store.

`ais_service/ais/index.py` is a real spatial index -- region/time-bucket
parquet partitions with a bbox/timespan manifest for pruning, then a shapely
STRtree fine pass over the survivors. Until now attribution ignored it and
full-scanned a flat vessels.parquet. This module is the missing wire:

    vessels.parquet --ingest--> AISStore(region=run_id) --query_cloud--> pruned set

The prune is deliberately generous (25 km buffer, +/-3 h) so Engine C still
sees the near-miss vessels it explains away with measured reasons; it removes
the traffic that could never have mattered, not the traffic worth ruling out.

Every run gets its own region so synthetic vessels from one scenario can never
surface as phantom candidates in another. Any failure falls back to the
un-pruned file and says so -- the index is an accelerator, never a gate.
"""
from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT / "ais_service") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "ais_service"))

STORE_ROOT = REPO_ROOT / "data" / "ais" / "store"
BUFFER_KM = 25.0
WINDOW_PAD = timedelta(hours=3)


def prune_via_store(vessels_native: Path, origin_native: Path, run_id: str,
                    summary: Optional[dict], out_dir: Path) -> Tuple[Path, dict]:
    """Return (vessels_path_for_engine_c, notes). Never raises."""
    notes: dict = {"indexed": False}
    if not summary or not summary.get("window_start_utc"):
        notes["reason"] = "no origin window"
        return vessels_native, notes
    try:
        import pandas as pd
        from ais.index import AISStore

        df = pd.read_parquet(vessels_native)
        n_rows, n_vessels = len(df), int(df["mmsi"].nunique()) if "mmsi" in df else 0

        # Baseline: the full scan attribution used to do (read + naive filter).
        t0 = time.perf_counter()
        full = pd.read_parquet(vessels_native)
        _ = full[(full["lat"].notna()) & (full["lon"].notna())]
        t_fullscan = time.perf_counter() - t0

        start = pd.Timestamp(summary["window_start_utc"]).tz_convert("UTC") \
            if pd.Timestamp(summary["window_start_utc"]).tzinfo else \
            pd.Timestamp(summary["window_start_utc"]).tz_localize("UTC")
        end = pd.Timestamp(summary["window_end_utc"]).tz_convert("UTC") \
            if pd.Timestamp(summary["window_end_utc"]).tzinfo else \
            pd.Timestamp(summary["window_end_utc"]).tz_localize("UTC")
        start, end = start - WINDOW_PAD, end + WINDOW_PAD

        store = AISStore(STORE_ROOT)
        t0 = time.perf_counter()
        report = store.ingest(df, region=run_id)
        t_ingest = time.perf_counter() - t0

        # Query with the cloud's convex hull, not the union of every particle
        # and ellipse: buffering 7,500 geometries took ~2 s on the Chennai run;
        # the hull is a handful of vertices and, with a 25 km buffer, the same
        # decision surface.
        cloud_geom = _cloud_hull(origin_native) or origin_native
        t0 = time.perf_counter()
        pruned = store.query_cloud(cloud_geom, start, end,
                                   buffer_km=BUFFER_KM, region=run_id,
                                   whole_track=True)
        t_query = time.perf_counter() - t0

        if pruned.empty:
            notes.update(reason="index returned no vessels; using full set",
                         t_fullscan_ms=round(t_fullscan * 1e3, 1),
                         t_query_ms=round(t_query * 1e3, 1))
            return vessels_native, notes

        # Preserve the engine's expected column twins (timestamp/timestamp_utc...).
        for src, dst in (("timestamp_utc", "timestamp"), ("draught_m", "draft_m"),
                         ("interpolated", "gap_flag")):
            if src in pruned.columns and dst not in pruned.columns:
                pruned[dst] = pruned[src]
        out = vessels_native.parent / "vessels_indexed.parquet"
        pruned.to_parquet(out, index=False)

        notes.update(
            indexed=True, store=str(STORE_ROOT), region=run_id,
            rows_in=n_rows, vessels_in=n_vessels,
            rows_out=int(len(pruned)), vessels_out=int(pruned["mmsi"].nunique()),
            partitions_written=getattr(report, "partitions_written",
                                       getattr(report, "partitions", None)),
            buffer_km=BUFFER_KM, window_pad_h=WINDOW_PAD.total_seconds() / 3600,
            t_fullscan_ms=round(t_fullscan * 1e3, 1),
            t_ingest_ms=round(t_ingest * 1e3, 1),
            t_query_ms=round(t_query * 1e3, 1),
        )
        return out, notes
    except Exception as exc:  # noqa: BLE001 -- accelerator, never a gate
        notes["reason"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return vessels_native, notes


def _cloud_hull(origin_native: Path):
    """Convex hull of all particle/ellipse geometries in the origin cloud."""
    try:
        import json
        from shapely.geometry import shape, MultiPoint
        doc = json.loads(Path(origin_native).read_text(encoding="utf-8"))
        pts = []
        for f in doc.get("features", []):
            g = f.get("geometry")
            if not g:
                continue
            geom = shape(g)
            pts.extend(list(geom.exterior.coords) if geom.geom_type == "Polygon"
                       else [(geom.x, geom.y)] if geom.geom_type == "Point"
                       else [c for part in getattr(geom, "geoms", []) for c in getattr(part, "coords", [])])
        return MultiPoint(pts).convex_hull if len(pts) >= 3 else None
    except Exception:
        return None
