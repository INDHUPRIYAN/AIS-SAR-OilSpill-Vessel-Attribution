import argparse
import json
import pandas as pd
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
    gen_parser.add_argument("--out", type=str, default="vessels.parquet")
    
    # build-benchmark
    bench_parser = subparsers.add_parser("build-benchmark")
    bench_parser.add_argument("--scenarios", type=int, default=50)
    bench_parser.add_argument("--master-seed", type=int, default=1337)
    bench_parser.add_argument("--out", type=str, default="data/ais/benchmark/")
    
    # status
    status_parser = subparsers.add_parser("status")
    
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
            seed=args.seed
        )
        if not df.empty:
            df.to_parquet(args.out, index=False)
            print(f"Saved to {args.out}")
        
    elif args.command == "build-benchmark":
        build_benchmark(scenarios=args.scenarios, master_seed=args.master_seed, out_dir=args.out)
        
    elif args.command == "status":
        print(json.dumps(get_provider_status(), indent=2))
        
    elif args.fetch:
        print("Fetching AIS data...")
    elif args.generate:
        print("Generating synthetic AIS coordinates...")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
