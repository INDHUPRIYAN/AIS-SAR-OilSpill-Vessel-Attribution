"""Superseded entry point -- kept so old references fail loudly, not silently.

The Trujillo segmentation model now trains via `ml.train_unet`; see ml/README.md
for the full stage order (download -> audit -> prepare -> train -> evaluate ->
export). The DARTIS/YOLO screening stage is not built yet.
"""
import sys


def train_trujillo():
    raise SystemExit(
        "Moved. Run the segmentation training through its own stage:\n"
        "  cd 1_indhu_main_system\n"
        "  ../.venv/Scripts/python -m ml.train_unet --epochs 40 --batch-size 12\n"
        "Prepare tiles first with ml.download -> ml.audit -> ml.prepare_trujillo.")


def train_dartis():
    raise SystemExit(
        "Not implemented. The DARTIS/YOLO screening model (stage 1 of the\n"
        "two-stage detector) still needs building -- it rejects look-alikes\n"
        "before the segmenter runs. Planned as ml.train_yolo.")


if __name__ == "__main__":
    print(__doc__, file=sys.stderr)
    raise SystemExit(1)
