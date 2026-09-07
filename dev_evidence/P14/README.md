# P14 — the flagship real-data run

**Run id: `inv-gulf-flagship-20230108-final`**
Code `1a20f7b`, artefact digest `bf47d72e6f76e536`, 8 artefacts hashed and
re-verified unchanged.

```
5/5 stages ran for real, 0 from mocks, 0 failed  (371.82s)
```

| stage | status | engine | data_source | seconds |
|---|---|---|---|---|
| detect | ok | ml | sensor | 147.32 |
| characterise | ok | primary | sensor | 204.36 |
| drift_hindcast | ok | euler | cached | 4.14 |
| drift_forecast | ok | euler | cached | 2.67 |
| attribution | ok | primary | sensor | 3.62 |

Every input is real. Nothing in this run is mocked, synthesised or planted.

## What was real, and from where

| | source | detail |
|---|---|---|
| SAR scene | **Sentinel-1A**, CDSE | `S1A_IW_GRDH_1SDV_20230108T001008_…_E5A1_COG`, acquired 2023-01-08T00:10:08Z, calibrated in-repo to Sigma0 dB (VV), 16733 × 25899 @ 10 m |
| segmenter | `unet-r34-fullcorpus-e48` | 31 oil + 361 look-alike; 92% of segmented regions rejected by the screen |
| screen | `yolo11n-screen-dartis-2026-08-24` | 127 oil candidates over 1768 tiles, best 0.73 |
| currents | **CMEMS** (chain primary) | 49 × 36 @ 1/12°, 3 daily steps, 92.7% finite, mean 0.15 m/s |
| wind | **ECMWF ERA5 (CDS API)** (chain primary) | 17 × 12 @ 0.25°, 72 hourly steps, 100% finite, mean 5.37 m/s |
| AIS | **MarineCadastre** (NOAA OCM) | 2023-01-07 archive, 86,269 rows, 439 MMSI, contract-valid at exactly 14 columns, all `source: real` |

Provider detail and the reachability record: [`forcing.json`](forcing.json).

## Drift and the origin

```
origin window   2023-01-07T11:10:08Z -> 2023-01-08T00:10:08Z   (13.0 h)
method          cloud_convergence
peak            2023-01-08T00:10:08Z
uncertainty     0.3658 km at 0.908 coverage
ellipses        25, semi-major 4921 m .. 6278 m, all 25 non-zero
```

`method: cloud_convergence` matters: it means the backtracked cloud had a real
spread minimum, so the window localises something. The other two values
`origin_window()` can return — `age_estimate` and `midpoint` — mean the current
field never deformed the cloud, the whole run is being reported as the window,
and the peak carries no information. That distinction now travels with the
published artefact; see the defects below.

The peak sits at the acquisition instant. Read honestly, the drift did not
localise a release time *earlier* than the image: the cloud is tightest at the
slick itself and every step within 13 h back is inside 10% of that minimum. The
window is wide and weak, and should be presented as a 13-hour window rather
than as a discharge time.

## Attribution

```
32 vessels considered  ->  4 ranked, 28 filtered (all "outside origin region")
source: real
```

| | MMSI | score | type | name |
|---|---|---|---|---|
| Rank #1 | 367653160 | 0.67 | other | — |
| Rank #2 | 367668740 | 0.64 | other | — |
| Rank #3 | 367357000 | 0.63 | other | — |
| Rank #4 | 367630990 | 0.50 | other | — |

Rank #1's stated reason, unedited:

> Passed through the 90% origin region at 2023-01-07 18:00 UTC, slowed from
> 18.3 to 18.1 kn, altered course by 180°, and ran within 6° of the slick's
> axis.

**No culprit was planted.** `ensure_vessels` selected real AIS because it
covered the computed origin window, so the synthetic generator never ran and no
`culprit.json` exists in the run. The manifest's `ais` block records the choice
and everything rejected on the way:

| candidate | data_source | covers origin |
|---|---|---|
| `vessels.parquet` (this run) | real | **yes → selected** |
| `mc_gulf_2023_01_01.parquet` | real | no |
| `ais_service/test_output/vessels.parquet` | synthetic | no |
| `contracts/mocks/vessels.parquet` | synthetic | no |

The second row is the case that matters: a genuinely real MarineCadastre
archive for a *different day* was rejected. Real is not sufficient — covering
this run's origin is.

### What this result is not

* **These are not culprits.** This is a weighted, explainable ranking over
  vessels that were near a model-detected slick. Rank #1 · Score 0.67 is a
  position in a list, not a finding of responsibility.
* **There is no ground truth for this scene.** It is an operational
  acquisition, not a labelled incident, and the Gulf of Mexico carries abundant
  natural seeps. The 31 "oil" regions are model output.
* **All four ranked vessels have `vessel_name: null`.** The identity sidecar
  holds 123 identities and matches 8 of the 28 filtered vessels, but none of
  the four ranked ones: MarineCadastre carried no static name, IMO or call sign
  for those MMSIs in this window. That is absence of data. It stays null rather
  than being filled in from anywhere else.

