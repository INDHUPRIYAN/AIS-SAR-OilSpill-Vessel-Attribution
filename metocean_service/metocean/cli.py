"""
Command-Line Interface (CLI) for OceanTrace Met-Ocean Data Service.
Supports automated querying, bounding box parsing, provider selection, and JSON reporting.
"""

import argparse
import json
from pathlib import Path
import sys

# Ensure module root is on sys.path
MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from metocean.chain import MetoceanChain
from metocean.models import BBox, MetoceanRequest
from metocean.status import get_status


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fetch-metocean",
        description="OceanTrace Met-Ocean Data Service — Fetches surface currents and 10m atmospheric winds.",
    )

    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="Bounding box coordinates in EPSG:4326 (min_lon min_lat max_lon max_lat)",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start timestamp in ISO-8601 UTC format (e.g. 2017-01-29T00:00:00Z)",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End timestamp in ISO-8601 UTC format (e.g. 2017-02-02T00:00:00Z)",
    )
    parser.add_argument(
        "--what",
        type=str,
        choices=["currents", "wind", "both"],
        default="both",
        help="Data type to retrieve (currents, wind, or both)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["auto", "cmems", "hycom", "era5", "openmeteo", "cache"],
        default="auto",
        help="Provider selection mode (default: auto fallback chain)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/metocean",
        help="Destination directory for output NetCDF datasets and telemetry",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Display current provider health status telemetry and exit",
    )

    args = parser.parse_args()

    # Health check mode
    if args.health_check:
        status = get_status()
        print(json.dumps(status, indent=2))
        return 0

    # Validate required arguments for data fetch
    if not args.bbox or not args.start or not args.end:
        parser.error("--bbox, --start, and --end are required to fetch metocean data.")
        return 1

    try:
        req = MetoceanRequest(
            bbox=args.bbox,
            start=args.start,
            end=args.end,
            what=args.what,
            provider=args.provider,
            output_dir=args.output_dir,
        )

        chain = MetoceanChain()
        response = chain.fetch_metocean(req)

        print(json.dumps(response.to_dict(), indent=2))
        return 0 if response.status in ("success", "degraded") else 1

    except Exception as exc:
        err_payload = {
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
        print(json.dumps(err_payload, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
