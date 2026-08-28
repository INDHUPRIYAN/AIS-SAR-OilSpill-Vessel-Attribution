"""Unpack the downloaded Trujillo .7z archives -- the step between download and audit.

Trujillo ships images and masks as SEPARATE archives (e.g.
`01_Train_Val_Oil_Spill_images.7z` + `01_Train_Val_Oil_Spill_mask.7z`), and the
image archives are tens of GB while the mask archives are a few hundred KB. So:

  * masks are extracted into a sibling `masks/` tree and images into `images/`,
    which is the layout `ml.audit.pair_files` already understands;
  * `--discard-archive` deletes each .7z as soon as it has been unpacked, because
    keeping a 38 GB archive next to its 40 GB expansion is what fills the disk.

Peak disk is the archive plus its expansion, so check headroom before starting:
Part 1 needs roughly 90 GB free, Part 3 about 20 GB.

Usage:
    python -m ml.extract --part 3
    python -m ml.extract --part 3 --list          # inspect archive contents only
    python -m ml.extract --part 1 --discard-archive
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ml.config import DATA_ROOT

MASK_ARCHIVE_HINTS = ("mask", "ground_truth", "groundtruth", "_gt", "label")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def free_bytes(path: Path) -> int:
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


IMAGE_TREE_HINTS = ("image", "img", "scene")


def is_self_contained(archive: Path) -> bool:
    """True if the archive already holds BOTH an image tree and a mask tree.

    Trujillo Part III ships as one archive containing `Images/{Oil,Lookalike,
    No oil}` alongside `Mask/{Oil,Lookalike,No oil}`. Classifying it by
    filename would be actively wrong -- it is called
    `02_Test_images_and_ground_truth`, which matches the mask hint, so every
    one of its 900 files would land in `masks/` and nothing would pair.

    When both trees are present the internal layout is already what the pairing
    logic expects, so it is extracted verbatim.
    """
    try:
        import py7zr

        with py7zr.SevenZipFile(archive, mode="r") as z:
            tops = {n.split("/")[0].lower() for n in z.getnames() if "/" in n}
    except Exception:
        return False
    has_masks = any(any(h in t for h in MASK_ARCHIVE_HINTS) for t in tops)
    has_images = any(any(h in t for h in IMAGE_TREE_HINTS) for t in tops)
    return has_masks and has_images


def classify(archive: Path) -> str:
    """images vs masks, from the archive name.

    Only consulted for archives that hold one or the other. A 'ground truth'
    archive holds masks; one mentioning neither is assumed to be imagery, the
    safe default -- a mistake there surfaces immediately in the audit, whereas
    mislabelling masks as images would silently produce unpaired data.
    """
    name = archive.name.lower()
    return "masks" if any(h in name for h in MASK_ARCHIVE_HINTS) else "images"


def list_archive(archive: Path, limit: int = 25) -> None:
    import py7zr

    with py7zr.SevenZipFile(archive, mode="r") as z:
        names = z.getnames()
    print(f"  {archive.name}: {len(names)} entries")
    for n in names[:limit]:
        print(f"    {n}")
    if len(names) > limit:
        print(f"    ... {len(names) - limit} more")


def extract_archive(archive: Path, dest: Path, discard: bool = False) -> Path:
    import py7zr

    dest.mkdir(parents=True, exist_ok=True)
    size = archive.stat().st_size
    free = free_bytes(dest)
    print(f"  {archive.name} ({human(size)}) -> {dest}")
    # 7z on 2048x2048 float32 TIFFs compresses poorly, so expansion is roughly
    # 1:1. Require 1.2x as a margin rather than discovering the shortfall at 90%.
    if free < size * 1.2:
        raise IOError(
            f"not enough disk: {human(free)} free, need about {human(size * 1.2)} "
            f"to expand {archive.name}. Free space or use --discard-archive on "
            f"the previous part first.")

    with py7zr.SevenZipFile(archive, mode="r") as z:
        z.extractall(path=dest)

    produced = sum(1 for p in dest.rglob("*") if p.is_file())
    print(f"    extracted, {produced} file(s) now under {dest.name}/")
    if discard:
        archive.unlink()
        print(f"    removed archive {archive.name} ({human(size)} freed)")
    return dest


def extract_part(part: int, discard: bool, list_only: bool) -> int:
    src = DATA_ROOT / "raw" / "trujillo" / f"part{part}"
    print(f"\n=== Extracting Trujillo Part {part} ===\n  source: {src}")
    if not src.exists():
        print(f"  NOT DOWNLOADED. Run: python -m ml.download --dataset trujillo "
              f"--part {part}")
        return 1

    archives = sorted(src.glob("*.7z"))
    if not archives:
        already = [p for p in src.rglob("*") if p.suffix.lower() in {".tif", ".tiff"}]
        if already:
            print(f"  no .7z archives, but {len(already)} raster(s) already present "
                  f"-- nothing to do")
            return 0
        print(f"  no .7z archives found in {src}")
        return 1

    print(f"  {len(archives)} archive(s), {human(sum(a.stat().st_size for a in archives))} total")
    print(f"  free disk: {human(free_bytes(src))}")

    if list_only:
        for a in archives:
            list_archive(a)
        return 0

    # Masks first: they are tiny, and if their layout is surprising we find out
    # in seconds rather than after unpacking 38 GB of imagery.
    for archive in sorted(archives, key=lambda a: (classify(a) != "masks", a.name)):
        if is_self_contained(archive):
            print(f"  {archive.name}: contains both image and mask trees, "
                  f"extracting verbatim")
            dest = src
        else:
            dest = src / classify(archive)
        try:
            extract_archive(archive, dest, discard)
        except Exception as exc:
            print(f"    FAILED: {exc}")
            return 2

    print(f"\n  done. Next: python -m ml.audit --dataset trujillo --part {part}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", type=int, required=True, choices=[1, 2, 3])
    ap.add_argument("--discard-archive", action="store_true",
                    help="Delete each .7z once unpacked (needed for parts 1-2).")
    ap.add_argument("--list", dest="list_only", action="store_true",
                    help="Show archive contents without extracting.")
    args = ap.parse_args(argv)
    return extract_part(args.part, args.discard_archive, args.list_only)


if __name__ == "__main__":
    sys.exit(main())
