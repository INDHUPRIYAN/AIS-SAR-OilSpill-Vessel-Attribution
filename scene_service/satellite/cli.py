"""Command Line Interface for Satellite Scene Service.

Provides CLI access for:
- Fetching/retrieving Sentinel-1 SAR scenes by scene ID
- Searching scenes by geographic bounding box and time window
- Checking operational health status of CDSE and ASF providers
- Offline / mock operation
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .asf_adapter import ASFAdapter
from .cache import LocalSceneCache
from .cdse_adapter import CDSEAdapter
from .chain import SceneRetrievalChain
from .models import GeoBoundingBox, SceneMetadata, SceneSearchResult
from .status import get_api_status


def parse_bbox_string(bbox_str: str) -> GeoBoundingBox:
    """Parses comma-separated string 'min_lon,min_lat,max_lon,max_lat' into GeoBoundingBox."""
    parts = [p.strip() for p in bbox_str.split(",")]
    if len(parts) != 4:
        raise ValueError(
            f"Invalid bbox format '{bbox_str}'. Expected 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat"
        )
    try:
        coords = [float(p) for p in parts]
    except ValueError as err:
        raise ValueError(f"Non-numeric coordinate in bbox '{bbox_str}': {err}") from err

    return GeoBoundingBox.from_list(coords)


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parses ISO-8601 string to a UTC-aware datetime."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError as err:
        raise ValueError(f"Invalid ISO-8601 datetime format '{dt_str}': {err}") from err


def build_parser() -> argparse.ArgumentParser:
    """Builds and configures the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="satellite",
        description="Sentinel-1 SAR Satellite Scene Acquisition Service CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--scene-id",
        type=str,
        help="Sentinel-1 scene ID to acquire (e.g., S1A_IW_GRDH_1SDV_20231012T172530)",
    )
    group.add_argument(
        "--check-status",
        action="store_true",
        help="Check operational health/status of CDSE and ASF providers",
    )
    group.add_argument(
        "--bbox",
        type=str,
        help="Geographic bounding box for search: 'min_lon,min_lat,max_lon,max_lat' (e.g., '2.5,51.5,3.2,52.1')",
    )
    group.add_argument(
        "--calibrate",
        type=str,
        metavar="SAFE_DIR",
        help="Radiometrically calibrate an unpacked Sentinel-1 .SAFE product "
        "(DN -> Sigma0 dB, thermal noise removed, GCP-warped to EPSG:4326, land-masked)",
    )
    group.add_argument(
        "--check-domain-gap",
        type=str,
        metavar="SIGMA0_TIF",
        help="Run the domain-gap gate: compare a calibrated scene's sea-pixel "
        "p1-p99 dB against the normalisation.yaml clip range (PASS / DOMAIN GAP)",
    )

    parser.add_argument(
        "--start-time",
        type=str,
        default=None,
        help="Search start timestamp in ISO-8601 format (e.g., 2023-10-12T00:00:00Z)",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        default=None,
        help="Search end timestamp in ISO-8601 format (e.g., 2023-10-13T00:00:00Z)",
    )
    parser.add_argument(
        "--product-type",
        type=str,
        default="GRD",
        help="Sentinel-1 product type (e.g., GRD, SLC)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom destination directory for downloaded scenes",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Directory for local scene caching (defaults to CACHE_DIR env or ./data/cache/satellite)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in offline mock mode without remote network requests or credentials",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output path for --calibrate (defaults to <SAFE_DIR>/scene_sigma0_db.tif)",
    )
    parser.add_argument(
        "--pol",
        type=str,
        default="vv",
        choices=["vv", "vh", "hh", "hv"],
        help="Polarisation to calibrate",
    )
    parser.add_argument(
        "--no-land-mask",
        action="store_true",
        help="Skip the land mask step during calibration (debugging only)",
    )
    parser.add_argument(
        "--no-clip",
        action="store_true",
        help="Skip the db_range clip (audit/diagnostics only — NEVER for production output)",
    )
    parser.add_argument(
        "--calibrate-after-fetch",
        action="store_true",
        help="After --scene-id retrieval, calibrate the delivered product if its "
        "annotation XMLs are present alongside (adds minutes of runtime)",
    )
    parser.add_argument(
        "--status-out",
        type=str,
        default=None,
        help="With --check-status: also write provider_status.json (frozen contract 8) to this path",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint.

    Returns:
        0 on success, 1 on operational failure, 2 on invalid arguments.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # 0a. Radiometric calibration of an unpacked SAFE product
    if args.calibrate:
        from .calibrate import calibrate_safe

        try:
            out_path = args.out or os.path.join(args.calibrate, "scene_sigma0_db.tif")
            stats = calibrate_safe(
                safe_dir=args.calibrate,
                out_path=out_path,
                polarisation=args.pol,
                land_mask=not args.no_land_mask,
                clip=not args.no_clip,
            )
            print(json.dumps({"success": True, **stats}, indent=2))
            return 0
        except Exception as err:
            print(json.dumps({"success": False, "error": f"Calibration failed: {err}"}, indent=2))
            return 1

    # 0b. Domain-gap gate on a calibrated Sigma0 dB scene
    if args.check_domain_gap:
        from .calibrate import check_domain_gap

        try:
            result = check_domain_gap(args.check_domain_gap)
            print(json.dumps({"success": True, **result}, indent=2))
            return 0 if result["verdict"] == "PASS" else 1
        except Exception as err:
            print(json.dumps({"success": False, "error": f"Domain-gap check failed: {err}"}, indent=2))
            return 1

    # 1. Health Status Check
    if args.check_status:
        try:
            status_map = get_api_status(mock_mode=args.mock)
            output_dict = {
                "success": True,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "providers": {
                    prov: health.model_dump() for prov, health in status_map.items()
                },
            }
            if args.status_out:
                from .status import write_provider_status_file

                write_provider_status_file(args.status_out, status_map)
                output_dict["status_file"] = args.status_out
            print(json.dumps(output_dict, indent=2))
            return 0
        except Exception as err:
            err_dict = {
                "success": False,
                "error": f"Failed to check provider health: {err}",
            }
            print(json.dumps(err_dict, indent=2))
            return 1

    # Determine directories from args or environment
    cache_dir = (
        args.cache_dir
        or os.getenv("CACHE_DIR")
        or "./data/cache/satellite"
    )
    output_dir = (
        args.output_dir
        or os.getenv("OUTPUT_DIR")
        or os.path.join(cache_dir, "downloads")
    )

    # Initialize adapters, cache, and retrieval chain
    cache = LocalSceneCache(cache_dir=cache_dir)
    cdse = CDSEAdapter(mock_mode=args.mock)
    asf = ASFAdapter(mock_mode=args.mock)
    chain = SceneRetrievalChain(
        cdse_adapter=cdse,
        asf_adapter=asf,
        cache=cache,
        download_dir=output_dir,
    )

    # 2. Scene Retrieval by Scene ID
    if args.scene_id:
        try:
            response = chain.retrieve_scene(args.scene_id)
            resp_dict = response.model_dump()
            # Optional post-fetch calibration: only possible when the delivered
            # product is an unpacked .SAFE (annotation XMLs beside the raster).
            # Behind a flag because it adds minutes of runtime to a fetch.
            if args.calibrate_after_fetch and response.success and response.geotiff_path:
                from .calibrate import calibrate_safe, find_safe_files

                safe_dir = os.path.dirname(os.path.abspath(response.geotiff_path))
                try:
                    find_safe_files(safe_dir, args.pol)  # raises if not calibratable
                    out_path = args.out or os.path.join(safe_dir, "scene_sigma0_db.tif")
                    resp_dict["calibration"] = calibrate_safe(
                        safe_dir=safe_dir,
                        out_path=out_path,
                        polarisation=args.pol,
                        land_mask=not args.no_land_mask,
                        provider_used=response.source_provider or "unknown",
                    )
                except FileNotFoundError as cal_err:
                    resp_dict["calibration"] = {
                        "skipped": True,
                        "reason": f"Product is not an unpacked SAFE with annotation XMLs: {cal_err}",
                    }
            print(json.dumps(resp_dict, indent=2, default=str))
            return 0 if response.success else 1
        except Exception as err:
            err_dict = {
                "success": False,
                "scene_id": args.scene_id,
                "source_provider": None,
                "error_message": str(err),
            }
            print(json.dumps(err_dict, indent=2))
            return 1

    # 3. Scene Search by Bounding Box
    if args.bbox:
        try:
            geo_bbox = parse_bbox_string(args.bbox)
        except Exception as err:
            err_dict = {
                "success": False,
                "error": f"Invalid --bbox argument: {err}",
            }
            print(json.dumps(err_dict, indent=2))
            return 2

        try:
            dt_start = parse_iso_datetime(args.start_time)
            dt_end = parse_iso_datetime(args.end_time)
        except Exception as err:
            err_dict = {
                "success": False,
                "error": f"Invalid datetime argument: {err}",
            }
            print(json.dumps(err_dict, indent=2))
            return 2

        try:
            search_res = chain.search_scenes(
                bbox=geo_bbox,
                start_time=dt_start,
                end_time=dt_end,
                product_type=args.product_type,
            )
            output_dict = {
                "success": True,
                "total_count": search_res.total_count,
                "provider": search_res.provider,
                "query_bbox": geo_bbox.to_list(),
                "scenes": [s.model_dump() for s in search_res.scenes],
            }
            print(json.dumps(output_dict, indent=2, default=str))
            return 0
        except Exception as err:
            err_dict = {
                "success": False,
                "error": f"Scene search failed: {err}",
            }
            print(json.dumps(err_dict, indent=2))
            return 1

    # 4. No action specified
    parser.print_help(file=sys.stderr)
    err_dict = {
        "success": False,
        "error": "No action specified. Please provide --scene-id, --bbox, or --check-status.",
    }
    print(json.dumps(err_dict, indent=2))
    return 2


if __name__ == "__main__":
    sys.exit(main())

