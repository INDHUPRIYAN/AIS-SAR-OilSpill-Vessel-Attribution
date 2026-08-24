"""Torch dataset over the prepared uint8 tile cache.

Reads the memory-mapped tiles written by `prepare_trujillo.py`. Tiles stay on
disk and are paged in per batch, so a multi-GB cache trains fine inside 6 GB
of VRAM and modest RAM.

The train/val split is BY SCENE. Tiles cut from one 2048px scene overlap in
content; splitting at tile level puts near-duplicates on both sides and
inflates val IoU by several points. `index.json` carries the scene id per
tile precisely so this stays honest.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

from ml.config import DATA_ROOT, load_config, uint8_to_model

TILES_ROOT = DATA_ROOT / "processed" / "trujillo"


def load_split_index(split_dir: Path) -> dict:
    idx = split_dir / "index.json"
    if not idx.exists():
        raise FileNotFoundError(
            f"No prepared tiles at {split_dir}. Run:\n"
            f"  python -m ml.prepare_trujillo --part 3        (test)\n"
            f"  python -m ml.prepare_trujillo --part 1 --discard  (train/val)")
    return json.loads(idx.read_text(encoding="utf-8"))


def scene_split(records: list[dict], val_fraction: float = 0.2,
                seed: int = 1337) -> tuple[list[int], list[int]]:
    """Partition tile indices by scene, not by tile."""
    scenes = sorted({r["scene"] for r in records})
    rng = random.Random(seed)
    rng.shuffle(scenes)
    n_val = max(1, int(round(len(scenes) * val_fraction))) if scenes else 0
    val_scenes = set(scenes[:n_val])
    train_idx = [r["i"] for r in records if r["scene"] not in val_scenes]
    val_idx = [r["i"] for r in records if r["scene"] in val_scenes]
    return train_idx, val_idx


def build_augmentations(strength: str = "default"):
    """Geometric flips/rotations plus mild radiometric jitter.

    SAR has no canonical orientation, so D4 symmetry is free signal. The
    brightness/contrast jitter is deliberately small: it stands in for
    radiometric variation between scenes (the domain gap that bites at
    inference), but pushed hard it would start inventing slicks.
    """
    import albumentations as A

    if strength == "none":
        return None
    aug = [A.HorizontalFlip(p=0.5), A.VerticalFlip(p=0.5), A.RandomRotate90(p=0.5)]
    if strength == "default":
        aug += [
            A.Affine(translate_percent=(-0.05, 0.05), scale=(0.9, 1.1),
                     rotate=(-15, 15), border_mode=0, fill=0, fill_mask=0, p=0.3),
            A.RandomBrightnessContrast(brightness_limit=0.10,
                                       contrast_limit=0.10, p=0.3),
        ]
    return A.Compose(aug)


class TrujilloTiles(Dataset):
    """One split of the prepared tile cache.

    Args:
        split_dir: directory holding images.npy / masks.npy / index.json
        indices:   subset of tile indices (from `scene_split`); None = all
        augment:   albumentations pipeline, or None
    """

    def __init__(self, split_dir: Path, indices=None, augment=None):
        self.split_dir = Path(split_dir)
        payload = load_split_index(self.split_dir)
        self.meta = payload["meta"]
        self.records = payload["tiles"]
        self.indices = list(range(len(self.records))) if indices is None else list(indices)
        self.augment = augment

        cfg = load_config()
        stamped = self.meta.get("config_fingerprint")
        if stamped and stamped != cfg.fingerprint:
            raise RuntimeError(
                f"Tile cache at {split_dir} was built with normalisation "
                f"fingerprint {stamped}, but config/normalisation.yaml is now "
                f"{cfg.fingerprint}. Re-run ml.prepare_trujillo -- training on "
                f"mismatched constants silently degrades real-scene performance.")

        # mmap_mode='r' keeps tiles on disk; the OS page cache does the work.
        self.images = np.load(self.split_dir / "images.npy", mmap_mode="r")
        self.masks = np.load(self.split_dir / "masks.npy", mmap_mode="r")

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, i: int):
        j = self.indices[i]
        img = np.asarray(self.images[j])            # uint8 (H, W)
        mask = np.asarray(self.masks[j])            # uint8 (H, W) in {0,1}

        if self.augment is not None:
            out = self.augment(image=img, mask=mask)
            img, mask = out["image"], out["mask"]

        x = torch.from_numpy(uint8_to_model(img)).unsqueeze(0)          # (1,H,W)
        y = torch.from_numpy(mask.astype(np.float32)).unsqueeze(0)      # (1,H,W)
        return x, y

    def oil_fraction(self) -> float:
        vals = [self.records[j]["oil_fraction"] for j in self.indices]
        return float(np.mean(vals)) if vals else 0.0

    def describe(self) -> str:
        kinds: dict[str, int] = {}
        scenes = set()
        for j in self.indices:
            r = self.records[j]
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            scenes.add(r["scene"])
        return (f"{len(self.indices)} tiles from {len(scenes)} scenes, "
                f"kinds={kinds}, mean oil {self.oil_fraction() * 100:.2f}%")


def build_dataloaders(split_dir: Path | None = None, batch_size: int = 12,
                      val_fraction: float = 0.2, seed: int = 1337,
                      num_workers: int = 2, augment: str = "default"):
    """Train/val loaders over the trainval cache, split by scene."""
    split_dir = Path(split_dir or (TILES_ROOT / "trainval"))
    payload = load_split_index(split_dir)
    train_idx, val_idx = scene_split(payload["tiles"], val_fraction, seed)

    train_ds = TrujilloTiles(split_dir, train_idx, build_augmentations(augment))
    val_ds = TrujilloTiles(split_dir, val_idx, None)

    common = dict(num_workers=num_workers, pin_memory=torch.cuda.is_available(),
                  persistent_workers=num_workers > 0)
    train_dl = torch.utils.data.DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, drop_last=True, **common)
    val_dl = torch.utils.data.DataLoader(
        val_ds, batch_size=batch_size, shuffle=False, drop_last=False, **common)
    return train_dl, val_dl, train_ds, val_ds


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else TILES_ROOT / "trainval"
    ds = TrujilloTiles(target)
    print(f"split dir : {target}")
    print(f"meta      : {json.dumps(ds.meta, indent=2)}")
    print(f"all tiles : {ds.describe()}")
    tr, va = scene_split(ds.records)
    print(f"train     : {len(tr)} tiles")
    print(f"val       : {len(va)} tiles")
    x, y = ds[0]
    print(f"sample    : x{tuple(x.shape)} {x.dtype} [{x.min():.3f},{x.max():.3f}]  "
          f"y{tuple(y.shape)} oil={float(y.mean()) * 100:.2f}%")
