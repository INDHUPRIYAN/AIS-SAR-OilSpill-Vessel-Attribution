"""Frozen CLI for Engine A (handbook §7).

    python -m engines.characterise --mask <tif> --scene-meta <json> --out slick.geojson

Prints the status object of §4.5 to stdout as JSON. Exit codes:

    0  ok
    2  a declared engine error (EMPTY_MASK / MISSING_INPUT) - status JSON still printed

A traceback is never the interface; the main system reads the JSON either way.
"""

from __future__ import annotations

import argparse
import json
import sys

from .runner import DEFAULT_CONFIG, characterise

EXIT_OK = 0
EXIT_ENGINE_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engines.characterise",
        description="Engine A - turn a raw oil-spill mask into slick.geojson",
    )
    parser.add_argument("--mask", required=True, help="0/1 mask GeoTIFF from detection")
    parser.add_argument(
        "--scene-meta", required=True, help="scene metadata JSON (scene_id, UTC, ...)"
    )
    parser.add_argument("--out", required=True, help="output slick.geojson path")
    parser.add_argument(
        "--scene-db",
        default=None,
        help="calibrated Sigma0 dB GeoTIFF for the damping ratio "
        "(default: the 'file_path' recorded in the scene metadata)",
    )
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG), help="characterise tuning YAML"
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="detection confidence 0-1, used only when the scene metadata omits it",
    )
    parser.add_argument(
        "--slick-id-prefix",
        default=None,
        help="override the slick_id prefix (default: trailing token of scene_id)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status = characterise(
        args.mask,
        args.scene_meta,
        args.out,
        scene_db_path=args.scene_db,
        config_path=args.config,
        confidence=args.confidence,
        slick_id_prefix=args.slick_id_prefix,
    )
    json.dump(status, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return EXIT_OK if status["ok"] else EXIT_ENGINE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
