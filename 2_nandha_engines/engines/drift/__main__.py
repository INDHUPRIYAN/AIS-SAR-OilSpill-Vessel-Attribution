"""Frozen CLI for Engine B (handbook §7).

    python -m engines.drift --slick slick.geojson \
        --currents currents.nc --wind wind.nc \
        --mode hindcast --hours 24 --out origin_cloud.geojson

Exit 0 on success, exit 2 on a declared engine error; the status JSON is printed either
way. `--mode forecast` arrives in Phase 4 and is rejected until then rather than being
silently treated as a hindcast.
"""

import argparse
import json
import sys

from .runner import DEFAULT_CONFIG, hindcast

EXIT_OK = 0
EXIT_ENGINE_ERROR = 2
IMPLEMENTED_MODES = ("hindcast",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engines.drift",
        description="Engine B - backtrack a slick to its probable origin",
    )
    parser.add_argument("--slick", required=True, help="slick.geojson from Engine A")
    parser.add_argument("--currents", default=None, help="currents.nc (u/v)")
    parser.add_argument("--wind", default=None, help="wind.nc (u10/v10)")
    parser.add_argument("--mode", default="hindcast", choices=("hindcast", "forecast"))
    parser.add_argument("--hours", type=float, default=None, help="run length (default 24)")
    parser.add_argument("--out", required=True, help="output GeoJSON path")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--slick-id", default=None, help="seed from this slick_id")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.mode not in IMPLEMENTED_MODES:
        json.dump(
            {
                "ok": False,
                "engine_used": "fallback",
                "warnings": [],
                "error": {
                    "error_class": "MISSING_INPUT",
                    "message": f"--mode {args.mode} is not implemented yet "
                               "(forecast lands in Phase 4)",
                    "detail": {"implemented_modes": list(IMPLEMENTED_MODES)},
                },
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return EXIT_ENGINE_ERROR

    status = hindcast(
        args.slick,
        args.out,
        currents_path=args.currents,
        wind_path=args.wind,
        config_path=args.config,
        hours=args.hours,
        slick_id=args.slick_id,
    )
    json.dump(status, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return EXIT_OK if status["ok"] else EXIT_ENGINE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
