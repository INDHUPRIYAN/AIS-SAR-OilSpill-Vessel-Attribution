"""Convenience wrapper around the three frozen engine CLIs.

The contract interface is `python -m engines.<engine>` (handbook §7); this script only
forwards to it, so `python cli.py drift --slick ...` works identically.
"""

import importlib
import sys

ENGINES = ("characterise", "drift", "attribution")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ENGINES:
        print(f"usage: python cli.py {{{'|'.join(ENGINES)}}} [engine options]")
        print("equivalent to: python -m engines.<engine> [engine options]")
        print()
        print("  characterise  mask GeoTIFF + scene metadata -> slick.geojson")
        print("  drift         slick + currents/wind -> origin_cloud / forecast")
        print("  attribution   origin cloud + vessels.parquet -> suspects.json")
        return 1

    engine, rest = sys.argv[1], sys.argv[2:]
    module = importlib.import_module(f"engines.{engine}.__main__")
    return module.main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
