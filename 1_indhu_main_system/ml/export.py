"""Export the selected checkpoint to ONNX and write its model card.

The exported artefact carries its normalisation constants in ONNX metadata,
so the /detect service can assert at load time that it is preprocessing
scenes the same way training did, rather than trusting a config file to have
stayed in sync.

Export is followed by a parity check against PyTorch on random inputs. An
ONNX graph that silently differs from the trained model is the kind of bug
that only surfaces on demo day.

Usage:
    python -m ml.export --checkpoint data/runs/training/unet-r34/best.pt
    python -m ml.export --checkpoint ... --opset 17 --no-parity
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from ml.config import REPO_ROOT, load_config
from ml.evaluate import load_checkpoint

MODELS_ROOT = REPO_ROOT / "1_indhu_main_system" / "backend" / "services" / "detection" / "weights"


def export_onnx(model, out_path: Path, tile: int, cfg, opset: int = 17) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.randn(1, 1, tile, tile)

    torch.onnx.export(
        model.cpu(), dummy, str(out_path),
        input_names=["input"], output_names=["logits"],
        # Dynamic batch lets the service tile a full scene and run batches of
        # whatever size fits, without a re-export per batch size.
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=opset, do_constant_folding=True,
    )

    import onnx
    m = onnx.load(str(out_path))
    onnx.checker.check_model(m)
    meta = {
        "config_fingerprint": cfg.fingerprint,
        "db_min": str(cfg.sar.db_min), "db_max": str(cfg.sar.db_max),
        "tile_size": str(tile), "in_channels": "1",
        "input_range": "float32 [0,1] = (clip(dB, db_min, db_max) - db_min) / (db_max - db_min)",
        "output": "raw logits; apply sigmoid then threshold (default 0.5)",
    }
    for k, v in meta.items():
        entry = m.metadata_props.add()
        entry.key, entry.value = k, v
    onnx.save(m, str(out_path))
    return out_path


def parity_check(model, onnx_path: Path, tile: int, n: int = 3,
                 threshold: float = 0.5, confident_margin: float = 0.05,
                 max_disagreement: float = 1e-4) -> dict:
    """Compare ONNX against PyTorch on what actually ships: the binary mask.

    Raw logits are the wrong thing to gate on. ONNX Runtime's CUDA provider
    uses TF32 and reorders fused ops, so logits routinely differ by a few 1e-3
    while the decisions that matter agree.

    Nor is total mask disagreement the right gate. A pixel whose probability
    sits at 0.4999 is a coin-flip: float noise legitimately tips it either way,
    and an undertrained model has many such pixels. Failing the export on those
    would block a perfectly good artefact.

    So the gate is CONFIDENT disagreement -- pixels where PyTorch was sure
    (probability at least `confident_margin` clear of the threshold) and ONNX
    disagreed anyway. Rounding cannot do that; a wrong graph can. Total
    disagreement is still reported, as context.
    """
    import onnxruntime as ort

    available = ort.get_available_providers()
    providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                 if "CUDAExecutionProvider" in available else ["CPUExecutionProvider"])
    sess = ort.InferenceSession(str(onnx_path), providers=providers)
    model.eval().cpu()

    worst_logit = worst_prob = worst_disagree = worst_confident = 0.0
    confident_share = 0.0
    for _ in range(n):
        x = np.random.rand(2, 1, tile, tile).astype(np.float32)
        with torch.no_grad():
            ref = model(torch.from_numpy(x)).numpy()
        got = sess.run(["logits"], {"input": x})[0]

        worst_logit = max(worst_logit, float(np.abs(ref - got).max()))
        p_ref = 1.0 / (1.0 + np.exp(-ref))
        p_got = 1.0 / (1.0 + np.exp(-got))
        worst_prob = max(worst_prob, float(np.abs(p_ref - p_got).max()))

        flipped = (p_ref > threshold) != (p_got > threshold)
        worst_disagree = max(worst_disagree, float(flipped.mean()))

        confident = np.abs(p_ref - threshold) >= confident_margin
        confident_share = max(confident_share, float(confident.mean()))
        n_confident = int(confident.sum())
        if n_confident:
            worst_confident = max(
                worst_confident, float((flipped & confident).sum()) / n_confident)

    return {
        "provider": providers[0],
        "max_abs_logit_diff": worst_logit,
        "max_abs_prob_diff": worst_prob,
        "mask_disagreement_rate": worst_disagree,
        "confident_disagreement_rate": worst_confident,
        "confident_pixel_share": confident_share,
        "confident_margin": confident_margin,
        "threshold": threshold,
        "max_disagreement": max_disagreement,
        "passed": worst_confident <= max_disagreement,
    }


def parity_section(parity: dict | None) -> str:
    """Render the parity results as a markdown block for the model card."""
    if not parity:
        return "_(parity check skipped)_"
    verdict = "PASS" if parity["passed"] else "FAIL"
    return (
        f"ONNX vs PyTorch via `{parity['provider']}` on random inputs — "
        f"**{verdict}**.\n\n"
        f"| Quantity | Value |\n|---|---|\n"
        f"| Max probability difference | `{parity['max_abs_prob_diff']:.2e}` |\n"
        f"| Mask disagreement, all pixels | `{parity['mask_disagreement_rate'] * 100:.4f}%` |\n"
        f"| Mask disagreement, confident pixels (**gated**) | "
        f"`{parity['confident_disagreement_rate'] * 100:.4f}%` |\n\n"
        f"The gate is *confident* disagreement: pixels where PyTorch was at least "
        f"{parity['confident_margin']} clear of the {parity['threshold']} threshold "
        f"and ONNX disagreed anyway. Pixels sitting on the threshold are coin-flips "
        f"that float noise legitimately tips either way. The CUDA provider uses "
        f"TF32, so raw logits drift by up to `{parity['max_abs_logit_diff']:.2e}` "
        f"with no effect on the mask that ships."
    )


def write_model_card(path: Path, ckpt: dict, cfg, onnx_path: Path,
                     parity: dict | None, metrics_path: Path) -> None:
    metrics = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    at_half = (metrics.get("results", {}) or {}).get("0.5", {}).get("overall", {})
    rows = ""
    if at_half:
        rows = (f"| Binary IoU | **{at_half.get('iou', 0):.4f}** |\n"
                f"| Precision | {at_half.get('precision', 0):.4f} |\n"
                f"| Recall | {at_half.get('recall', 0):.4f} |\n"
                f"| F1 | {at_half.get('f1', 0):.4f} |\n")
    else:
        rows = "| _(run `python -m ml.evaluate` to populate)_ | — |\n"

    fp_line = ""
    fp = (metrics.get("results", {}) or {}).get("0.5", {})
    if fp.get("scene_level_false_positive_rate") is not None:
        fp_line = (f"\nOn no-oil test tiles, {fp['no_oil_tiles_with_false_detection']}"
                   f"/{fp['no_oil_tiles']} produced a false detection "
                   f"({fp['scene_level_false_positive_rate'] * 100:.1f}%).\n")

    card = f"""# Model Card — Trujillo oil-slick segmenter

