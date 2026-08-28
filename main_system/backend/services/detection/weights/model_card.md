# Model Card — Trujillo oil-slick segmenter

**Artefact:** `segment.onnx`
**Architecture:** U-Net, `resnet34` encoder (ImageNet init), 1 input channel, 1 output class
**Trained:** epoch 29 selected · checkpoint saved 2026-08-24T16:37:20+00:00
**Exported:** 2026-08-24T16:54:17+00:00

## Input contract (frozen)

Single-band Sigma0 **dB** raster. Preprocessing, which MUST match
`config/normalisation.yaml` (fingerprint `01e24b0fb0e8`):

```
x = (clip(dB, -35.0, 0.0) - (-35.0)) / 35.0
```

Tiles of 256×256, float32 in `[0, 1]`, shape `(batch, 1, 256, 256)`.
At inference, full scenes are tiled with 32px overlap and stitched.

**Output:** raw logits, same spatial shape. Apply `sigmoid`, then threshold
(default 0.5) for the binary mask.

## Metrics — Trujillo Part III (untouched test split)

| Metric | Value |
|---|---|
| Binary IoU | **0.3128** |
| Precision | 0.3227 |
| Recall | 0.9111 |
| F1 | 0.4766 |

Pixel accuracy is **not** reported: sea-class dominance makes it meaningless.

On no-oil test tiles, 755/5248 produced a false detection (14.4%).

## Fallback

When weights are missing or no GPU is present, `/detect` serves the
dependency-free adaptive-threshold + morphology path and reports
`engine: "threshold_fallback"` instead of `engine: "ml"`.

## Parity

ONNX vs PyTorch via `CUDAExecutionProvider` on random inputs — **PASS**.

| Quantity | Value |
|---|---|
| Max probability difference | `1.28e-04` |
| Mask disagreement, all pixels | `0.0000%` |
| Mask disagreement, confident pixels (**gated**) | `0.0000%` |

The gate is *confident* disagreement: pixels where PyTorch was at least 0.05 clear of the 0.5 threshold and ONNX disagreed anyway. Pixels sitting on the threshold are coin-flips that float noise legitimately tips either way. The CUDA provider uses TF32, so raw logits drift by up to `6.69e-03` with no effect on the mask that ships.

## Known limitations

- Trained on Sentinel-1 Sigma0 dB from the Trujillo corpus only; DARTIS is a
  separate screening model and the two are never merged (different radiometry,
  format and label geometry).
- Look-alike rejection is the screening stage's job, not this segmenter's.
- Performance on a new scene depends on that scene being calibrated to the
  same dB convention. Verify before locking any demo scene.
