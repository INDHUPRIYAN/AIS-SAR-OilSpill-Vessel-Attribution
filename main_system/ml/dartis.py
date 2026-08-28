"""DARTIS acquisition and YOLO dataset preparation -- Model 2, the screening stage.

DARTIS (Yang & Singha, DLR; PANGAEA doi:10.1594/PANGAEA.980773) is the
look-alike discrimination set: oil slicks AND the things that mimic them in
SAR. Model 2's whole job is rejecting those before the segmenter runs, which is
the question judges actually ask -- "how do you know that dark patch is oil?"

Two things make this simpler than it first appears:

  * PANGAEA's bulk `allfiles.zip` is behind a login (HTTP 401), but the
    individual files under `/dataset/980773/files/<name>` are open. 3,655 JPEGs
    at ~155 KB each is about 570 MB, and they are small enough to fetch in
    parallel.
  * The published data matrix already carries every object's bounding box in
    patch pixel coordinates, so the per-image XML annotations never need
    parsing. The matrix is the annotation source.

The `subset` column encodes both content and setting:
    ow = oil on open water     oc = oil near coast      -> annotated oil boxes
    nw = look-alike on water   nc = look-alike near coast -> NO annotations

That asymmetry is the whole design. Oil patches carry bounding boxes; look-alike
patches carry none (empty bbox columns, and their XML files return HTTP 500).
So this trains a ONE-class `oil` detector where the 2,290 look-alike patches are
background negatives -- images the model must look at and report nothing on.
That is how a detector learns not to fire on calm water and internal waves.

Usage:
    python -m ml.dartis matrix                 # fetch + cache the annotation table
    python -m ml.dartis images --workers 8     # fetch the JPEGs (resumable)
    python -m ml.dartis prepare                # build the YOLO dataset
    python -m ml.dartis status
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

import requests

from ml.config import DATA_ROOT

DOI = "10.1594/PANGAEA.980773"
MATRIX_URL = f"https://doi.pangaea.de/{DOI}?format=textfile"
FILE_URL = "https://download.pangaea.de/dataset/980773/files/{name}"
UA = {"User-Agent": "Mozilla/5.0 (research; OceanTrace SIH2026)"}

RAW = DATA_ROOT / "raw" / "dartis"
IMAGES = RAW / "images"
MATRIX = RAW / "data_matrix.tab"
YOLO = DATA_ROOT / "processed" / "dartis"

# Column indices in the published matrix (verified against the header).
C_SUBSET, C_JPG, C_TAG = 0, 1, 3
C_PW, C_PH = 8, 9
C_XMIN, C_YMIN, C_XMAX, C_YMAX = 26, 27, 28, 29

# ONE detection class. The dataset annotates oil objects with boxes; the
# look-alike patches (nw/nc) carry NO object annotations at all -- their XML
# files do not even exist (HTTP 500), and their matrix bbox fields are empty.
#
# So look-alikes are not a second class, they are BACKGROUND images: patches a
# detector must look at and report nothing on. That is precisely how a detector
# is taught not to fire on them, and it is the honest reading of the labels.
# Inventing a "lookalike" box class would mean fabricating annotations.
CLASS_NAMES = ["oil"]
OIL_SUBSETS = {"ow", "oc"}
NEGATIVE_SUBSETS = {"nw", "nc"}
SUBSET_LABEL = {"ow": "oil / open water", "oc": "oil / coast",
                "nw": "look-alike / water", "nc": "look-alike / coast"}


# --------------------------------------------------------------------------
# matrix
# --------------------------------------------------------------------------


def fetch_matrix(force: bool = False) -> Path:
    if MATRIX.exists() and not force:
        print(f"  matrix cached: {MATRIX} ({MATRIX.stat().st_size/1024:.0f} KB)")
        return MATRIX
    MATRIX.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {MATRIX_URL}")
    r = requests.get(MATRIX_URL, headers=UA, timeout=120)
    r.raise_for_status()
    MATRIX.write_text(r.text, encoding="utf-8")
    print(f"  -> {MATRIX} ({len(r.text)/1024:.0f} KB)")
    return MATRIX


def parse_matrix() -> List[dict]:
    """Return one record per annotated object."""
    if not MATRIX.exists():
        raise FileNotFoundError("Run `python -m ml.dartis matrix` first.")
    lines = MATRIX.read_text(encoding="utf-8").splitlines()
    end = next(i for i, l in enumerate(lines) if l.strip() == "*/")

    records = []
    for line in lines[end + 2:]:
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) <= C_YMAX:
            continue
        subset = f[C_SUBSET].strip()
        jpg = f[C_JPG].strip()
        if not jpg:
            continue
        rec = {"subset": subset, "jpg": jpg, "tag": f[C_TAG].strip(), "has_box": False}
        try:
            rec["w"] = int(float(f[C_PW]))
            rec["h"] = int(float(f[C_PH]))
        except (ValueError, IndexError):
            rec["w"] = rec["h"] = 640          # DARTIS patches are uniformly 640x640
        try:
            rec.update({
                "xmin": float(f[C_XMIN]), "ymin": float(f[C_YMIN]),
                "xmax": float(f[C_XMAX]), "ymax": float(f[C_YMAX]),
            })
            rec["has_box"] = True
        except (ValueError, IndexError):
            # Empty bbox columns: a look-alike / no-oil patch. Kept as a
            # negative rather than dropped -- discarding these would remove
            # every hard negative and leave a model that fires on calm water.
            pass
        records.append(rec)
    return records


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------


def fetch_one(name: str, session: requests.Session, retries: int = 3) -> tuple:
    dest = IMAGES / name
    if dest.exists() and dest.stat().st_size > 1024:
        return name, "cached", dest.stat().st_size
    for attempt in range(retries):
        try:
            r = session.get(FILE_URL.format(name=name), headers=UA, timeout=(20, 60))
            if r.status_code == 404:
                return name, "missing", 0
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(r.content)
            tmp.rename(dest)
            return name, "ok", len(r.content)
        except Exception:
            if attempt == retries - 1:
                return name, "failed", 0
            time.sleep(1.5 * (attempt + 1))
    return name, "failed", 0


def fetch_images(workers: int = 8, limit: int = 0) -> None:
    """Download every referenced JPEG. Resumable: existing files are skipped.

    Parallel because each file is ~155 KB -- at that size per-request latency
    dominates, so a single stream wastes most of the link. Kept modest so as
    not to starve a Trujillo download running alongside.
    """
    records = parse_matrix()
    names = sorted({r["jpg"] for r in records})
    if limit:
        names = names[:limit]
    IMAGES.mkdir(parents=True, exist_ok=True)

    have = sum(1 for n in names if (IMAGES / n).exists())
    print(f"  {len(names)} images referenced, {have} already on disk")
    todo = [n for n in names if not (IMAGES / n).exists()]
    if not todo:
        print("  nothing to fetch")
        return

    t0 = time.time()
    counts, total_bytes = Counter(), 0
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, n, session): n for n in todo}
        for i, fut in enumerate(as_completed(futures), start=1):
            name, status, nbytes = fut.result()
            counts[status] += 1
            total_bytes += nbytes
            if i % 25 == 0 or i == len(todo):
                dt = max(time.time() - t0, 1e-6)
                rate = total_bytes / dt / 1024**2
                eta = (len(todo) - i) / max(i / dt, 1e-6)
                print(f"\r  {i}/{len(todo)}  {total_bytes/1024**2:.0f} MB  "
                      f"{rate:.2f} MB/s  eta {eta/60:.1f} min  "
                      f"[ok {counts['ok']} cached {counts['cached']} "
                      f"failed {counts['failed']} missing {counts['missing']}]",
                      end="", flush=True)
    print()
    if counts["failed"]:
        print(f"  {counts['failed']} failed -- re-run to retry just those")


# --------------------------------------------------------------------------
# YOLO dataset
# --------------------------------------------------------------------------


def prepare_yolo(val_fraction: float = 0.2, seed: int = 1337) -> dict:
    """Build an Ultralytics-format dataset from the matrix + downloaded JPEGs.

    The split is stratified across the four subsets, so the val set keeps the
    same oil/look-alike and coast/water balance as train. A val split that
    happened to hold few look-alikes would report a false-positive rate that
    means nothing -- and false positives are the entire point of this model.
    """
    import shutil

    records = parse_matrix()
    by_image: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        if (IMAGES / r["jpg"]).exists():
            by_image[r["jpg"]].append(r)

    if not by_image:
        raise RuntimeError("No downloaded images found. Run `ml.dartis images` first.")

    # Stratify by subset, which is a property of the image (all objects in one
    # patch share it).
    groups: Dict[str, List[str]] = defaultdict(list)
    for jpg, objs in by_image.items():
        groups[objs[0]["subset"]].append(jpg)

    rng = random.Random(seed)
    train, val = [], []
    for subset, jpgs in groups.items():
        jpgs = sorted(jpgs)
        rng.shuffle(jpgs)
        cut = int(round(len(jpgs) * val_fraction))
        val.extend(jpgs[:cut])
        train.extend(jpgs[cut:])

    for split in ("train", "val"):
        for kind in ("images", "labels"):
            (YOLO / split / kind).mkdir(parents=True, exist_ok=True)

    stats = {"train": Counter(), "val": Counter()}
    for split, jpgs in (("train", train), ("val", val)):
        for jpg in jpgs:
            objs = by_image[jpg]
            shutil.copy(IMAGES / jpg, YOLO / split / "images" / jpg)

            lines = []
            for o in objs:
                if not o["has_box"] or o["subset"] not in OIL_SUBSETS:
                    continue                    # negative patch -> empty label file
                cls = 0
                w, h = max(o["w"], 1), max(o["h"], 1)
                # matrix gives corners in pixels -> YOLO cx,cy,w,h normalised
                x0, x1 = sorted((o["xmin"], o["xmax"]))
                y0, y1 = sorted((o["ymin"], o["ymax"]))
                cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
                bw, bh = (x1 - x0) / w, (y1 - y0) / h
                if bw <= 0 or bh <= 0:
                    continue
                cx, cy = min(max(cx, 0), 1), min(max(cy, 0), 1)
                bw, bh = min(bw, 1.0), min(bh, 1.0)
                lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                stats[split]["oil_objects"] += 1
            # An empty label file is valid YOLO: "this image contains nothing".
            # These are the look-alike patches, and they are the entire reason
            # this model exists -- so they are written, not skipped.
            (YOLO / split / "labels" / (Path(jpg).stem + ".txt")).write_text(
                "\n".join(lines), encoding="utf-8")
            stats[split]["images"] += 1
            stats[split]["positive_images" if lines else "negative_images"] += 1
            stats[split][f"subset_{objs[0]['subset']}"] += 1

    yaml_text = (
        f"# DARTIS look-alike screening dataset (auto-generated by ml.dartis)\n"
        f"# Source: PANGAEA doi:{DOI}\n"
        f"path: {YOLO.as_posix()}\n"
        f"train: train/images\n"
        f"val: val/images\n"
        f"nc: {len(CLASS_NAMES)}\n"
        f"names: {CLASS_NAMES}\n"
    )
    (YOLO / "dartis.yaml").write_text(yaml_text, encoding="utf-8")

    for split in ("train", "val"):
        s = stats[split]
        subs = " ".join(f"{k.replace('subset_','')}={s[k]}"
                        for k in sorted(s) if k.startswith("subset_"))
        print(f"  {split:<5s}: {s['images']:>5d} images  "
              f"({s['positive_images']} with oil, {s['negative_images']} background)  "
              f"{s['oil_objects']} oil boxes   [{subs}]")
    print(f"  -> {YOLO / 'dartis.yaml'}")
    return {"train": dict(stats["train"]), "val": dict(stats["val"]),
            "yaml": str(YOLO / "dartis.yaml")}


def status() -> None:
    print(f"matrix : {'present' if MATRIX.exists() else 'MISSING'}  {MATRIX}")
    if MATRIX.exists():
        recs = parse_matrix()
        names = {r['jpg'] for r in recs}
        have = sum(1 for n in names if (IMAGES / n).exists())
        size = sum((IMAGES / n).stat().st_size for n in names if (IMAGES / n).exists())
        print(f"objects: {len(recs)}   patches: {len(names)}")
        for k, v in Counter(r["subset"] for r in recs).most_common():
            pat = len({r['jpg'] for r in recs if r['subset'] == k})
            print(f"   {k:<3s} {SUBSET_LABEL.get(k,'?'):<22s} {v:>5d} objects  {pat:>5d} patches")
        print(f"images : {have}/{len(names)} downloaded ({size/1024**2:.0f} MB)")
    y = YOLO / "dartis.yaml"
    print(f"yolo   : {'ready ' + str(y) if y.exists() else 'not prepared'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("matrix").add_argument("--force", action="store_true")
    p_img = sub.add_parser("images")
    p_img.add_argument("--workers", type=int, default=8)
    p_img.add_argument("--limit", type=int, default=0)
    p_prep = sub.add_parser("prepare")
    p_prep.add_argument("--val-fraction", type=float, default=0.2)
    p_prep.add_argument("--seed", type=int, default=1337)
    sub.add_parser("status")
    args = ap.parse_args(argv)

    if args.cmd == "matrix":
        fetch_matrix(args.force)
    elif args.cmd == "images":
        fetch_images(args.workers, args.limit)
    elif args.cmd == "prepare":
        prepare_yolo(args.val_fraction, args.seed)
    else:
        status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
