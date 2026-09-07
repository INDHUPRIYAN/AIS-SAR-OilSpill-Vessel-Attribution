# P14 — the flagship real-data run

**Run id: `inv-gulf-flagship-20230108-2day`**
Code `03b48a7`, artefact digest `fd42e078f8366110`, 8 artefacts hashed and
re-verified unchanged. This is the run with D1's **complete two-day AIS
ingest**; `inv-gulf-flagship-20230108-final` is its one-day predecessor, still
sealed and still verifying, and the two are compared in
[`two_day_delta.json`](two_day_delta.json).

```
5/5 stages ran for real, 0 from mocks, 0 failed  (384.30s)
```

| stage | status | engine | data_source | seconds |
|---|---|---|---|---|
| detect | ok | ml | sensor | 154.43 |
| characterise | ok | primary | sensor | 205.36 |
| drift_hindcast | ok | euler | cached | 4.37 |
| drift_forecast | ok | euler | cached | 2.68 |
| attribution | ok | primary | sensor | 3.58 |

Every input is real. Nothing in this run is mocked, synthesised or planted.

## What was real, and from where

| | source | detail |
|---|---|---|
| SAR scene | **Sentinel-1A**, CDSE | `S1A_IW_GRDH_1SDV_20230108T001008_…_E5A1_COG`, acquired 2023-01-08T00:10:08Z, calibrated in-repo to Sigma0 dB (VV), 16733 × 25899 @ 10 m |
| segmenter | `unet-r34-fullcorpus-e48` | 31 oil + 361 look-alike; 92% of segmented regions rejected by the screen |
| screen | `yolo11n-screen-dartis-2026-08-24` | 127 oil candidates over 1768 tiles, best 0.73 |
| currents | **CMEMS** (chain primary) | 49 × 36 @ 1/12°, 3 daily steps, 92.7% finite, mean 0.15 m/s |
| wind | **ECMWF ERA5 (CDS API)** (chain primary) | 17 × 12 @ 0.25°, 72 hourly steps, 100% finite, mean 5.37 m/s |
| AIS | **MarineCadastre** (NOAA OCM) | 2023-01-07 **and** 2023-01-08 archives, 86,830 rows, 441 MMSI, 126 identities, contract-valid at exactly 14 columns, all `source: real` |

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
  holds 126 identities but matches none of the four ranked MMSIs: MarineCadastre carried no static name, IMO or call sign
  for those MMSIs in this window. That is absence of data. It stays null rather
  than being filled in from anywhere else.

## AIS coverage — the two-day ingest, and what it changed

[`ais_coverage.json`](ais_coverage.json), measured from the run's own artefacts:

```
origin window     2023-01-07T11:10:08Z .. 2023-01-08T00:10:08Z   (13.0 h)
AIS on disk       2023-01-07T00:10:00Z .. 2023-01-08T00:10:00Z
covered fraction  0.9998
uncovered         0.1 minutes
rows in window    45,966   MMSI in window 386   sources ['real']
```

**D1's two-day ingest is complete.** Both MarineCadastre archives downloaded in
full — 2023-01-07 (333,938,388 B) and 2023-01-08 (326,026,492 B, matching the
server's declared length, zip integrity verified). The second took hours: NOAA
throttled it to ~1–2 kB/s for most of the transfer, which is why
`ais/fetch_archive.py` resumes from the byte already on disk rather than
restarting.

The archives are concatenated **before** parsing, byte-exactly: day1 + day2
minus 127 bytes, precisely one duplicate header line, with the header-equality
guard confirming both days share a schema first. Parsing them separately would
run `interpolate_trajectory` on each in isolation and sever every vessel track
at midnight — exactly where the origin window sits.

The residual 0.1 minutes is **not a gap in the data**: MarineCadastre reports
land on a one-minute grid, so the last row is 00:10:00 while acquisition is at
00:10:08. Coverage is complete to the archive's own resolution. `covered_fraction`
is reported as 0.9998 rather than rounded to 1.0 because the measurement is what
it is.

### What the second day actually changed

Recorded in [`two_day_delta.json`](two_day_delta.json). The honest answer:
**measurably, but not materially.**

| rank | MMSI | one day | two days | delta | minutes in origin window |
|---|---|---|---|---|---|
| 1 | 367653160 | 0.6689 | 0.6691 | +0.0002 | 759.9 → 774.9 |
| 2 | 367668740 | 0.6405 | 0.6405 | 0.0000 | 724.9 → 724.9 |
| 3 | 367357000 | 0.6256 | 0.6330 | +0.0074 | 764.9 → 774.9 |
| 4 | 367630990 | 0.5024 | **0.4978** | **−0.0046** | 515.0 → 520.0 |

* The ordering is unchanged and no vessel entered or left the ranking.
* 485 rows across 248 MMSI fall in the final 10 minutes, but only **2 MMSI**
  (367642490, 538004693) are genuinely new to the dataset, and **neither
  reached attribution**. Day 2 mostly adds track *points* to vessels already
  followed through 2023-01-07 — which is why it refines evidence rather than
  changing who is considered.
* Three of the four suspects gained time inside the origin window, and their
  proximity sub-scores moved. **Rank #4's score went down.** The extra data did
  not flatter the result; it re-measured it.

The one-day predecessor was therefore not biased by its 15-minute gap — but
that is now **measured, not assumed**, and it could only be established by
doing the ingest. Under one day, a vessel entering the origin region solely in
that final quarter-hour would have been *invisible rather than excluded*:
absent from both the ranking and the exclusion ledger, with nothing in the
artefact to hint it existed. That is the failure mode D1's second archive
exists to prevent.

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
