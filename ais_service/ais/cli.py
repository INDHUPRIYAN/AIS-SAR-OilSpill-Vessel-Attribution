"""AIS Service CLI.

Two families of command:

  fetch-ais / generate-ais / build-benchmark   produce a single vessels.parquet
  index-*                                      manage the partitioned archive
                                               that Stage 6 actually queries
                                               (design doc v2 section 8 and 12)

The index commands exist because a per-run full scan of a real coastal month is
minutes per attribution. `index-ingest` loads a bulk archive once; `index-query`
and `index-cloud` then answer in milliseconds by pruning to the partitions that
can possibly match.
"""

import argparse
import json
import sys
from pathlib import Path

from .dma_ingest import DMAIngest
from .mc_ingest import MarineCadastreIngest
from .generator import generate_synthetic_ais
from .benchmark import build_benchmark
from .status import get_provider_status

def main():
    parser = argparse.ArgumentParser(description="AIS Service CLI")
    
    subparsers = parser.add_subparsers(dest="command", required=False)
    
    # fetch-ais
    fetch_parser = subparsers.add_parser("fetch-ais")
    fetch_parser.add_argument("--bbox", nargs=4, type=float, required=True)
    fetch_parser.add_argument("--start", type=str, required=True)
    fetch_parser.add_argument("--end", type=str, required=True)
    fetch_parser.add_argument("--source", type=str, choices=['dma', 'marinecadastre'], required=True)
    fetch_parser.add_argument("--out", type=str, default="vessels.parquet")
    fetch_parser.add_argument("--input-file", type=str, help="Path to raw CSV file", default="data/ais/raw.csv")
    
    # generate-ais
    gen_parser = subparsers.add_parser("generate-ais")
    gen_parser.add_argument("--bbox", nargs=4, type=float, required=True)
    gen_parser.add_argument("--start", type=str, required=True)
    gen_parser.add_argument("--end", type=str, required=True)
    gen_parser.add_argument("--n-vessels", type=int, required=True)
    gen_parser.add_argument("--culprit-json", type=str, required=True)
    gen_parser.add_argument("--seed", type=int, default=42)
    gen_parser.add_argument("--fleet-seed", type=int, default=None)
    gen_parser.add_argument("--hard-negatives", type=int, default=3,
                            help="innocent near-miss vessels planted alongside "
                                 "the culprit (0 disables)")
    gen_parser.add_argument("--out", type=str, default="vessels.parquet")

    # build-benchmark
    bench_parser = subparsers.add_parser("build-benchmark")
    bench_parser.add_argument("--scenarios", type=int, default=50)
    bench_parser.add_argument("--master-seed", type=int, default=1337)
    bench_parser.add_argument("--hard-negatives", type=int, default=3)
    bench_parser.add_argument("--out", type=str, default="data/ais/benchmark/")

    # ---- index (design doc v2 section 8: the spatial index) -------------
    ing = subparsers.add_parser(
        "index-ingest", help="load an archive (CSV or parquet) into the store")
    ing.add_argument("--store", default="data/ais/store")
    ing.add_argument("--region", required=True,
                     help="partition key, e.g. 'denmark' or 'us-gulf'")
    ing.add_argument("--input-file", required=True,
                     help="raw bulk CSV, or an already contract-shaped .parquet")
    ing.add_argument("--source", choices=["dma", "marinecadastre", "parquet"],
                     default="parquet")
    ing.add_argument("--bbox", nargs=4, type=float, default=None,
                     help="clip the archive on the way in "
                          "(lon_min lat_min lon_max lat_max)")
    ing.add_argument("--start", default=None)
    ing.add_argument("--end", default=None)
    ing.add_argument("--granularity", choices=["day", "hour"], default="day")

    q = subparsers.add_parser("index-query", help="bbox + time window query")
    q.add_argument("--store", default="data/ais/store")
    q.add_argument("--bbox", nargs=4, type=float, required=True)
    q.add_argument("--start", required=True)
    q.add_argument("--end", required=True)
    q.add_argument("--region", default=None)
    q.add_argument("--out", default=None, help="write vessels.parquet here")

    qc = subparsers.add_parser(
        "index-cloud", help="vessels that entered a buffered origin cloud")
    qc.add_argument("--store", default="data/ais/store")
    qc.add_argument("--cloud", required=True, help="origin_cloud.geojson")
    qc.add_argument("--start", required=True)
    qc.add_argument("--end", required=True)
    qc.add_argument("--buffer-km", type=float, default=5.0)
    qc.add_argument("--region", default=None)
    qc.add_argument("--out", default=None, help="write vessels.parquet here")

    st = subparsers.add_parser("index-stats", help="what the archive holds")
    st.add_argument("--store", default="data/ais/store")

    vf = subparsers.add_parser(
        "index-verify",
        help="every indexed partition still present and unchanged")
    vf.add_argument("--store", default="data/ais/store")

    rb = subparsers.add_parser(
        "index-rebuild", help="regenerate _index.json from the partitions on disk")
    rb.add_argument("--store", default="data/ais/store")
    rb.add_argument("--granularity", choices=["day", "hour"], default="day")

    # status
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--out", type=str, default="provider_status.json",
                               help="where to write the ProviderStatusFile JSON")
    status_parser.add_argument("--timeout", type=float, default=4.0)
    
    # Legacy flags compatibility
    parser.add_argument("--fetch", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--generate", action="store_true", help=argparse.SUPPRESS)
    
    args = parser.parse_args()
    
    if args.command == "fetch-ais":
        print(f"Fetching AIS data from {args.source}...")
        bbox = args.bbox
        try:
            if args.source == 'dma':
                df = DMAIngest.parse_dma_csv(args.input_file, bbox, args.start, args.end)
            else:
                df = MarineCadastreIngest.parse_mc_csv(args.input_file, bbox, args.start, args.end)
                
            if not df.empty:
                df.to_parquet(args.out, index=False)
                print(f"Saved to {args.out}")
            else:
                print("EMPTY_REGION: No vessels found in the given bounding box and time window.")
        except Exception as e:
            print(f"Error processing archive: {e}")
            print("ARCHIVE_UNAVAILABLE: mode=real falls back to mode=synthetic.")
        
    elif args.command == "generate-ais":
        print("Generating synthetic AIS coordinates...")
        bbox = args.bbox
        try:
            with open(args.culprit_json, 'r') as f:
                culprit_config = json.load(f)
        except Exception:
            # allow falling back if json not provided for test
            culprit_config = None
            
        df = generate_synthetic_ais(
            bbox=bbox,
            start_time=args.start,
            end_time=args.end,
            n_vessels=args.n_vessels,
            culprit_config=culprit_config,
            seed=args.seed,
            fleet_seed=getattr(args, "fleet_seed", None),
            n_hard_negatives=getattr(args, "hard_negatives", 0),
        )
        if not df.empty:
            df.to_parquet(args.out, index=False)
            print(f"Saved to {args.out}")

    elif args.command == "build-benchmark":
        build_benchmark(scenarios=args.scenarios, master_seed=args.master_seed,
                        out_dir=args.out, hard_negatives=args.hard_negatives)

    elif args.command == "index-ingest":
        return _cmd_index_ingest(args)

    elif args.command == "index-query":
        return _cmd_index_query(args)

    elif args.command == "index-cloud":
        return _cmd_index_cloud(args)

    elif args.command == "index-stats":
        from .index import AISStore
        print(json.dumps(AISStore(args.store).stats(), indent=2, default=str))

    elif args.command == "index-verify":
        from .index import AISStore
        problems = AISStore(args.store).verify()
        if problems:
            print(f"{len(problems)} problem(s):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("index-verify: OK -- every partition present and unchanged")

    elif args.command == "index-rebuild":
        from .index import AISStore
        parts = AISStore(args.store, granularity=args.granularity).rebuild_manifest()
        print(f"rebuilt index over {len(parts)} partition(s)")

    elif args.command == "status":
        status = get_provider_status(write_path=args.out, timeout_s=args.timeout)
        print(json.dumps(status, indent=2))
        print(f"Written to {args.out}")
        
    elif args.fetch:
        print("Fetching AIS data...")
    elif args.generate:
        print("Generating synthetic AIS coordinates...")
    else:
        parser.print_help()
    return 0


# --------------------------------------------------------------------------
# index command bodies
# --------------------------------------------------------------------------


def _load_archive(args):
    """Read the input file into a frame, applying the source-specific parser.

    A bbox/time clip on the way in is optional but strongly advised for bulk
    archives: MarineCadastre ships one CSV per day for the whole US coast, and
    there is no reason to keep the Aleutians in a Gulf-of-Mexico store.
    """
    import pandas as pd

    path = args.input_file
    if args.source == "parquet":
        return pd.read_parquet(path)

    if args.bbox is None or args.start is None or args.end is None:
        raise SystemExit(
            "--bbox, --start and --end are required when --source is a bulk CSV "
            "(the raw parsers filter as they stream, chunk by chunk)")
    if args.source == "dma":
        return DMAIngest.parse_dma_csv(path, args.bbox, args.start, args.end)
    return MarineCadastreIngest.parse_mc_csv(path, args.bbox, args.start, args.end)


def _cmd_index_ingest(args):
    from .index import AISStore

    df = _load_archive(args)
    if df is None or df.empty:
        print("EMPTY_REGION: nothing in the archive matched; store unchanged.")
        return 0

    store = AISStore(args.store, granularity=args.granularity)
    report = store.ingest(df, region=args.region)
    print(json.dumps(report.to_dict(), indent=2))
    if report.rows_dropped_contract:
        print(f"note: {report.rows_dropped_contract} row(s) failed the frozen "
              f"vessels contract and were dropped, not silently coerced")
    return 0


def _write_or_print(df, out, what):
    if df.empty:
        print(f"{what}: 0 rows")
        return 0
    print(f"{what}: {len(df)} rows, {df['mmsi'].nunique()} vessels, "
          f"{df['timestamp_utc'].min()} .. {df['timestamp_utc'].max()}")
    real = int((df["source"] == "real").sum())
    print(f"  provenance: {real} real, {len(df) - real} synthetic")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        print(f"  -> {out}")
    return 0


def _cmd_index_query(args):
    from .index import AISStore

    store = AISStore(args.store)
    df = store.query(tuple(args.bbox), args.start, args.end, region=args.region)
    return _write_or_print(df, args.out, "index-query")


def _cmd_index_cloud(args):
    from .index import AISStore

    store = AISStore(args.store)
    df = store.query_cloud(args.cloud, args.start, args.end,
                           buffer_km=args.buffer_km, region=args.region)
    return _write_or_print(df, args.out,
                           f"index-cloud (buffer {args.buffer_km} km)")


if __name__ == "__main__":
    sys.exit(main() or 0)
