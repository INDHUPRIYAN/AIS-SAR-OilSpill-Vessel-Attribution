"""Build a small, human-inspectable sample of the DARTIS training set.

Metrics tell you how a model scores; they do not tell you what it was shown.
This writes a handful of real training images with their labels drawn on, so a
person can look at the data and confirm it means what the pipeline claims --
particularly that look-alike patches really do carry no boxes.

Usage:
    python -m ml.make_sample                 # 10 images, balanced
    python -m ml.make_sample --n 20 --split val
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from ml.config import REPO_ROOT
from ml.dartis import SUBSET_LABEL, YOLO as DARTIS_YOLO, parse_matrix

OUT = REPO_ROOT / "1_indhu_main_system" / "ml" / "dataset_sample"

BOX_COLOR = (255, 59, 48)
TEXT_BG = (255, 59, 48)


def annotate(src: Path, label_file: Path, dest: Path, subset: str) -> int:
    """Copy `src` to `dest` with its YOLO boxes drawn. Returns box count."""
    from PIL import Image, ImageDraw

    img = Image.open(src).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    boxes = []
    if label_file.exists():
        for line in label_file.read_text().strip().splitlines():
            if not line.strip():
                continue
            _cls, cx, cy, bw, bh = (float(v) for v in line.split())
            boxes.append((( cx - bw / 2) * w, (cy - bh / 2) * h,
                          (cx + bw / 2) * w, (cy + bh / 2) * h))

    for (x0, y0, x1, y1) in boxes:
        draw.rectangle([x0, y0, x1, y1], outline=BOX_COLOR, width=3)
        tag = "oil"
        tw = 6 * len(tag) + 8
        draw.rectangle([x0, max(y0 - 16, 0), x0 + tw, max(y0, 16)], fill=TEXT_BG)
        draw.text((x0 + 4, max(y0 - 15, 1)), tag, fill=(255, 255, 255))

    banner = (f"{subset}  {SUBSET_LABEL.get(subset, '?')}   "
              f"{'NO BOXES - background negative' if not boxes else f'{len(boxes)} oil box(es)'}")
    draw.rectangle([0, h - 20, w, h], fill=(0, 0, 0))
    draw.text((6, h - 15), banner, fill=(255, 255, 255))

    img.save(dest)
    return len(boxes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--split", choices=["train", "val"], default="train")
    args = ap.parse_args(argv)

    split_dir = DARTIS_YOLO / args.split
    if not (split_dir / "images").exists():
        raise SystemExit(f"No prepared dataset at {split_dir}. Run `ml.dartis prepare`.")

    subset_of = {r["jpg"]: r["subset"] for r in parse_matrix()}
    available = defaultdict(list)
    for img in sorted((split_dir / "images").glob("*.jpg")):
        available[subset_of.get(img.name, "??")].append(img)

    # Balanced across all four subsets, so the sample shows both what the model
    # must find (ow/oc) and what it must ignore (nw/nc).
    order = ["ow", "oc", "nw", "nc"]
    picks = []
    i = 0
    while len(picks) < args.n and any(available[s] for s in order):
        s = order[i % len(order)]
        if available[s]:
            picks.append((s, available[s].pop(len(available[s]) // 2)))
        i += 1

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "annotated").mkdir(parents=True)
    (OUT / "original").mkdir(parents=True)
    (OUT / "labels").mkdir(parents=True)

    rows = []
    for subset, img in picks:
        lbl = split_dir / "labels" / (img.stem + ".txt")
        shutil.copy(img, OUT / "original" / img.name)
        shutil.copy(lbl, OUT / "labels" / lbl.name)
        n = annotate(img, lbl, OUT / "annotated" / img.name, subset)
        rows.append((img.name, subset, SUBSET_LABEL.get(subset, "?"), n,
                     lbl.stat().st_size))

    lines = [
        "# DARTIS training-set sample",
        "",
        f"{len(rows)} real images from the `{args.split}` split the screening "
        f"detector is training on right now.",
        "",
        "- `annotated/` — the same images with their labels drawn on (**look here**)",
        "- `original/`  — untouched 640x640 patches, exactly as the model sees them",
        "- `labels/`    — the YOLO label files, byte for byte",
        "",
        "## The thing to notice",
        "",
        "Oil patches (`ow`, `oc`) carry boxes. Look-alike patches (`nw`, `nc`) carry",
        "**an empty label file** — 0 bytes. That is not missing data; it is the label.",
        "They are background negatives: images the detector must look at and report",
        "nothing on. Roughly 63% of the training set is exactly this, which is how the",
        "model learns not to fire on calm water and internal waves.",
        "",
        "| file | subset | meaning | boxes | label size |",
        "|---|---|---|---|---|",
    ]
    for name, subset, meaning, n, size in rows:
        lines.append(f"| `{name}` | `{subset}` | {meaning} | "
                     f"{n if n else '**0 — background**'} | {size} B |")
    lines += [
        "",
        "## YOLO label format",
        "",
        "`class cx cy w h` — one line per object, all values normalised to [0,1],",
        "class `0` = `oil` (the only class).",
        "",
        "```",
    ]
    sample_lbl = next((OUT / "labels").glob("*.txt"))
    for f in sorted((OUT / "labels").glob("*.txt")):
        body = f.read_text().strip()
        if body:
            lines.append(f"{f.name}:")
            lines += ["  " + l for l in body.splitlines()[:3]]
            break
    empty = [f.name for f in sorted((OUT / "labels").glob("*.txt"))
             if not f.read_text().strip()]
    if empty:
        lines.append(f"{empty[0]}:")
        lines.append("  (empty file - no oil in this patch)")
    lines.append("```")

    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"sample -> {OUT}")
    for name, subset, meaning, n, size in rows:
        flag = f"{n} box(es)" if n else "EMPTY label (background negative)"
        print(f"  {subset}  {name:<28s} {meaning:<22s} {flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
