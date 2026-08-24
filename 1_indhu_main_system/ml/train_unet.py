"""Train the Trujillo segmentation model -- step 5 of the training workflow.

U-Net with a ResNet-34 ImageNet encoder (segmentation-models-pytorch),
Dice+BCE, AMP fp16, batch 12-16 at 256px, ~40 epochs, checkpoint every epoch.

Every checkpoint carries the normalisation fingerprint it was trained under.
`evaluate.py` and `export.py` refuse to run on a mismatch, which is the one
guard against the classic SAR domain-gap failure: training on one dB clip
range and inferring with another looks fine in metrics and fails on real scenes.

Usage:
    python -m ml.train_unet                          # defaults, 40 epochs
    python -m ml.train_unet --epochs 40 --batch-size 12
    python -m ml.train_unet --resume runs/unet-r34/last.pt
    python -m ml.train_unet --smoke                  # 2 epochs on a tiny subset
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from tqdm import tqdm

from ml.config import REPO_ROOT, load_config
from ml.dataset import TILES_ROOT, build_dataloaders

RUNS_ROOT = REPO_ROOT / "data" / "runs" / "training"


class DiceBCELoss(nn.Module):
    """Dice handles the class imbalance (sea dominates every tile); BCE keeps
    per-pixel gradients alive where Dice saturates. Standard pairing for
    thin, sparse targets like slicks."""

    def __init__(self, bce_weight: float = 0.5, smooth: float = 1.0):
        super().__init__()
        self.bce = nn.BCEWithLogitsLoss()
        self.bce_weight = bce_weight
        self.smooth = smooth

    def forward(self, logits, target):
        bce = self.bce(logits, target)
        probs = torch.sigmoid(logits)
        num = 2.0 * (probs * target).sum(dim=(2, 3)) + self.smooth
        den = probs.sum(dim=(2, 3)) + target.sum(dim=(2, 3)) + self.smooth
        dice = 1.0 - (num / den).mean()
        return self.bce_weight * bce + (1.0 - self.bce_weight) * dice


@torch.no_grad()
def binary_metrics(logits, target, threshold: float = 0.5) -> dict:
    """Binary IoU / precision / recall / F1 accumulated as raw counts.

    Pixel accuracy is deliberately absent: sea-class dominance makes it
    meaningless (a model predicting all-sea scores >99%).
    """
    pred = (torch.sigmoid(logits) > threshold).float()
    tp = float((pred * target).sum())
    fp = float((pred * (1 - target)).sum())
    fn = float(((1 - pred) * target).sum())
    return {"tp": tp, "fp": fp, "fn": fn}


def reduce_metrics(acc: dict) -> dict:
    tp, fp, fn = acc["tp"], acc["fp"], acc["fn"]
    eps = 1e-9
    iou = tp / (tp + fp + fn + eps)
    precision = tp / (tp + fp + eps)
    recall = tp / (tp + fn + eps)
    f1 = 2 * precision * recall / (precision + recall + eps)
    return {"iou": iou, "precision": precision, "recall": recall, "f1": f1}


def build_model(encoder: str = "resnet34", weights: str | None = "imagenet"):
    import segmentation_models_pytorch as smp

    # in_channels=1: smp adapts the 3-channel ImageNet stem by summing its
    # weights, which keeps the pretrained features usable on single-band SAR.
    return smp.Unet(encoder_name=encoder, encoder_weights=weights,
                    in_channels=1, classes=1)


def run_epoch(model, loader, criterion, device, scaler=None, optimizer=None,
              desc: str = "", amp: bool = True) -> tuple[float, dict]:
    train = optimizer is not None
    model.train(train)
    acc = {"tp": 0.0, "fp": 0.0, "fn": 0.0}
    total_loss, n_batches = 0.0, 0

    bar = tqdm(loader, desc=desc, unit="batch", leave=False, disable=None)
    for x, y in bar:
        x = x.to(device, non_blocking=True)
        y = y.to(device, non_blocking=True)

        with torch.autocast(device_type=device.type, dtype=torch.float16,
                            enabled=amp and device.type == "cuda"):
            logits = model(x)
            loss = criterion(logits, y)

        if train:
            optimizer.zero_grad(set_to_none=True)
            if scaler is not None and scaler.is_enabled():
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                optimizer.step()

        total_loss += float(loss.detach())
        n_batches += 1
        for k, v in binary_metrics(logits.detach().float(), y).items():
            acc[k] += v
        m = reduce_metrics(acc)
        bar.set_postfix(loss=f"{total_loss / n_batches:.4f}", iou=f"{m['iou']:.4f}")

    return total_loss / max(n_batches, 1), reduce_metrics(acc)


def save_checkpoint(path: Path, model, optimizer, scaler, epoch: int,
                    metrics: dict, args, cfg) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "epoch": epoch,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scaler_state": scaler.state_dict() if scaler is not None else None,
        "metrics": metrics,
        "encoder": args.encoder,
        "in_channels": 1,
        "tile_size": cfg.tiling.tile_size,
        "config_fingerprint": cfg.fingerprint,
        "db_min": cfg.sar.db_min,
        "db_max": cfg.sar.db_max,
        "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tiles", type=Path, default=TILES_ROOT / "trainval")
    ap.add_argument("--out", type=Path, default=RUNS_ROOT / "unet-r34")
    ap.add_argument("--encoder", default="resnet34")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch-size", type=int, default=12,
                    help="12 fits 6 GB VRAM at 256px fp16; 16 is tight.")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--val-fraction", type=float, default=0.2)
    ap.add_argument("--num-workers", type=int, default=2)
    ap.add_argument("--augment", choices=["default", "flips", "none"], default="default")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--no-amp", action="store_true")
    ap.add_argument("--resume", type=Path)
    ap.add_argument("--smoke", action="store_true",
                    help="2 epochs on <=64 tiles -- proves the loop runs.")
    args = ap.parse_args(argv)

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    cfg = load_config()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    amp = not args.no_amp and device.type == "cuda"

    print(f"device      : {device} "
          f"({torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU'})")
    print(f"normalisation: fingerprint {cfg.fingerprint}  "
          f"dB [{cfg.sar.db_min}, {cfg.sar.db_max}]  tile {cfg.tiling.tile_size}")
    if device.type == "cpu":
        print("WARNING: no CUDA device. Training on CPU is not viable for 40 epochs.")

    train_dl, val_dl, train_ds, val_ds = build_dataloaders(
        args.tiles, args.batch_size, args.val_fraction, args.seed,
        args.num_workers, args.augment)

    if args.smoke:
        from torch.utils.data import Subset
        train_dl = torch.utils.data.DataLoader(
            Subset(train_ds, range(min(64, len(train_ds)))),
            batch_size=min(args.batch_size, 4), shuffle=True)
        val_dl = torch.utils.data.DataLoader(
            Subset(val_ds, range(min(32, len(val_ds)))),
            batch_size=min(args.batch_size, 4))
        args.epochs = 2
        print("SMOKE MODE: 2 epochs on a tiny subset")

    print(f"train       : {train_ds.describe()}")
    print(f"val         : {val_ds.describe()}")

    model = build_model(args.encoder).to(device)
    criterion = DiceBCELoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                  weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    scaler = torch.amp.GradScaler("cuda", enabled=amp)

    start_epoch, best_iou = 0, -1.0
    if args.resume and args.resume.exists():
        ckpt = torch.load(args.resume, map_location=device, weights_only=False)
        if ckpt.get("config_fingerprint") != cfg.fingerprint:
            print(f"REFUSING to resume: checkpoint fingerprint "
                  f"{ckpt.get('config_fingerprint')} != current {cfg.fingerprint}")
            return 2
        model.load_state_dict(ckpt["model_state"])
        optimizer.load_state_dict(ckpt["optimizer_state"])
        if ckpt.get("scaler_state") and scaler.is_enabled():
            scaler.load_state_dict(ckpt["scaler_state"])
        start_epoch = ckpt["epoch"] + 1
        best_iou = ckpt.get("metrics", {}).get("val_iou", -1.0)
        print(f"resumed from {args.resume} at epoch {start_epoch} (best IoU {best_iou:.4f})")

    args.out.mkdir(parents=True, exist_ok=True)
    history_path = args.out / "history.jsonl"
    print(f"checkpoints : {args.out}\n")

    for epoch in range(start_epoch, args.epochs):
        t0 = time.time()
        tr_loss, tr_m = run_epoch(model, train_dl, criterion, device, scaler,
                                  optimizer, f"epoch {epoch + 1}/{args.epochs} train", amp)
        va_loss, va_m = run_epoch(model, val_dl, criterion, device, None, None,
                                  f"epoch {epoch + 1}/{args.epochs} val", amp)
        scheduler.step()
        dt = time.time() - t0

        vram = (torch.cuda.max_memory_allocated() / 1024 ** 3
                if device.type == "cuda" else 0.0)
        print(f"epoch {epoch + 1:3d}/{args.epochs}  "
              f"train loss {tr_loss:.4f} IoU {tr_m['iou']:.4f}  |  "
              f"val loss {va_loss:.4f} IoU {va_m['iou']:.4f} "
              f"P {va_m['precision']:.3f} R {va_m['recall']:.3f}  "
              f"[{dt:.0f}s, {vram:.1f} GB]")

        record = {"epoch": epoch + 1, "train_loss": tr_loss, "val_loss": va_loss,
                  "train_iou": tr_m["iou"], "val_iou": va_m["iou"],
                  "val_precision": va_m["precision"], "val_recall": va_m["recall"],
                  "val_f1": va_m["f1"], "lr": scheduler.get_last_lr()[0],
                  "seconds": round(dt, 1)}
        with history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

        metrics = {"val_iou": va_m["iou"], **record}
        # Checkpoint EVERY epoch: a 2-hour run that dies at epoch 38 with no
        # checkpoint is two hours gone.
        save_checkpoint(args.out / "last.pt", model, optimizer, scaler, epoch,
                        metrics, args, cfg)
        if va_m["iou"] > best_iou:
            best_iou = va_m["iou"]
            save_checkpoint(args.out / "best.pt", model, optimizer, scaler, epoch,
                            metrics, args, cfg)
            print(f"           new best val IoU {best_iou:.4f} -> best.pt")

    print(f"\ndone. best val IoU {best_iou:.4f}")
    print(f"next: python -m ml.evaluate --checkpoint {args.out / 'best.pt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
