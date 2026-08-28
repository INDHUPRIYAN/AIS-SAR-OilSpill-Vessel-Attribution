"""Generate a synthetic tile cache in the exact prepared-data format.

This is NOT training data and must never appear in a reported metric. Its only
job is to exercise the pipeline -- dataset, training loop, evaluation, ONNX
export -- before the 40-60 GB Trujillo download finishes, so that when the real
tiles land the only unknown left is the data itself.

The tiles mimic the coarse statistics of Sigma0 dB sea surface: speckled
background (multiplicative gamma noise, as in real SAR) with darker elliptical
"slicks" of lower backscatter. A model trains on this to a high IoU quickly,
which proves the loop works and proves nothing about oil.

Usage:
    python -m ml.synth --split trainval --scenes 24
    python -m ml.synth --split test --scenes 12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from ml.config import db_to_uint8, load_config
from ml.prepare_trujillo import TILES_ROOT, TileWriter


def synth_scene(rng: np.random.Generator, size: int, cfg,
                n_slicks: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Return (sigma0_db, mask) for one fake scene."""
    # Speckle: gamma-distributed multiplicative noise is the standard SAR
    # intensity model; take 10*log10 to land in dB.
    looks = 4.0
    intensity = rng.gamma(shape=looks, scale=1.0 / looks, size=(size, size))
    sea_db = -12.0 + 10.0 * np.log10(np.clip(intensity, 1e-6, None))

    # Gentle large-scale wind modulation so the background is not stationary.
    yy, xx = np.mgrid[0:size, 0:size] / size
    sea_db += 3.0 * np.sin(2 * np.pi * (0.7 * xx + 0.3 * yy) + rng.uniform(0, 6.28))

    mask = np.zeros((size, size), dtype=np.uint8)
    for _ in range(rng.integers(0, n_slicks + 1)):
        cy, cx = rng.uniform(0.2, 0.8, 2) * size
        a, b = rng.uniform(0.06, 0.22) * size, rng.uniform(0.02, 0.07) * size
        theta = rng.uniform(0, np.pi)
        ct, st = np.cos(theta), np.sin(theta)
        dy, dx = np.mgrid[0:size, 0:size] - np.array([cy, cx]).reshape(2, 1, 1)
        u = (dx * ct + dy * st) / a
        v = (-dx * st + dy * ct) / b
        blob = (u ** 2 + v ** 2) <= 1.0
        # Slicks damp capillary waves -> lower backscatter, typically 4-10 dB.
        sea_db[blob] -= rng.uniform(5.0, 10.0)
        mask[blob] = 1

    return sea_db.astype(np.float32), mask


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["trainval", "test"], default="trainval")
    ap.add_argument("--scenes", type=int, default=24)
    ap.add_argument("--scene-size", type=int, default=1024)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    cfg = load_config()
    t, stride = cfg.tiling.tile_size, cfg.tiling.stride
    out_dir = args.out or (TILES_ROOT / args.split)
    rng = np.random.default_rng(args.seed)

    print(f"SYNTHETIC tiles -- not real data, never report metrics from these.")
    print(f"  split {args.split}  scenes {args.scenes}  "
          f"scene {args.scene_size}px  tile {t}px  -> {out_dir}")

    writer = TileWriter(out_dir, t)
    filter_tiles = args.split != "test"
    n_oil = n_neg = 0

    for s in range(args.scenes):
        db, mask = synth_scene(rng, args.scene_size, cfg)
        img_u8 = db_to_uint8(db, cfg)
        scene = f"synth_{args.split}_{s:03d}"

        oil, neg = [], []
        for r in range(0, args.scene_size - t + 1, stride):
            for c in range(0, args.scene_size - t + 1, stride):
                itile = img_u8[r:r + t, c:c + t]
                mtile = mask[r:r + t, c:c + t]
                frac = float(mtile.mean())
                if not filter_tiles:
                    writer.add(itile, mtile, scene, r, c, frac,
                               "oil" if frac > 0 else "background")
                    n_oil += frac > 0
                    n_neg += frac == 0
                elif frac >= cfg.tiling.min_oil_fraction:
                    oil.append((itile, mtile, r, c, frac))
                elif frac == 0.0:
                    neg.append((itile, mtile, r, c, frac))

        if filter_tiles:
            for itile, mtile, r, c, frac in oil:
                writer.add(itile, mtile, scene, r, c, frac, "oil")
            n_oil += len(oil)
            k = min(int(round(len(oil) * cfg.tiling.hard_negative_ratio)), len(neg))
            for i in rng.choice(len(neg), size=k, replace=False) if k else []:
                itile, mtile, r, c, frac = neg[int(i)]
                writer.add(itile, mtile, scene, r, c, frac, "hard_negative")
                n_neg += 1

    writer.finalise({
        "dataset": "SYNTHETIC", "part": None, "split": args.split,
        "source": "ml.synth", "config_fingerprint": cfg.fingerprint,
        "db_min": cfg.sar.db_min, "db_max": cfg.sar.db_max,
        "primary_band": cfg.sar.primary_band, "seed": args.seed,
        "filtered": filter_tiles, "scenes": args.scenes,
        "oil_tiles": int(n_oil), "negative_tiles": int(n_neg),
        "WARNING": "synthetic data -- pipeline smoke test only, not for metrics",
    })
    print(f"  {n_oil} oil + {n_neg} negative tiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
