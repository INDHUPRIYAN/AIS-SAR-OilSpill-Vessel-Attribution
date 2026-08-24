"""Evaluate a trained segmenter on the untouched Trujillo Part III test split.

Reports binary IoU, precision, recall and F1 -- and deliberately NOT pixel
accuracy. Sea dominates every tile, so a model that predicts "no oil"
everywhere scores above 99% pixel accuracy while being worthless. Any number
we put on a slide comes from here.

Also reports a false-positive rate on no-oil tiles, which is what actually
distinguishes a usable detector from one that paints slicks onto calm water.

Usage:
    python -m ml.evaluate --checkpoint data/runs/training/unet-r34/best.pt
    python -m ml.evaluate --checkpoint ... --sweep      # threshold sweep
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from tqdm import tqdm

from ml.config import REPO_ROOT, load_config
from ml.dataset import TILES_ROOT, TrujilloTiles
from ml.train_unet import build_model

DEFAULT_THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7]


def load_checkpoint(path: Path, device: torch.device):
    cfg = load_config()
    ckpt = torch.load(path, map_location=device, weights_only=False)
    stamped = ckpt.get("config_fingerprint")
    if stamped and stamped != cfg.fingerprint:
        raise RuntimeError(
            f"Checkpoint was trained under normalisation fingerprint {stamped}, "
            f"but config/normalisation.yaml is now {cfg.fingerprint}. Evaluating "
            f"across a mismatch produces numbers that do not describe the "
            f"deployed model.")
    model = build_model(ckpt.get("encoder", "resnet34"), weights=None).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, ckpt


@torch.no_grad()
def evaluate(model, dataset: TrujilloTiles, device, thresholds, batch_size=16,
             min_blob_frac: float = 0.001):
    """Accumulate counts globally and per tile-kind in one pass."""
    loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size,
                                         shuffle=False, num_workers=0)
    kinds = [dataset.records[j]["kind"] for j in dataset.indices]

    zero = lambda: {"tp": 0.0, "fp": 0.0, "fn": 0.0, "tn": 0.0, "tiles": 0}
    acc = {t: zero() for t in thresholds}
    per_kind = {t: {} for t in thresholds}
    # A no-oil tile "fires" if it produces a detection large enough to survive
    # the morphological cleanup the /detect service applies downstream. Counting
    # any single stray pixel would overstate the false-positive rate; requiring
    # a connected blob of at least min_blob_frac of the tile is what an operator
    # would actually see as a spurious slick.
    min_blob_px = max(1, int(min_blob_frac * dataset.meta["tile_size"] ** 2))
    fires = {t: {"n": 0, "hits": 0} for t in thresholds}

    cursor = 0
    for x, y in tqdm(loader, desc="  evaluating", unit="batch", disable=None):
        x = x.to(device, non_blocking=True)
        y = y.to(device, non_blocking=True)
        probs = torch.sigmoid(model(x)).float()
        batch_kinds = kinds[cursor:cursor + x.shape[0]]
        cursor += x.shape[0]

        for t in thresholds:
            pred = (probs > t).float()
            tp = (pred * y).sum(dim=(1, 2, 3))
            fp = (pred * (1 - y)).sum(dim=(1, 2, 3))
            fn = ((1 - pred) * y).sum(dim=(1, 2, 3))
            tn = ((1 - pred) * (1 - y)).sum(dim=(1, 2, 3))

            acc[t]["tp"] += float(tp.sum())
            acc[t]["fp"] += float(fp.sum())
            acc[t]["fn"] += float(fn.sum())
            acc[t]["tn"] += float(tn.sum())

            for i, kind in enumerate(batch_kinds):
                slot = per_kind[t].setdefault(kind, zero())
                slot["tp"] += float(tp[i]); slot["fp"] += float(fp[i])
                slot["fn"] += float(fn[i]); slot["tn"] += float(tn[i])
                slot["tiles"] += 1
                if float(y[i].sum()) == 0.0:
                    fires[t]["n"] += 1
                    fires[t]["hits"] += int(float(pred[i].sum()) >= min_blob_px)

    def scores(c: dict) -> dict:
        eps = 1e-9
        tp, fp, fn, tiles = c["tp"], c["fp"], c["fn"], c.get("tiles", 0)
        has_oil = (tp + fn) > 0
        if not has_oil:
            # No true oil pixels: IoU, precision and recall are all trivially
            # zero and say nothing. What matters here is how much clean water
            # the model painted as oil.
            px = tiles * dataset.meta["tile_size"] ** 2
            return {"has_true_oil": False, "tiles": tiles,
                    "false_positive_pixels": fp,
                    "false_positive_pixel_rate": fp / (px + eps)}
        p = tp / (tp + fp + eps)
        r = tp / (tp + fn + eps)
        return {
            "has_true_oil": True, "tiles": tiles,
            "iou": tp / (tp + fp + fn + eps),
            "precision": p, "recall": r,
            "f1": 2 * p * r / (p + r + eps),
            "dice": 2 * tp / (2 * tp + fp + fn + eps),
            "pixels_oil_true": tp + fn, "pixels_oil_pred": tp + fp,
        }

    return {
        str(t): {
            "overall": scores(acc[t]),
            "per_kind": {k: scores(v) for k, v in per_kind[t].items()},
            "no_oil_tiles": fires[t]["n"],
            "no_oil_tiles_with_false_detection": fires[t]["hits"],
            "scene_level_false_positive_rate": (
                fires[t]["hits"] / fires[t]["n"] if fires[t]["n"] else None),
        }
        for t in thresholds
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--tiles", type=Path, default=TILES_ROOT / "test")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--min-blob-frac", type=float, default=0.001,
                    help="Min detection size (fraction of tile area) that counts "
                         "as a false positive on a no-oil tile. Smaller blobs "
                         "would not survive morphological cleanup downstream.")
    ap.add_argument("--sweep", action="store_true",
                    help="Evaluate across several thresholds, not just 0.5.")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "data" / "runs" / "training" / "metrics.json")
    args = ap.parse_args(argv)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cfg = load_config()
    thresholds = DEFAULT_THRESHOLDS if args.sweep else [0.5]

    print(f"checkpoint  : {args.checkpoint}")
    print(f"test tiles  : {args.tiles}")
    print(f"device      : {device}")

    model, ckpt = load_checkpoint(args.checkpoint, device)
    ds = TrujilloTiles(args.tiles)
    print(f"dataset     : {ds.describe()}")
    if ds.meta.get("filtered"):
        print("WARNING: this test cache was built WITH oil filtering. False-positive "
              "rates from it understate reality -- re-prepare Part III unfiltered.")

    results = evaluate(model, ds, device, thresholds, args.batch_size,
                       args.min_blob_frac)

    print("\n" + "=" * 64)
    print("Trujillo Part III -- test metrics (pixel accuracy deliberately omitted)")
    print("=" * 64)
    for t in thresholds:
        r = results[str(t)]
        o = r["overall"]
        print(f"\nthreshold {t}")
        print(f"  IoU {o['iou']:.4f}   precision {o['precision']:.4f}   "
              f"recall {o['recall']:.4f}   F1 {o['f1']:.4f}")
        if r["scene_level_false_positive_rate"] is not None:
            print(f"  no-oil tiles with a detection >= {args.min_blob_frac * 100:.2f}%"
                  f" of tile area: {r['no_oil_tiles_with_false_detection']}"
                  f"/{r['no_oil_tiles']} "
                  f"({r['scene_level_false_positive_rate'] * 100:.1f}%)")
        for kind, s in sorted(r["per_kind"].items()):
            if s["has_true_oil"]:
                print(f"    {kind:<16s} ({s['tiles']:>4d} tiles) IoU {s['iou']:.4f}  "
                      f"P {s['precision']:.4f}  R {s['recall']:.4f}")
            else:
                print(f"    {kind:<16s} ({s['tiles']:>4d} tiles) no true oil -- "
                      f"false-positive pixels {int(s['false_positive_pixels'])} "
                      f"({s['false_positive_pixel_rate'] * 100:.4f}% of area)")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checkpoint": str(args.checkpoint),
        "checkpoint_epoch": ckpt.get("epoch"),
        "encoder": ckpt.get("encoder"),
        "config_fingerprint": cfg.fingerprint,
        "db_range": [cfg.sar.db_min, cfg.sar.db_max],
        "tile_size": cfg.tiling.tile_size,
        "test_split": str(args.tiles),
        "test_tiles": len(ds),
        "min_blob_frac": args.min_blob_frac,
        "results": results,
        "note": "Pixel accuracy is intentionally not reported: sea-class "
                "dominance makes it meaningless.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nmetrics -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
