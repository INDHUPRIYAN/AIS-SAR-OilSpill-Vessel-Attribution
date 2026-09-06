# P01 — /api/metrics serves the deployed checkpoint (audit M-05)

Executed: 2026-09-06T17:22:23Z  ·  live uvicorn, port 8078

## BEFORE (P00 capture)
```
served checkpoint_epoch : 29        <- superseded POC
served checkpoint       : (absent)
deployed identity       : unet-r34-fullcorpus-e48
```

## AFTER
```
$ curl -s /api/metrics
segmentation.checkpoint
  name                : unet-r34-fullcorpus-e48
  epoch               : 48
  file                : main_system/backend/services/detection/weights/segment.onnx
  sha256              : a4aac81f3ddd54ce05859c0fc4faada7b196288ed64a54293ab96488c5804a44
  bytes               : 97719644
  config_fingerprint  : 01e24b0fb0e8

segmentation metrics @ threshold 0.5
  oil_tile_iou        : 0.5723
  oil_tile_precision  : 0.9405
  oil_tile_recall     : 0.5938
  oil_tile_f1         : 0.7280
  overall_iou         : 0.4445
  overall_precision   : 0.6387
  overall_recall      : 0.5938
  overall_f1          : 0.6154
  no_oil_tiles        : 5248
  no_oil_firing       : 280  (5.34%)

screening.checkpoint
  name                : yolo11n-screen-dartis-2026-08-24
  sha256              : 9a6ff8df0d2b4a8f8a287895c6780030...
screening metrics
  map50               : 0.6230
  map50_95            : 0.2970
  precision           : 0.6570
  recall              : 0.5653

drift
  status              : experimental
  applied             : False
  evaluated           : negative
  experiment          : drift-residual-mlp-20260906
```

## Contradiction found and resolved (documented, not silently patched)

```
SOURCE A  data/processed/trujillo/test/index.json
          poc_holdout: true
          WARNING: 'carved out of Part III itself; metrics are POC figures,
                    re-measure once Parts I-II are prepared'

SOURCE B  weights/model_card.md:24
          '## Metrics - Trujillo Part III (untouched test split)'

Both e29 and e48 were evaluated on this SAME split (5,760 tiles).
```
**Resolution.** The endpoint follows the split's own index.json -- the artefact that
records how the set was actually built -- and keeps the caveat in `notes[]`. The existing
code comment already stated this precedence; it was correct and was left unchanged.
Publishing e48's better numbers while dropping the caveat would have overstated the model.

**Flagged for P17:** `model_card.md:24` says "untouched test split" and should be
reconciled with the split metadata, or the split re-cut and re-measured. Not changed here:
P01's scope is the endpoint, and the model card is the deployed artefact's own record.