## AIS coverage — the exact limitation

[`ais_coverage.json`](ais_coverage.json), measured from the run's own artefacts:

```
origin window     2023-01-07T11:10:08Z .. 2023-01-08T00:10:08Z   (13.0 h)
AIS on disk       2023-01-07T00:10:00Z .. 2023-01-07T23:55:00Z
covered fraction  0.9806
uncovered         15.1 minutes, at the END of the window
rows in window    45,405   MMSI in window 384   sources ['real']
```

**The two-day ingest D1 asks for is NOT complete.** The 2023-01-07 archive
downloaded in full (333,938,388 B). The 2023-01-08 archive did not: NOAA
throttled the transfer to roughly 1–2 kB/s, and it stalled around 239 MB of
326 MB with a projected completion of about 14 hours. The download is
resumable and was left running; this run used one day.

The consequence, stated precisely: **the final 15.1 minutes of the origin
window — 2023-01-07T23:55 to 2023-01-08T00:10:08, ending at the acquisition
instant — carry no AIS.** A vessel that entered the origin region only inside
that last quarter-hour would not appear in the ranking or in the exclusion
ledger. It would be invisible, not excluded.

This is why the window's `covers_origin: true` is true but not the whole story:
`vessels_cover_origin` asks whether *any* report lands inside the window, and
45,405 do. It does not ask whether the window is covered end to end.

## Four defects this run exposed

Each was found by running real data through the pipeline, and each is fixed
with a regression test.

### 1. Characterise was O(regions × scene)

`extract_slicks` built a whole-scene boolean (`labelled == region.label`) for
every region. With 392 regions over 433 M pixels the stage never finished. Now
each region is vectorised from its own bounding box with a translated
transform. Geometry identical (max symmetric difference 6.3e-17 deg²), 24×
faster on a 3 M-pixel scene, and the real scene is 144× larger per pass.
Characterise: *never completed in 13+ min* → **204 s**. Commit `4eb0462`.

### 2. The segmenter fell back to threshold-morphology on any full-size scene

Covered in [P12b](../P12b/README.md). Stitching held ~11 GB of full-scene
float32 canvases, hit `MemoryError`, and the run silently downgraded to an
engine nobody thought was being evaluated. **P14's flagship would have done the
same.** Commit `332706f`.

### 3. Attribution reported FAILED while holding four ranked suspects

`normalise` writes `filter_reason` and `failed_gates` onto each `filtered_out`
entry (the exclusion ledger, audit H5) but `FilteredVessel` forbade extra
fields. The stage ran, produced a correct `suspects.json`, and then marked
*itself* failed on its own contract check. Nothing caught it because every
earlier run had an empty `filtered_out`; the first real-AIS run filtered 28
vessels. Contract extended additively — the new fields are Optional, so runs
sealed before they existed still load.

### 4. The published origin cloud did not say how its window was derived

Normalisation lifted `start`/`end` out of the engine's `origin_window` feature
and dropped the feature, taking `method` with it. A consumer could render
"release localised to 11:10–00:10" for a run that had reported it could not
localise anything. `origin_window_method` and `origin_peak_utc` now travel with
the window.

**Fixing #4 immediately reproduced #3** — new metadata fields, contract not
extended, `drift_hindcast` marked FAILED while writing a correct file. So there
is now a test that compares what normalisation publishes against what the
schemas declare, plus one asserting the models still `forbid` extras so the
first test cannot pass vacuously.

## Reproduce

```bash
# forcing (both chain primaries)
python -m metocean.cli --bbox -93.5 26.7 -89.5 29.7 \
  --start 2023-01-07T00:00:00Z --end 2023-01-09T00:00:00Z --what both \
  --output-dir data/metocean/<scene_id>

# AIS
python -m ais.fetch_archive --date 2023-01-08 --with-preceding-day
python -m backend.prepare_ais --csv data/ais/raw/AIS_2023_01_07.csv \
  --scene-meta data/scenes/S1A_GULF_20230108/scene_meta.json \
  --out-dir data/runs/<run_id>

# run, seal, verify
python -m backend.services.pipeline.run \
  --scene data/scenes/S1A_GULF_20230108/scene_sigma0_db.tif \
  --scene-meta data/scenes/S1A_GULF_20230108/scene_meta.json \
  --run-id <run_id>
python -m backend.services.pipeline.run --verify <run_id>
python -m backend.ais_coverage --run data/runs/<run_id>
```

Acceptance is enforced by `main_system/tests/test_flagship_run.py` (12 tests),
which reads the run this directory's `flagship.json` points at and skips
cleanly when it is not in the checkout. It deliberately does **not** assert
that a culprit was found or that the top score is high: D1 is explicit that an
honest run finding no strong suspect is a valid result, and a test demanding
one would be exactly the pressure to tune gates that the standing rules forbid.
