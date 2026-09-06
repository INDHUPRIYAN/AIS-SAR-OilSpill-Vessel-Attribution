# Segmenter promotion record — 2026-09-01

**Frozen holdout:** `data/processed/trujillo/test` — 90 Part-III scenes, 5,760 unfiltered
256px tiles (512 with oil / 5,248 without), fingerprint `01e24b0fb0e8`. Same tiles, same
`ml.evaluate`, for both models. Binary IoU / precision / recall only — pixel accuracy is
not reported (sea dominance makes it meaningless).

| thr | model | IoU | P | R | F1 | IoU (oil tiles) | clean tiles w/ false detection |
|---|---|---|---|---|---|---|---|
| 0.3 | POC e29 (previous) | 0.2246 | 0.2272 | 0.9526 | 0.3668 | 0.8971 | 1015/5248 (19.3%) |
| 0.3 | full-corpus e48 (deployed) | 0.3750 | 0.4041 | 0.8387 | 0.5455 | 0.7946 | 400/5248 (7.6%) |
| 0.4 | POC e29 (previous) | 0.2744 | 0.2795 | 0.9375 | 0.4307 | 0.8946 | 960/5248 (18.3%) |
| 0.4 | full-corpus e48 (deployed) | 0.4350 | 0.5323 | 0.7041 | 0.6063 | 0.6736 | 365/5248 (7.0%) |
| 0.5 | POC e29 (previous) | 0.3128 | 0.3227 | 0.9111 | 0.4766 | 0.8781 | 755/5248 (14.4%) |
| 0.5 | full-corpus e48 (deployed) | 0.4445 | 0.6387 | 0.5938 | 0.6154 | 0.5723 | 280/5248 (5.3%) |
| 0.6 | POC e29 (previous) | 0.3340 | 0.3837 | 0.7205 | 0.5008 | 0.7003 | 659/5248 (12.6%) |
| 0.6 | full-corpus e48 (deployed) | 0.4332 | 0.6886 | 0.5387 | 0.6045 | 0.5225 | 198/5248 (3.8%) |
| 0.7 | POC e29 (previous) | 0.3543 | 0.4754 | 0.5816 | 0.5232 | 0.5699 | 546/5248 (10.4%) |
| 0.7 | full-corpus e48 (deployed) | 0.4209 | 0.7384 | 0.4947 | 0.5925 | 0.4826 | 177/5248 (3.4%) |

## Decision

**PROMOTED** `unet-r34-fullcorpus-e48` (sha256 `a4aac81f3ddd54ce05859c0fc4faada7b196288ed64a54293ab96488c5804a44`,
parity PASS, 0.0000% confident disagreement) over `unet-r34` POC e29.

- Gate: beat the deployed model's binary IoU **on this holdout**. Deployed = **0.3128** (its
  own model card). New = **0.4445** at threshold 0.5; the new model leads at every threshold.
- Trade-off stated plainly: the new model is far more precise (false detections on clean
  tiles 14.4% → 5.3%; false-positive pixel rate 5.9% → 0.9%) but under-segments
  (recall 0.91 → 0.59; IoU on oil-bearing tiles 0.88 → 0.57). Threshold 0.4 is the
  recall-favouring alternative (R 0.70, IoU 0.435, FP 7.0%).
- The "0.655 val IoU" sometimes quoted for the POC model is its *training-time validation*
  score on a different split. It was never a holdout number and is not the gate.

## Training provenance

Corpus: Trujillo Parts I+II whole + Part III minus the 90 holdout scenes = 2,930 scenes →
93,646 tiles (15,115 oil / 78,531 negative), split by scene 80/20 (2,344 / 586 scenes).
U-Net ResNet-34 (ImageNet init), 1-ch input, Dice+BCE, AdamW lr 3e-4 cosine, AMP fp16,
batch 12, 50 epochs, seed 1337; best epoch 48 (val IoU 0.5816 at 49 within noise).
~11 h on an RTX 4050 Laptop GPU. Per-epoch curve: `unet-r34-fullcorpus_history.jsonl`.

## Screening model (Model 1) — DARTIS validation, 2026-09-01

The deployed `screen.onnx` is the **yolo11n** run (`screen/weights/best.onnx`, sha256
`99940e77…`). It previously had no recorded metrics; the number on the slides (mAP@0.5
0.625) came from an unshipped yolo11s run. Both are now measured on the same DARTIS
validation set at conf 0.25:

