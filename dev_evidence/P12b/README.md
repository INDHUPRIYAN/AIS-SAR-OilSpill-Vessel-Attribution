# P12b — detect-only screening of candidate scenes

D1 step 2: *"Detect before you commit. Download 3–5 candidate scenes and run
detection only on each. Pick a scene where the deployed model genuinely fires
(≥1 oil candidate surviving look-alike rejection, confidence recorded)."*

Six scenes were screened with `python -m backend.screen_candidates`. No
threshold, gate or weight was changed at any point. Every result is recorded,
including the ones that were wrong the first time.

## Result

| scene | source | oil | look-alike | confidence | engine | seconds |
|---|---|---|---|---|---|---|
| 00000 | Trujillo Part III, held out | 1 | 215 | 0.7643 | ml | 9.7 |
| 00005 | Trujillo Part III, held out | 174 | 0 | 0.7299 | ml | 1.6 |
| 00014 | Trujillo Part III, held out | 41 | 0 | 0.8846 | ml | 1.4 |
| 00019 | Trujillo Part III, held out | 7 | 18 | 0.9388 | ml | 1.4 |
| 00021 | Trujillo Part III, held out | 4 | 10 | 0.8815 | ml | 1.4 |
| `S1A_IW_GRDH_1SDV_20230108T001008_…_E5A1_COG` | **real Sentinel-1, Gulf of Mexico** | 31 | 361 | 0.6882 | ml | 150.2 |

6/6 scenes produced at least one oil candidate. The deployed model
(`unet-r34-fullcorpus-e48` + `yolo11n` screen) ran on all six.

Logs: [`screening_log.json`](screening_log.json) (corpus),
[`../P12b_gulf/screening_log.json`](../P12b_gulf/screening_log.json) (Gulf).

## The flagship candidate

`S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1_COG`

* acquired **2023-01-08T00:10:08Z**, bbox `[-92.995, 27.218, -90.087, 29.143]`
* CDSE → downloaded as `.SAFE`, calibrated in-repo to Sigma0 dB
  (`satellite.cli --calibrate`, VV, thermal noise removed, GCP-warped to
  EPSG:4326, 696,116 land pixels masked). This closes audit item **D-02**.
* 16733 × 25899 px at 10 m
* §6a domain-gap gate: **PASS** — sea p1 −32.65 dB, p50 −19.75 dB, p99 −11.95 dB
  against the frozen normalisation clip `[-35.0, 0.0]`, over 418 M sea pixels
  ([`../P12b_gulf/domain_gap.json`](../P12b_gulf/domain_gap.json)). The gate was
  run *before* detection: a DOMAIN GAP verdict would have made the segmenter's
  output uninterpretable on this scene rather than merely poor, and the honest
  response would have been to say so, not to widen the clip range.
* stage 1 (YOLO11n): 127 oil candidates over 1768 tiles, best 0.73
* stage 2 (U-Net + ResNet-34): 392 regions segmented, **361 rejected as
  look-alike**, 31 called oil, mean confidence 0.6882

### What this result is not

There is **no ground truth for this scene.** It is an operational acquisition,
not a labelled incident. The Gulf of Mexico carries abundant natural
hydrocarbon seeps, and wind shadows and biogenic slicks are common look-alikes.
These 31 regions are model output. They are not confirmed spills, and nothing
downstream should describe them as such.

What the scene *does* establish is what D1 asked for: the deployed model fires
on real, unseen, operationally-acquired SAR that sits inside its training
domain, with the look-alike stage visibly doing work (92% of segmented regions
rejected).

## Two defects found here, and the retraction

Both were mine, both are fixed, both are pinned by tests in
`main_system/tests/test_detect_large_scene.py`.

### 1. The harness counted every candidate as oil

`screen_candidates.py` selected oil with `getattr(c, "is_oil", None) is not
False`. No candidate carries `is_oil`, so the expression was true for every
row and each scene was reported as entirely oil. The look-alike rejection —
the thing this screening was meant to evidence — was invisible in the log.

