"""Train the DARTIS screening detector -- Model 2 of two.

A one-class YOLO `oil` detector whose real job is *not* firing on look-alikes.
It runs before the segmenter: if this stage says a dark patch is a low-wind calm
area or an internal wave, the U-Net is never asked to delineate it.

What matters here is precision on background, not mAP. The dataset is built so
that ~63% of images (the 2,290 nw/nc patches) contain no oil at all, and every
one of them is a chance for the model to raise a false alarm. So evaluation
reports the **background false-positive rate** alongside mAP -- the share of
no-oil patches on which the detector claims a slick. That is the number a judge
is really asking about when they say "how do you know it's oil?".

Usage:
    python -m ml.train_yolo --epochs 50
    python -m ml.train_yolo --smoke                # 3 epochs, tiny subset
    python -m ml.train_yolo --evaluate runs/screen/weights/best.pt
    python -m ml.train_yolo --export  runs/screen/weights/best.pt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def go_offline() -> None:
    """Cut every network path Ultralytics might take mid-run.

    Called at IMPORT time, before ultralytics is ever imported, because it
    resolves its ONLINE flag during its own import -- setting these variables
    afterwards is too late and the process would still try to reach the
    network. Training needs no network (data and weights are local), but
    Ultralytics also does analytics uploads, version checks and an on-demand
    font download for plots, any of which can stall on a dead link.
    """
    for var in ("YOLO_OFFLINE", "ULTRALYTICS_OFFLINE"):
        os.environ[var] = "True"
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("YOLO_VERBOSE", "True")


go_offline()

from ml.config import REPO_ROOT  # noqa: E402
from ml.dartis import YOLO as DARTIS_YOLO  # noqa: E402

RUNS = REPO_ROOT / "data" / "runs" / "training"
WEIGHTS_OUT = REPO_ROOT / "1_indhu_main_system" / "backend" / "services" / "detection" / "weights"


def require_dataset() -> Path:
    yaml_path = DARTIS_YOLO / "dartis.yaml"
    if not yaml_path.exists():
        raise SystemExit(
            "DARTIS YOLO dataset not built. Run:\n"
            "  python -m ml.dartis matrix\n"
            "  python -m ml.dartis images\n"
            "  python -m ml.dartis prepare")
    return yaml_path


def train(epochs: int, batch: int, imgsz: int, model_name: str, name: str,
          device: str, workers: int, smoke: bool, resume: bool = False,
          patience: int = 0, cos_lr: bool = True) -> Path:
    import torch
    from ultralytics import YOLO

    try:                                   # analytics upload is a network call
        from ultralytics.utils import SETTINGS
        if SETTINGS.get("sync"):
            SETTINGS.update({"sync": False})
    except Exception:
        pass
    yaml_path = require_dataset()
    if device == "auto":
        device = "0" if torch.cuda.is_available() else "cpu"

    if smoke:
        epochs, batch = 3, min(batch, 8)
        print("SMOKE MODE: 3 epochs -- proves the loop runs, tells you nothing else")

    # Resume picks up from last.pt, which Ultralytics rewrites every epoch, so
    # an interruption costs at most one epoch of work. The optimizer state and
    # epoch counter travel with the checkpoint, so this is a true continuation,
    # not a restart from the weights.
    last = RUNS / name / "weights" / "last.pt"
    if resume:
        if not last.exists():
            print(f"--resume: nothing to resume at {last}; starting fresh")
            resume = False
        else:
            print(f"resuming from {last}")
            model = YOLO(str(last))
            model.train(resume=True)
            best = RUNS / name / "weights" / "best.pt"
            print(f"\nbest weights -> {best}")
            return best
    elif last.exists():
        print(f"NOTE: {last} exists -- pass --resume to continue it "
              f"instead of overwriting")

    print(f"dataset : {yaml_path}")
    print(f"model   : {model_name}   device {device}   "
          f"{epochs} epochs, batch {batch}, imgsz {imgsz}")

    model = YOLO(model_name)
    model.train(
        data=str(yaml_path),
        epochs=epochs,
        batch=batch,
        imgsz=imgsz,
        device=device,
        workers=workers,
        project=str(RUNS),
        name=name,
        exist_ok=True,
        seed=1337,
        # Early stopping scaled to the schedule. A flat 15 on a 100-epoch run
        # would cut it short around epoch 60, wasting the longer schedule --
        # detection mAP often plateaus for 20+ epochs before improving again.
        patience=patience or max(15, epochs // 4),
        # Cosine decay rather than linear: over a long run it spends more time
        # at low LR, which is where the last points of mAP come from.
        cos_lr=cos_lr,
        # SAR patches have no canonical orientation, so flips are free signal.
        # Colour jitter is meaningless on single-channel backscatter rendered to
        # greyscale JPEG, so hue/saturation augmentation is switched off rather
        # than left at defaults tuned for natural photographs.
        fliplr=0.5, flipud=0.5, degrees=15.0,
        hsv_h=0.0, hsv_s=0.0, hsv_v=0.2,
        mosaic=0.5,
        plots=True,
        verbose=True,
    )
    best = RUNS / name / "weights" / "best.pt"
    print(f"\nbest weights -> {best}")
    return best


def evaluate(weights: Path, imgsz: int, device: str, conf: float = 0.25) -> dict:
    """Standard detection metrics plus the background false-positive rate."""
    import torch
    from ultralytics import YOLO

    yaml_path = require_dataset()
    if device == "auto":
        device = "0" if torch.cuda.is_available() else "cpu"

    model = YOLO(str(weights))
    metrics = model.val(data=str(yaml_path), imgsz=imgsz, device=device, verbose=False)

    box = metrics.box
    result = {
        "weights": str(weights),
        "map50": float(box.map50),
        "map50_95": float(box.map),
        "precision": float(box.mp),
        "recall": float(box.mr),
    }

    # --- background false-positive rate ----------------------------------
    # Ultralytics reports mAP over labelled objects; it says nothing about how
    # often the model invents a slick on a patch that has none. Measure that
    # directly on the val images whose label file is empty.
    val_images = sorted((DARTIS_YOLO / "val" / "images").glob("*.jpg"))
    negatives = []
    for img in val_images:
        lbl = DARTIS_YOLO / "val" / "labels" / (img.stem + ".txt")
        if not lbl.exists() or not lbl.read_text().strip():
            negatives.append(img)

    fired = 0
    if negatives:
        for i in range(0, len(negatives), 32):
            chunk = [str(p) for p in negatives[i:i + 32]]
            for pred in model.predict(chunk, imgsz=imgsz, device=device,
                                      conf=conf, verbose=False):
                if len(pred.boxes) > 0:
                    fired += 1
        result["background_images"] = len(negatives)
        result["background_false_positives"] = fired
        result["background_fp_rate"] = fired / len(negatives)
        result["conf_threshold"] = conf

    print("\n" + "=" * 62)
    print("DARTIS screening detector -- validation")
    print("=" * 62)
    print(f"  mAP@0.5      {result['map50']:.4f}")
    print(f"  mAP@0.5:0.95 {result['map50_95']:.4f}")
    print(f"  precision    {result['precision']:.4f}   recall {result['recall']:.4f}")
    if negatives:
        print(f"\n  look-alike / background patches: {len(negatives)}")
        print(f"  falsely flagged as oil (conf>{conf}): {fired} "
              f"({result['background_fp_rate']*100:.1f}%)")
        print("  ^ this is the number that matters for the screening stage")

    out = RUNS / "screen_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    result["generated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nmetrics -> {out}")
    return result


def export(weights: Path, imgsz: int) -> Path:
    from ultralytics import YOLO

    WEIGHTS_OUT.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(weights))
    # dynamic=True gives a variable batch axis. Without it the graph is pinned
    # to batch 1 and any batched call fails outright -- which matters because
    # the detection service tiles a full scene and runs those tiles in batches.
    produced = Path(model.export(format="onnx", imgsz=imgsz, opset=17,
                                 dynamic=True, simplify=False))
    target = WEIGHTS_OUT / "screen.onnx"
    target.write_bytes(produced.read_bytes())
    print(f"screening model -> {target} ({target.stat().st_size/1024**2:.1f} MB)")
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="yolo11n.pt",
                    help="yolo11n.pt (default) or yolov8n.pt")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--imgsz", type=int, default=640, help="DARTIS patches are 640x640")
    ap.add_argument("--name", default="screen")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--patience", type=int, default=0,
                    help="Early-stop patience; 0 = epochs//4 (min 15).")
    ap.add_argument("--no-cos-lr", action="store_true",
                    help="Use linear LR decay instead of cosine.")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="Continue an interrupted run from its last.pt "
                         "(optimizer state and epoch counter included).")
    ap.add_argument("--evaluate", type=Path, help="Evaluate these weights and exit")
    ap.add_argument("--export", type=Path, help="Export these weights to ONNX and exit")
    args = ap.parse_args(argv)

    if args.evaluate:
        evaluate(args.evaluate, args.imgsz, args.device, args.conf)
        return 0
    if args.export:
        export(args.export, args.imgsz)
        return 0

    best = train(args.epochs, args.batch, args.imgsz, args.model, args.name,
                 args.device, args.workers, args.smoke, args.resume,
                 args.patience, not args.no_cos_lr)
    if best.exists():
        evaluate(best, args.imgsz, args.device, args.conf)
        print(f"\nnext: python -m ml.train_yolo --export {best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
