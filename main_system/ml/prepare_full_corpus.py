"""Production full-corpus tiling: Parts 1+2 whole, Part 3 minus the holdout.

The promotion gate compares old and new models on the SAME frozen holdout the
POC model was evaluated on -- the 90 Part-3 scenes in processed/trujillo/test.
That cache is never rebuilt here. The other 360 Part-3 scenes ARE trainable,
and they are derived as the COMPLEMENT of the salvaged holdout index, not from
a re-derived split: re-deriving would silently move scenes across the holdout
boundary and poison the comparison.

Usage:
    python -m ml.prepare_full_corpus            # writes processed/trujillo/trainval
    python -m ml.prepare_full_corpus --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys

from ml.audit import pair_files, RASTER_EXT
from ml.config import DATA_ROOT, load_config
from ml.prepare_trujillo import _tile_pairs

POC_TEST_INDEX = (DATA_ROOT / "processed" / "trujillo" / "_poc_backup"
                  / "poc_test_index.json")
HOLDOUT_SCENES = 90   # frozen; 360 trainval + 90 holdout = 450 Part-3 scenes


def collect_pairs(part: int):
    root = DATA_ROOT / "raw" / "trujillo" / f"part{part}"
    files = sorted(p for p in root.rglob("*") if p.suffix in RASTER_EXT)
    pairs, unpaired = pair_files(files)
    return [(i, m) for i, m in pairs if m is not None], unpaired


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args(argv)

    cfg = load_config()
    poc = json.loads(POC_TEST_INDEX.read_text())
    if not poc["meta"].get("poc_holdout"):
        print("salvaged test index is not the POC holdout; refusing.")
        return 2
    # POC-era scene keys are category/stem (no part prefix -- single-part cache).
    holdout = {t["scene"] for t in poc["tiles"]}
    if len(holdout) != HOLDOUT_SCENES:
        print(f"holdout index lists {len(holdout)} scenes; expected "
              f"{HOLDOUT_SCENES}. Refusing: the holdout boundary is off.")
        return 2

    pairs = []
    for part in (1, 2):
        part_pairs, unpaired = collect_pairs(part)
        print(f"  part{part}: {len(part_pairs)} pair(s), {len(unpaired)} unpaired")
        pairs += part_pairs

    p3_pairs, _ = collect_pairs(3)
    kept = [(i, m) for i, m in p3_pairs
            if f"{i.parent.name}/{i.stem}" not in holdout]
    held = len(p3_pairs) - len(kept)
    print(f"  part3: {len(kept)} trainval-carve pair(s) kept, "
          f"{held} holdout scene(s) EXCLUDED (they are the promotion gate)")
    if held != HOLDOUT_SCENES:
        print(f"  expected exactly {HOLDOUT_SCENES} excluded; got {held}. Refusing.")
        return 2
    pairs += kept

    print(f"  total: {len(pairs)} scene pair(s) -> trainval")
    if args.dry_run:
        print("  --dry-run: stopping before writing")
        return 0

    _tile_pairs(pairs, "trainval", cfg, args.seed, "1+2+3carve", False,
                DATA_ROOT / "raw" / "trujillo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
