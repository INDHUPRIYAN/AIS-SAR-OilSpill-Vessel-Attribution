"""Turn MarineCadastre daily archives into one run's real vessels.parquet.

The pipeline reads `<run_dir>/vessels.parquet` ahead of every other candidate
(`run.resolve_vessels`), so preparing a real-AIS run means putting a genuine,
contract-shaped file there before the run starts. This does that, and nothing
else: it neither invents vessels nor decides whether the AIS is good enough --
`vessels_cover_origin()` still makes that call during the run, and still falls
back to clearly-labelled SYNTHETIC when the answer is no.

Two things it is careful about.

**The archives are concatenated before parsing, not after.** Sentinel-1
descending passes over the Gulf are acquired just after 00:00 UTC, so a
backward drift window lies mostly in the *previous* day's archive. Parsing the
two days separately would run `interpolate_trajectory` on each in isolation and
leave every track severed at midnight -- precisely where the origin window
sits. One concatenated CSV, one parse, one continuous track.

**Identity travels beside the contract, not inside it.** `parse_mc_csv` lifts
vessel name, IMO and call sign off the raw frame before `to_contract()` drops
them, because the frozen 14-column contract does not carry identity and
widening it would break five modules that validate against it. Those go to
`vessel_identities.json`, which is where `vessel_index` already looks.

    python -m backend.prepare_ais \
        --csv data/ais/raw/AIS_2023_01_07.csv \
        --csv data/ais/raw/AIS_2023_01_08.csv \
        --scene-meta data/scenes/S1A_GULF_20230108/scene_meta.json \
        --out-dir data/runs/inv-gulf-flagship
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "main_system", REPO_ROOT / "ais_service"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CHUNK = 1 << 22


def hindcast_hours(default: float = 24.0) -> float:
    """How far back the drift hindcast actually runs, from its own config.

    The origin window is computed inside that span, so the AIS window has to
    match it. A number picked independently here would drift out of step the
    moment `drift.yaml` changed -- and the symptom would be a run reporting no
    vessel near the origin, which reads as a data problem rather than as two
    settings disagreeing.
    """
    path = REPO_ROOT / "analysis_engines" / "config" / "drift.yaml"
    try:
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:                                  # noqa: BLE001
        return default
    for block in doc.values():
        if isinstance(block, dict) and "hours" in block:
            return float(block["hours"])
    return float(doc.get("hours", default))


def concat_csvs(paths: List[Path], dest: Path) -> Path:
    """Stream several daily CSVs into one, keeping a single header row."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    first = None
    with open(dest, "wb") as out:
        for src in paths:
            with open(src, "rb") as fh:
                header = fh.readline()
                if not header.strip():
                    raise ValueError(f"{src} is empty")
                if first is None:
                    first = header
                    out.write(header)
                elif header != first:
                    # Dropping a mismatched header would shift every column by
                    # however much the schema moved, and the frame would still
                    # parse -- wrong positions, no error. Refuse instead.
                    raise ValueError(
                        f"{src} has a different header to {paths[0].name}; "
                        "concatenating them would misalign columns")
                while block := fh.read(CHUNK):
                    out.write(block)
    return dest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", action="append", required=True,
                    help="MarineCadastre daily CSV, repeatable and in date order")
    ap.add_argument("--scene-meta", required=True,
                    help="the run's scene_meta.json -- bbox and acquisition time "
                         "come from the scene, never from a flag")
    ap.add_argument("--out-dir", required=True, help="the run directory")
    ap.add_argument("--hours-back", type=float, default=None,
                    help="how far before acquisition to keep AIS "
                         "(default: the hindcast length from drift.yaml)")
    ap.add_argument("--work", default=None, help="scratch dir for the concatenated CSV")
    args = ap.parse_args(argv)

    meta = json.loads(Path(args.scene_meta).read_text(encoding="utf-8"))
    bbox = meta["bbox"]
    acquired = datetime.fromisoformat(str(meta["acquired_utc"]).replace("Z", "+00:00"))
    hours_back = args.hours_back if args.hours_back is not None else hindcast_hours()
    start = acquired - timedelta(hours=hours_back)

    csvs = [Path(c) for c in args.csv]
    missing = [c for c in csvs if not c.exists()]
    if missing:
        print(f"missing input: {missing}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(args.work) if args.work else out_dir / "_ais_work"
    combined = csvs[0]
    if len(csvs) > 1:
        combined = concat_csvs(csvs, work / "combined.csv")
        print(f"concatenated {len(csvs)} archive(s) -> {combined} "
              f"({combined.stat().st_size:,} B)")

    from ais.mc_ingest import MarineCadastreIngest

    print(f"scene   {meta['scene_id']}")
    print(f"bbox    {bbox}")
    print(f"window  {start:%Y-%m-%dT%H:%M:%SZ} .. {acquired:%Y-%m-%dT%H:%M:%SZ}")

    t0 = time.perf_counter()
    df = MarineCadastreIngest.parse_mc_csv(
        str(combined), bbox,
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        acquired.strftime("%Y-%m-%dT%H:%M:%SZ"))
    elapsed = round(time.perf_counter() - t0, 1)

    if df.empty:
        # A real and reportable outcome: the archive covers these waters, so an
        # empty result means no vessel reported inside the scene in the window.
        print(f"EMPTY: no AIS inside the scene bbox in the window ({elapsed}s)")
        return 1

    identities = dict(MarineCadastreIngest.last_identities or {})
    vessels = out_dir / "vessels.parquet"
    df.to_parquet(vessels, index=False)
    (out_dir / "vessel_identities.json").write_text(
        json.dumps({str(k): v for k, v in identities.items()}, indent=2),
        encoding="utf-8")

    interpolated = int(df["interpolated"].sum()) if "interpolated" in df else 0
    summary = {
        "scene_id": meta["scene_id"],
        "bbox": bbox,
        "window_utc": [start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       acquired.strftime("%Y-%m-%dT%H:%M:%SZ")],
        "hours_back": hours_back,
        "hours_back_source": ("--hours-back" if args.hours_back is not None
                              else "analysis_engines/config/drift.yaml drift.hours"),
        "archives": [str(c) for c in csvs],
        "rows": int(len(df)),
        "unique_mmsi": int(df["mmsi"].nunique()),
        "measured_rows": int(len(df) - interpolated),
        "interpolated_rows": interpolated,
        "identities": len(identities),
        "data_source": "real",
        "provider": "MarineCadastre (NOAA Office for Coastal Management)",
        "wall_seconds": elapsed,
    }
    (out_dir / "ais_ingest.json").write_text(json.dumps(summary, indent=2),
                                             encoding="utf-8")
    print(f"\n{summary['rows']:,} rows, {summary['unique_mmsi']} MMSI, "
          f"{interpolated:,} interpolated, {len(identities)} identities  ({elapsed}s)")
    print(f"  {vessels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
