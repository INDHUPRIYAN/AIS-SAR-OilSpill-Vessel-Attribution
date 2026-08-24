"""Dataset audit -- step 2 of the training workflow.

Handbook: "Open 20 random image/mask pairs from each set; confirm mask
alignment, dB value ranges, look-alike categories; record counts in
docs/data_card.md."

This script assumes as little as possible about on-disk layout, because the
layout is exactly what we are here to discover. It reports what it finds and
writes a contact sheet of sampled pairs so mask alignment can be checked by
eye -- a misaligned mask trains a model that scores well and is useless.

Usage:
    python -m ml.audit --dataset trujillo --part 3
    python -m ml.audit --dataset trujillo --part 3 --samples 20
    python -m ml.audit --dataset dartis
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from ml.config import DATA_ROOT, REPO_ROOT, load_config

RASTER_EXT = {".tif", ".tiff", ".TIF", ".TIFF"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

# Filename fragments that mark a file as the label rather than the image.
MASK_HINTS = ("mask", "label", "gt", "ground_truth", "target", "annot", "_m.")


def looks_like_mask(path: Path) -> bool:
    return any(h in path.name.lower() for h in MASK_HINTS)


def read_raster(path: Path):
    """Return (array, meta). Prefers rasterio (keeps georeferencing) and
    falls back to tifffile for plain TIFFs rasterio refuses."""
    try:
        import rasterio
        with rasterio.open(path) as src:
            arr = src.read()  # (bands, H, W)
            return arr, {
                "driver": src.driver, "crs": str(src.crs), "bands": src.count,
                "dtype": str(src.dtypes[0]), "shape": (src.height, src.width),
                "nodata": src.nodata,
                "transform": [round(v, 8) for v in src.transform[:6]],
            }
    except Exception:
        import tifffile
        arr = tifffile.imread(path)
        if arr.ndim == 2:
            arr = arr[None, ...]
        elif arr.ndim == 3 and arr.shape[-1] <= 4:  # (H,W,C) -> (C,H,W)
            arr = np.moveaxis(arr, -1, 0)
        return arr, {
            "driver": "tifffile", "crs": None, "bands": arr.shape[0],
            "dtype": str(arr.dtype), "shape": arr.shape[-2:], "nodata": None,
            "transform": None,
        }


def band_stats(band: np.ndarray) -> dict:
    finite = band[np.isfinite(band)]
    if finite.size == 0:
        return {"empty": True}
    unique_sample = np.unique(finite[:100000])
    return {
        "min": float(finite.min()), "max": float(finite.max()),
        "mean": float(finite.mean()), "std": float(finite.std()),
        "p1": float(np.percentile(finite, 1)),
        "p99": float(np.percentile(finite, 99)),
        "n_unique_sample": int(unique_sample.size),
        "binary_like": bool(unique_sample.size <= 3),
    }


MASK_DIRS = {"mask", "masks", "label", "labels", "gt", "ground_truth",
             "groundtruth", "annotation", "annotations"}


def in_mask_dir(path: Path) -> bool:
    """True if any directory on the path marks this as label data.

    `ml.extract` unpacks the separate image/mask archives into sibling
    `images/` and `masks/` trees, but the internal nesting inside each archive
    is unknown, so the check walks the whole relative path rather than
    assuming a fixed depth.
    """
    return any(part.lower() in MASK_DIRS for part in path.parts)


def normalise_stem(p: Path) -> str:
    """Strip mask-ish decorations so `scene_042.tif` and `scene_042_mask.tif`
    collapse to the same key."""
    s = p.stem.lower()
    for h in MASK_HINTS:
        s = s.replace(h.strip("_."), "")
    return s.strip("_-. ")


def pair_files(files: list[Path]) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Match images to masks, tolerating either on-disk layout.

    Trujillo ships images and masks as separate archives, so a mask is
    identified either by its filename or by living under a mask directory.
    Matching is by normalised stem, which survives arbitrary nesting; a
    same-relative-path lookup is tried as a fallback for datasets that mirror
    their directory structure exactly.
    """
    masks = [f for f in files if in_mask_dir(f) or looks_like_mask(f)]
    images = [f for f in files if f not in set(masks)]

    mask_by_stem: dict[str, Path] = {}
    for m in masks:
        mask_by_stem.setdefault(normalise_stem(m), m)

    # Relative-path index, keyed below the images/ or masks/ root.
    def below_root(p: Path) -> str:
        parts = [x for x in p.parts]
        for i, part in enumerate(parts):
            if part.lower() in MASK_DIRS or part.lower() == "images":
                return "/".join(parts[i + 1:]).lower()
        return p.name.lower()

    mask_by_relpath = {below_root(m): m for m in masks}

    pairs, unpaired = [], []
    for img in images:
        m = (mask_by_stem.get(normalise_stem(img))
             or mask_by_relpath.get(below_root(img)))
        (pairs.append((img, m)) if m else unpaired.append(img))
    return pairs, unpaired