| model | shipped | mAP@0.5 | mAP@0.5:0.95 | P | R | background patches falsely flagged |
|---|---|---|---|---|---|---|
| yolo11n (`screen`) | **yes** | **0.623** | 0.297 | 0.657 | 0.565 | **14/458 (3.1%)** |
| yolo11s (`screen_s`) | no | 0.625 | 0.299 | 0.668 | 0.563 | 20/458 (4.4%) |

Decision: keep yolo11n deployed — equal mAP, fewer false alarms on look-alikes (the
number that matters for a screening stage), 3.6× smaller. Records:
`screen-yolo11n-deployed_dartis.json`, `screen-yolo11s-unshipped_dartis.json`.

## AIS spatial index — wired into attribution, 2026-09-01

Attribution now routes its vessel set through `ais_service/ais/index.py` (`AISStore`:
region/day parquet partitions with a bbox/timespan manifest, then a shapely STRtree fine
pass over `query_cloud`). Each run ingests into its own region (`region=<run_id>`) so
synthetic scenarios can never leak into one another; the prune is generous (25 km buffer,
±3 h) so Engine C still sees and explains the near-miss vessels.

In-pipeline (BALI chip, 1,963 synthetic rows): 43 vessels → 27 vessels handed to Engine C;
`query_cloud` 218 ms (includes STRtree + whole-track re-query) vs 5 ms flat read.
At that size the index is overhead — the honest statement. It exists for archives:

| rows | partitions | ingest | flat full-scan (read + filter) | indexed query | hits equal |
|---|---|---|---|---|---|
| 2,000 | 30 | 0.6 s | 33.9 ms | **8.4 ms** | yes |
| 100,000 | 30 | 2.3 s | 44.3 ms | **17.9 ms** | yes |
| 1,000,000 | 30 | 17.0 s | 171.3 ms | **31.1 ms** | yes |

(6-hour window, 0.8°×0.6° bbox, synthetic uniform traffic over 30 days; identical hit
sets.) Record: `ais_index_scaling_2026-09-01.json`.

## Chennai 2017-01-29 — first real Sentinel-1 scene end to end, 2026-09-01

Scene `S1A_IW_GRDH_1SDV_20170129T003132_…_8B05` calibrated by the in-house SAFE→σ⁰ dB
chain (28,072 × 21,162 px, float32, EPSG:4326). Domain-gap gate **PASS**
(`chennai_domain_gap_2026-09-01.json`: sea p1 −30.35 / p50 −18.85 / p99 −13.55 dB,
86.8 M sea pixels, 0.0% pinned at either clip edge). Forcing is real: CMEMS GLORYS12
currents (43×55 @ 1/12°, daily) and ERA5 10 m winds (15×19, hourly) for
2017-01-27 → 01-31 — replacing the hand-written uniform-translation field.

| run | Engine A input | total | detect | Engine A | slicks | origin window | suspects |
|---|---|---|---|---|---|---|---|
| `inv-chennai-real` | full raster (594 Mpx, 5.2 GB RSS) | 482 s | 142 s | 315 s | 18 (7.411 km²) | 7 h of 24 h — **converged** | 1 |
| `inv-chennai-real2/3` | footprint window 5,711 × 11,737 px (+1,500 px margin) | 235 s | 157 s | **54 s** | 18 (7.411 km²) — identical | 7 h of 24 h — identical | 1 — identical |

What the run says, honestly:

- Detection segmented 76,741 pixels (0.013% of the scene) in 78 regions; the screening
  overlay rejected **all 78 as look-alikes** (best oil score 0.40). The manifest carries
  the warning "treat this origin and its suspects as unconfirmed". This is the
  correct behaviour for a scene with no confirmed slick signature under this model.
- With real forcing the hindcast **localises a release window** (7 h out of the 24 h
  run); under the old synthetic field it could not (whole run reported).
- Forecast footprints grow monotonically with horizon: ellipse 1.48 → 2.19 → 3.03 km²
  and hull 4.75 → 7.29 → 13.51 km² at 6/12/24 h.
- Provenance per layer: detection/geometry **SENSOR**, drift **SENSOR** (CMEMS+ERA5),
  attribution **SYNTHETIC** (generated AIS — no real AIS exists for Indian waters).
- Engine A loads the whole mask and scene into memory; the pipeline now hands it a
  georeferenced window around the detections (`pipeline/footprint_crop.py`), which is
  what makes full-scene runs practical. Geometry is byte-identical between the two.

Demo run to keep: `inv-chennai-real3` (all provenance fixes loaded).
