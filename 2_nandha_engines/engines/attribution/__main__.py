"""Frozen CLI for Engine C (handbook §7).

    python -m engines.attribution --origin origin_cloud.geojson \
        --vessels vessels.parquet \
        --weights config/attribution_weights.yaml --out suspects.json

Exit 0 on success, exit 2 on a declared engine error; the status JSON is printed either
way. `--slick` is optional: with it the trajectory factor uses Engine A's measured
orientation, without it the axis is derived from the origin cloud's seeded particles.
"""

import argparse
import json
import sys

from .runner import DEFAULT_INVESTIGATION_ID, DEFAULT_WEIGHTS_PATH, attribute

EXIT_OK = 0
EXIT_ENGINE_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engines.attribution",
        description="Engine C - rank vessels against an oil-spill origin cloud",
    )
    parser.add_argument("--origin", required=True, help="origin_cloud.geojson from Engine B")
    parser.add_argument("--vessels", required=True, help="vessels.parquet")
    parser.add_argument("--out", required=True, help="output suspects.json path")
    parser.add_argument("--weights", default=str(DEFAULT_WEIGHTS_PATH))
    parser.add_argument(
        "--slick", default=None,
        help="slick.geojson, for Engine A's measured slick orientation (optional)",
    )
    parser.add_argument("--investigation-id", default=DEFAULT_INVESTIGATION_ID)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    status = attribute(
        args.origin,
        args.vessels,
        args.out,
        weights_path=args.weights,
        investigation_id=args.investigation_id,
        slick_path=args.slick,
    )
    json.dump(status, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return EXIT_OK if status["ok"] else EXIT_ENGINE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
