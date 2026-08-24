"""Trujillo tile-and-discard preparation -- step 3 of the training workflow.

Turns 2048x2048 Sigma0-dB scenes into a compact uint8 tile cache the 4050 can
train on, and (with --discard) deletes each source archive as soon as it has
been tiled, so the 40-60 GB dataset never sits on disk in full.

Two deliberate decisions, both of which affect what our metrics mean:

1.  TRAIN/VAL tiles are filtered -- keep tiles with >= min_oil_fraction oil,
    plus an equal number of matched hard negatives drawn from the SAME scene.
    Same-scene negatives force the model to learn oil texture rather than
    "which scene is this".

2.  TEST tiles (Part III) are NOT filtered. The test split is 150 oil / 150
    look-alike / 150 no-oil precisely so we can report per-phenomenon false
    positives; dropping its no-oil tiles would inflate every number we quote.

Train/val is split BY SCENE, never by tile: two tiles from one 2048px scene
are near-duplicates, so a tile-level split leaks val into train and reports an
IoU several points too high.

Usage:
    python -m ml.prepare_trujillo --part 3                 # test harness
    python -m ml.prepare_trujillo --part 1 --discard       # then delete source
    python -m ml.prepare_trujillo --part 3 --dry-run
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import numpy as np
from tqdm import tqdm

from ml.audit import pair_files, read_raster, RASTER_EXT
from ml.config import DATA_ROOT, db_to_uint8, load_config

TILES_ROOT = DATA_ROOT / "processed" / "trujillo"


class TileWriter:
    """Appends fixed-size tiles to a raw stream, then finalises to .npy.

    Tile counts are not known until every scene is read, and holding tens of
    thousands of tiles in RAM to find out is wasteful -- so append raw, then
    stamp a proper .npy header once the count is known.
    """

    def __init__(self, out_dir: Path, tile: int):
        self.out_dir = out_dir = Path(out_dir)   # callers may pass a str
        self.tile = tile
        out_dir.mkdir(parents=True, exist_ok=True)
        self.img_raw = out_dir / "images.raw"
        self.msk_raw = out_dir / "masks.raw"
        self.img_fh = self.img_raw.open("wb")
        self.msk_fh = self.msk_raw.open("wb")
        self.n = 0
        self.records: list[dict] = []

    def add(self, img: np.ndarray, mask: np.ndarray, scene: str, row: int,
            col: int, oil_frac: float, kind: str) -> None:
        assert img.shape == (self.tile, self.tile), img.shape
        assert img.dtype == np.uint8 and mask.dtype == np.uint8
        self.img_fh.write(img.tobytes())
        self.msk_fh.write(mask.tobytes())
        self.records.append({"i": self.n, "scene": scene, "row": row, "col": col,
                             "oil_fraction": round(oil_frac, 6), "kind": kind})
        self.n += 1

    def finalise(self, meta: dict) -> None:
        self.img_fh.close()
        self.msk_fh.close()
        if self.n == 0:
            self.img_raw.unlink(missing_ok=True)
            self.msk_raw.unlink(missing_ok=True)
            print("  no tiles written")
            return

        for raw, name in ((self.img_raw, "images.npy"), (self.msk_raw, "masks.npy")):
            src = np.memmap(raw, dtype=np.uint8, mode="r",
                            shape=(self.n, self.tile, self.tile))
            dst = np.lib.format.open_memmap(
                self.out_dir / name, mode="w+", dtype=np.uint8,
                shape=(self.n, self.tile, self.tile))
            step = 2048
            for s in range(0, self.n, step):
                dst[s:s + step] = src[s:s + step]
            dst.flush()
            del dst, src
            raw.unlink()

        meta = {**meta, "n_tiles": self.n, "tile_size": self.tile}
        (self.out_dir / "index.json").write_text(
            json.dumps({"meta": meta, "tiles": self.records}, indent=2),
            encoding="utf-8")
        size_mb = sum((self.out_dir / f).stat().st_size
                      for f in ("images.npy", "masks.npy")) / 1024 ** 2
        print(f"  wrote {self.n} tiles ({size_mb:.1f} MB) -> {self.out_dir}")


def iter_tiles(arr: np.ndarray, tile: int, stride: int):
    """Yield (row, col, view). Partial edge tiles are dropped rather than
    padded -- padded edges teach the model that black borders mean 'no oil'."""
    h, w = arr.shape[-2:]
    for r in range(0, h - tile + 1, stride):
        for c in range(0, w - tile + 1, stride):
            yield r, c, arr[..., r:r + tile, c:c + tile]


def prepare_part(part: int, split: str, discard: bool, dry_run: bool,
                 seed: int = 1337) -> dict:
    cfg = load_config()
    t, stride = cfg.tiling.tile_size, cfg.tiling.stride
    src_root = DATA_ROOT / "raw" / "trujillo" / f"part{part}"
    out_dir = TILES_ROOT / split

    print(f"\n=== Preparing Trujillo Part {part} as '{split}' ===")
    print(f"  source: {src_root}")
    print(f"  tiles : {t}px stride {stride}   dB clip "
          f"[{cfg.sar.db_min}, {cfg.sar.db_max}]   fingerprint {cfg.fingerprint}")
    if not src_root.exists():
        print(f"  NOT DOWNLOADED. Run: python -m ml.download --dataset trujillo "
              f"--part {part}")
        return {"part": part, "ok": False}

    files = sorted(p for p in src_root.rglob("*") if p.suffix in RASTER_EXT)
    pairs, unpaired = pair_files(files)
    pairs = [(i, m) for i, m in pairs if m is not None]
    print(f"  {len(pairs)} image/mask pair(s), {len(unpaired)} unpaired image(s)")
    if not pairs:
        print("  nothing to tile (unpacked yet? run ml.audit to inspect)")
        return {"part": part, "ok": False}

    filter_tiles = split != "test"
    if filter_tiles:
        rule = (f">={cfg.tiling.min_oil_fraction * 100:.1f}% oil "
                f"+ {cfg.tiling.hard_negative_ratio:g}x matched hard negatives")
    else:
        rule = "NONE (test split kept whole, so FP rates stay honest)"
    print(f"  filtering: {rule}")
    if dry_run:
        print("  --dry-run: stopping before writing")
        return {"part": part, "ok": True, "pairs": len(pairs), "dry_run": True}

    rng = random.Random(seed)
    writer = TileWriter(out_dir, t)
    kept_oil = kept_neg = seen = 0

    for img_p, mask_p in tqdm(pairs, desc=f"  part{part}", unit="scene", disable=None):
        try:
            img_arr, _ = read_raster(img_p)
            mask_arr, _ = read_raster(mask_p)
        except Exception as exc:
            print(f"\n  SKIP {img_p.name}: {exc}")
            continue

        band = img_arr[cfg.sar.primary_band - 1].astype(np.float32)
        mask = (mask_arr[0] > 0).astype(np.uint8)
        if band.shape != mask.shape:
            print(f"\n  SKIP {img_p.name}: image {band.shape} != mask {mask.shape}")
            continue

        scene = img_p.stem
        img_u8 = db_to_uint8(band, cfg)

        oil_tiles, neg_tiles = [], []
        for r, c, mtile in iter_tiles(mask, t, stride):
            seen += 1
            frac = float(mtile.mean())
            itile = img_u8[r:r + t, c:c + t]
            if not filter_tiles:
                writer.add(itile, mtile, scene, r, c, frac,
                           "oil" if frac > 0 else "background")
                kept_oil += frac > 0
                kept_neg += frac == 0
            elif frac >= cfg.tiling.min_oil_fraction:
                oil_tiles.append((itile, mtile, r, c, frac))
            elif frac == 0.0:
                neg_tiles.append((itile, mtile, r, c, frac))

        if filter_tiles:
            for itile, mtile, r, c, frac in oil_tiles:
                writer.add(itile, mtile, scene, r, c, frac, "oil")
            kept_oil += len(oil_tiles)
            n_neg = int(round(len(oil_tiles) * cfg.tiling.hard_negative_ratio))
            for itile, mtile, r, c, frac in rng.sample(
                    neg_tiles, min(n_neg, len(neg_tiles))):
                writer.add(itile, mtile, scene, r, c, frac, "hard_negative")
                kept_neg += 1

    writer.finalise({
        "dataset": "trujillo", "part": part, "split": split,
        "source": str(src_root), "config_fingerprint": cfg.fingerprint,
        "db_min": cfg.sar.db_min, "db_max": cfg.sar.db_max,
        "primary_band": cfg.sar.primary_band, "seed": seed,
        "filtered": filter_tiles, "scenes": len(pairs),
        "tiles_examined": seen, "oil_tiles": kept_oil, "negative_tiles": kept_neg,
    })
    print(f"  examined {seen} tiles -> kept {kept_oil} oil + {kept_neg} negative")

    if discard:
        print(f"  --discard: removing source {src_root}")
        shutil.rmtree(src_root)
        print("  source deleted (tiles retained)")

    return {"part": part, "ok": True, "tiles": writer.n,
            "oil": kept_oil, "negative": kept_neg}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", type=int, required=True, choices=[1, 2, 3])
    ap.add_argument("--split", choices=["trainval", "test"],
                    help="Default: test for part 3, trainval for parts 1-2.")
    ap.add_argument("--discard", action="store_true",
                    help="Delete the source archive after tiling (parts 1-2).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args(argv)

    split = args.split or ("test" if args.part == 3 else "trainval")
    if args.discard and args.part == 3:
        print("Refusing --discard on Part III: it is the untouched test harness.")
        return 2

    prepare_part(args.part, split, args.discard, args.dry_run, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
