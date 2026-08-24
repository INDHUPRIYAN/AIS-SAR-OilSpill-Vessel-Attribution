"""Convenience wrapper around the three frozen engine CLIs.

The contract interface is `python -m engines.<engine>` (handbook §7); this script only
forwards to it so `python cli.py characterise --mask ...` also works.
"""

import sys

ENGINES = ("characterise", "drift", "attribution")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ENGINES:
        print(f"usage: python cli.py {{{'|'.join(ENGINES)}}} [engine options]")
        print("equivalent to: python -m engines.<engine> [engine options]")
        return 1

    engine, rest = sys.argv[1], sys.argv[2:]
    if engine == "characterise":
        from engines.characterise.__main__ import main as engine_main
    else:
        print(f"engine '{engine}' is not implemented yet")
        return 1
    return engine_main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