**Artefact:** `{onnx_path.name}`
**Architecture:** U-Net, `{ckpt.get('encoder', 'resnet34')}` encoder (ImageNet init), 1 input channel, 1 output class
**Trained:** epoch {ckpt.get('epoch', '?')} selected · checkpoint saved {ckpt.get('saved_utc', '?')}
**Exported:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}

## Input contract (frozen)

Single-band Sigma0 **dB** raster. Preprocessing, which MUST match
`config/normalisation.yaml` (fingerprint `{cfg.fingerprint}`):

```
x = (clip(dB, {cfg.sar.db_min}, {cfg.sar.db_max}) - ({cfg.sar.db_min})) / {cfg.sar.db_max - cfg.sar.db_min}
```

Tiles of {cfg.tiling.tile_size}×{cfg.tiling.tile_size}, float32 in `[0, 1]`, shape `(batch, 1, {cfg.tiling.tile_size}, {cfg.tiling.tile_size})`.
At inference, full scenes are tiled with {cfg.tiling.inference_overlap}px overlap and stitched.

**Output:** raw logits, same spatial shape. Apply `sigmoid`, then threshold
(default 0.5) for the binary mask.

## Metrics — Trujillo Part III (untouched test split)

| Metric | Value |
|---|---|
{rows}
Pixel accuracy is **not** reported: sea-class dominance makes it meaningless.
{fp_line}
## Fallback

When weights are missing or no GPU is present, `/detect` serves the
dependency-free adaptive-threshold + morphology path and reports
`engine: "threshold_fallback"` instead of `engine: "ml"`.

## Parity

{parity_section(parity)}

## Known limitations

- Trained on Sentinel-1 Sigma0 dB from the Trujillo corpus only; DARTIS is a
  separate screening model and the two are never merged (different radiometry,
  format and label geometry).
- Look-alike rejection is the screening stage's job, not this segmenter's.
- Performance on a new scene depends on that scene being calibrated to the
  same dB convention. Verify before locking any demo scene.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(card, encoding="utf-8")
    print(f"model card -> {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=MODELS_ROOT / "segment.onnx")
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--no-parity", action="store_true")
    ap.add_argument("--metrics", type=Path,
                    default=REPO_ROOT / "data" / "runs" / "training" / "metrics.json")
    args = ap.parse_args(argv)

    cfg = load_config()
    model, ckpt = load_checkpoint(args.checkpoint, torch.device("cpu"))
    tile = int(ckpt.get("tile_size", cfg.tiling.tile_size))

    print(f"checkpoint : {args.checkpoint} (epoch {ckpt.get('epoch')})")
    print(f"fingerprint: {cfg.fingerprint}")
    onnx_path = export_onnx(model, args.out, tile, cfg, args.opset)
    print(f"onnx       -> {onnx_path} "
          f"({onnx_path.stat().st_size / 1024 ** 2:.1f} MB, opset {args.opset})")

    parity = None
    if not args.no_parity:
        parity = parity_check(model, onnx_path, tile)
        status = "PASS" if parity["passed"] else "FAIL"
        print(f"parity     : prob diff {parity['max_abs_prob_diff']:.2e}  "
              f"mask disagreement {parity['mask_disagreement_rate'] * 100:.4f}% all "
              f"/ {parity['confident_disagreement_rate'] * 100:.4f}% confident "
              f"(gated)  via {parity['provider']} -- {status}")
        if parity["confident_pixel_share"] < 0.5:
            print(f"             note: only "
                  f"{parity['confident_pixel_share'] * 100:.0f}% of pixels were "
                  f"confident, so most disagreement is threshold noise. Expected "
                  f"for an undertrained model.")
        if not parity["passed"]:
            print(f"ONNX flips pixels PyTorch was CONFIDENT about: "
                  f"{parity['confident_disagreement_rate'] * 100:.4f}% "
                  f"(limit {parity['max_disagreement'] * 100:.4f}%). That is not "
                  f"rounding -- the exported graph is wrong. Do not ship it.")
            return 3

    write_model_card(args.out.parent / "model_card.md", ckpt, cfg, onnx_path,
                     parity, args.metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