def contact_sheet(pairs, out_path: Path, cfg, n: int = 20) -> None:
    """Render sampled image/mask pairs side by side with the mask overlaid.
    Alignment errors are obvious here and invisible in any metric."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sample = pairs[:n]
    rows = len(sample)
    if rows == 0:
        return
    fig, axes = plt.subplots(rows, 3, figsize=(10, 3.2 * rows), squeeze=False)
    for i, (img_p, mask_p) in enumerate(sample):
        img, _ = read_raster(img_p)
        band = img[cfg.sar.primary_band - 1].astype(np.float32)
        disp = np.clip(band, cfg.sar.db_min, cfg.sar.db_max)

        axes[i][0].imshow(disp, cmap="gray")
        axes[i][0].set_title(f"{img_p.name[:34]}\nband {cfg.sar.primary_band}", fontsize=7)

        if mask_p is not None:
            mask, _ = read_raster(mask_p)
            mk = mask[0]
            axes[i][1].imshow(mk, cmap="viridis")
            axes[i][1].set_title(f"mask  oil={float((mk > 0).mean()) * 100:.2f}%", fontsize=7)
            axes[i][2].imshow(disp, cmap="gray")
            axes[i][2].imshow(np.ma.masked_where(mk == 0, mk), cmap="autumn", alpha=0.5)
            axes[i][2].set_title("overlay (check alignment)", fontsize=7)
        for ax in axes[i]:
            ax.axis("off")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=90)
    plt.close(fig)
    print(f"  contact sheet -> {out_path}")


def audit_trujillo(part: int, samples: int, seed: int = 1337) -> dict:
    cfg = load_config()
    root = DATA_ROOT / "raw" / "trujillo" / f"part{part}"
    print(f"\n=== Trujillo Part {part} ===\n  root: {root}")
    if not root.exists():
        print("  NOT DOWNLOADED. Run: python -m ml.download --dataset trujillo "
              f"--part {part}")
        return {"part": part, "present": False}

    files = sorted(p for p in root.rglob("*") if p.suffix in RASTER_EXT)
    print(f"  {len(files)} raster file(s)")
    if not files:
        others = Counter(p.suffix for p in root.rglob("*") if p.is_file())
        print(f"  no rasters found. Extensions present: {dict(others)}")
        print("  (archives may still need unpacking)")
        return {"part": part, "present": True, "rasters": 0,
                "extensions": {k: v for k, v in others.items()}}

    pairs, unpaired = pair_files(files)
    print(f"  paired: {len(pairs)}   unpaired: {len(unpaired)}")

    rng = random.Random(seed)
    sample = rng.sample(pairs, min(samples, len(pairs))) if pairs else []

    per_band, mask_frac, shapes, dtypes, crs_set = {}, [], Counter(), Counter(), Counter()
    for img_p, mask_p in sample:
        img, meta = read_raster(img_p)
        shapes[str(meta["shape"])] += 1
        dtypes[meta["dtype"]] += 1
        crs_set[str(meta["crs"])] += 1
        for b in range(img.shape[0]):
            per_band.setdefault(b + 1, []).append(band_stats(img[b].astype(np.float32)))
        if mask_p is not None:
            mk, _ = read_raster(mask_p)
            mask_frac.append(float((mk[0] > 0).mean()))

    print(f"\n  shapes : {dict(shapes)}")
    print(f"  dtypes : {dict(dtypes)}")
    print(f"  crs    : {dict(crs_set)}")
    print(f"\n  band statistics over {len(sample)} sampled scenes:")
    band_summary = {}
    for b, stats in sorted(per_band.items()):
        valid = [s for s in stats if not s.get("empty")]
        if not valid:
            continue
        lo = float(np.mean([s["p1"] for s in valid]))
        hi = float(np.mean([s["p99"] for s in valid]))
        mn = float(np.mean([s["mean"] for s in valid]))
        binary = sum(s["binary_like"] for s in valid) == len(valid)
        band_summary[b] = {"p1": lo, "p99": hi, "mean": mn, "binary_like": binary}
        tag = "  <-- looks like a MASK, not backscatter" if binary else ""
        print(f"    band {b}: p1={lo:8.2f}  p99={hi:8.2f}  mean={mn:8.2f}{tag}")

    cfg_lo, cfg_hi = cfg.sar.db_min, cfg.sar.db_max
    prim = band_summary.get(cfg.sar.primary_band)
    if prim and not prim["binary_like"]:
        if prim["p1"] < cfg_lo - 10 or prim["p99"] > cfg_hi + 10:
            print(f"\n  WARNING: band {cfg.sar.primary_band} spans "
                  f"[{prim['p1']:.1f}, {prim['p99']:.1f}] dB but normalisation.yaml "
                  f"clips to [{cfg_lo}, {cfg_hi}]. Reconcile BEFORE preparing tiles.")
        else:
            print(f"\n  band {cfg.sar.primary_band} sits inside the configured "
                  f"[{cfg_lo}, {cfg_hi}] dB clip range -- consistent.")

    if mask_frac:
        arr = np.array(mask_frac)
        print(f"\n  oil coverage: mean {arr.mean() * 100:.2f}%  "
              f"median {np.median(arr) * 100:.2f}%  max {arr.max() * 100:.2f}%")
        print(f"  scenes with >=1% oil: {int((arr >= 0.01).sum())}/{len(arr)}")

    if sample:
        contact_sheet(sample, REPO_ROOT / "docs" / "audit" /
                      f"trujillo_part{part}_pairs.png", cfg, samples)

    return {
        "part": part, "present": True, "rasters": len(files),
        "paired": len(pairs), "unpaired": len(unpaired),
        "shapes": dict(shapes), "dtypes": dict(dtypes), "crs": dict(crs_set),
        "bands": band_summary,
        "oil_fraction_mean": float(np.mean(mask_frac)) if mask_frac else None,
        "sampled": len(sample), "config_fingerprint": cfg.fingerprint,
    }


def audit_dartis(samples: int) -> dict:
    root = DATA_ROOT / "raw" / "dartis"
    print(f"\n=== DARTIS ===\n  root: {root}")
    if not root.exists() or not any(root.rglob("*")):
        print("  NOT DOWNLOADED. Run: python -m ml.download --dataset dartis")
        return {"present": False}

    images = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXT)
    labels = sorted(p for p in root.rglob("*")
                    if p.suffix.lower() in {".txt", ".xml", ".json", ".csv"})
    print(f"  {len(images)} image(s), {len(labels)} annotation file(s)")

    # Look-alike phenomena are usually encoded in the directory name.
    by_dir = Counter(p.parent.name for p in images)
    print("\n  images per directory (phenomenon categories live here):")
    for d, n in by_dir.most_common(30):
        print(f"    {d[:44]:<44s} {n:>6d}")

    sizes = Counter()
    for p in images[:samples]:
        try:
            from PIL import Image
            with Image.open(p) as im:
                sizes[f"{im.width}x{im.height}"] += 1
        except Exception:
            pass
    print(f"\n  sampled image sizes: {dict(sizes)}")
    return {"present": True, "images": len(images), "annotations": len(labels),
            "by_directory": dict(by_dir), "sizes": dict(sizes)}


def write_data_card(results: dict) -> None:
    out = REPO_ROOT / "docs" / "data_card.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Data Card -- OceanTrace detection datasets",
        "",
        "Generated by `python -m ml.audit`. Regenerate after any change to",
        "`config/normalisation.yaml` or to the raw data on disk.",
        "",
        "```json",
        json.dumps(results, indent=2, default=str),
        "```",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\ndata card -> {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=["trujillo", "dartis", "all"], default="all")
    ap.add_argument("--part", type=int, action="append", choices=[1, 2, 3])
    ap.add_argument("--samples", type=int, default=20,
                    help="Pairs to open and render (handbook says 20).")
    ap.add_argument("--no-card", action="store_true", help="Skip writing data_card.md")
    args = ap.parse_args(argv)

    results = {}
    if args.dataset in ("trujillo", "all"):
        results["trujillo"] = [audit_trujillo(p, args.samples)
                               for p in sorted(set(args.part or [3, 1, 2]))]
    if args.dataset in ("dartis", "all"):
        results["dartis"] = audit_dartis(args.samples)

    if not args.no_card:
        write_data_card(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
