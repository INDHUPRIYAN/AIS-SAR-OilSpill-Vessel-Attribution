"""Frozen CLI for Engine B (handbook §7).

    python -m engines.drift --slick slick.geojson \
        --currents currents.nc --wind wind.nc \
        --mode hindcast --hours 24 --out origin_cloud.geojson

In forecast mode `--hours` sets the run length and the horizons come from the config,
filtered to those the run reaches:

    python -m engines.drift --slick slick.geojson --currents currents.nc         --wind wind.nc --mode forecast --hours 24 --out forecast.geojson

Exit 0 on success, exit 2 on a declared engine error; the status JSON is printed either
way.
"""

import argparse
import json
import sys

from .runner import DEFAULT_CONFIG, forecast, hindcast

EXIT_OK = 0
EXIT_ENGINE_ERROR = 2
MODES = {"hindcast": hindcast, "forecast": forecast}


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
    engine = MODES[args.mode]

    status = engine(
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