The field is `class`, and because `class` is a Python keyword the contract
model names it `class_` and aliases it, so reading one spelling silently
mislabels candidates from the other. `_candidate_class()` now reads both.

**Retracted counts** (the rasters and model output never changed; only the
tally was wrong):

| scene | first reported | actual |
|---|---|---|
| 00000 | 216 oil | 1 oil, 215 look-alike |
| 00019 | 25 oil | 7 oil, 18 look-alike |
| 00021 | 14 oil | 4 oil, 10 look-alike |

The conclusion — 5/5 held-out scenes detect oil — survived. The counts did not.

### 2. The Gulf scene did not run the deployed model at all

The first Gulf screening returned `engine: threshold_fallback`,
`model_version: threshold-morphology-v1`. Preserved verbatim at
[`../P12b_gulf/screening_log_01_threshold_fallback.json`](../P12b_gulf/screening_log_01_threshold_fallback.json).

YOLO11n screened the scene successfully; the U-Net then raised

    MemoryError: Unable to allocate 2.24 GiB for an array with
    shape (19968, 30048) and data type float32

which the detector catches by design and answers with the threshold engine. The
scene was still "detected" — just not by the model anyone would assume was
being evaluated. **Every full-size Sentinel-1 scene would have done this,
including P14's flagship run.**

Cause: stitching held two full-scene float32 canvases plus the padded input,
about 11 GB on a 600 M-pixel padded scene. Fix, in
`backend/services/detection/service.py`:

* the probability and count canvases spill to a memory-mapped file above
  200 M pixels;
* `count` is `uint16` — overlap counts are small integers, so this is exact;
* the padded input is released once tiling is done;
* averaging, cropping and thresholding run in row blocks instead of building
  another whole-scene float32 quotient.

**No threshold, gate, weight or model was touched.** The change is memory
layout only, and the arithmetic was verified identical by re-running three
scenes with stored results: confidences returned exactly 0.8846 / 0.9388 /
0.8815 and every count matched. `test_on_disk_and_in_memory_stitching_agree_exactly`
pins the two paths against each other.

Side effect worth recording: the Gulf scene now completes in **150 s versus
539 s**, because the failed 2.24 GiB allocation and the fallback's full-scene
morphology were both more expensive than simply running the model.

## Reproduce

```bash
# corpus
python -m backend.screen_candidates \
  --scene ../data/raw/trujillo/part3/Images/Oil/00000.tif \
  --scene ../data/raw/trujillo/part3/Images/Oil/00005.tif \
  --scene ../data/raw/trujillo/part3/Images/Oil/00014.tif \
  --scene ../data/raw/trujillo/part3/Images/Oil/00019.tif \
  --scene ../data/raw/trujillo/part3/Images/Oil/00021.tif \
  --out dev_evidence/P12b

# the Gulf scene, from CDSE
python -m backend.fetch_scene --bbox -91.5 27.8 -89.0 29.8 \
  --start 2023-01-08 --end 2023-01-09 --name GULF_20230108
python -m satellite.cli --calibrate <unpacked .SAFE> --pol vv \
  --out data/scenes/S1A_GULF_20230108/scene_sigma0_db.tif
python -m satellite.cli --check-domain-gap \
  data/scenes/S1A_GULF_20230108/scene_sigma0_db.tif
python -m backend.screen_candidates \
  --scene ../data/scenes/S1A_GULF_20230108/scene_sigma0_db.tif \
  --out dev_evidence/P12b_gulf
```

## Note carried into P14

Every Sentinel-1 product found over this Gulf box is acquired between
**00:01 and 00:11 UTC**. A backward drift window from a 00:10 acquisition
therefore lies almost entirely in the *previous* UTC day. D1's instruction to
ingest the preceding day as well is load-bearing here, not belt-and-braces:
ingesting only 2023-01-08 would leave the origin window almost empty and the
run would report `NO_VESSELS_IN_WINDOW` for a reason that has nothing to do
with the data.
