# OilGuard AI — Repository Capability Audit

Paste this whole file into Claude Code from the repo root.

---

## Your task

Audit this repository against the checklist below. **Verify by reading code and
running things — never by reading documentation, README claims, or file names.**

A file existing is not evidence the capability works. A function named
`calculate_damping_ratio` is not evidence it computes a damping ratio.

For every item, produce one of these verdicts:

- **WORKS** — you ran it or traced the code path end to end and it does what it claims
- **EXISTS-UNVERIFIED** — code is present and plausible, but you could not execute it
  (say exactly what blocked you: missing credentials, missing data, no GPU)
- **STUB** — present but returns hardcoded, placeholder, or trivially fake output
- **MISSING** — not present

**Bias toward the harsher verdict when uncertain.** An honest MISSING is far more
useful to me than an optimistic WORKS. I am making scheduling decisions from your
report; a false positive costs me a week I don't have.

### Rules

1. Read the actual implementation of every function you assess. Do not assume from
   the signature or the docstring.
2. Where I give a concrete check, run it. Report the real output, including errors.
3. If something is hardcoded that should be computed, say so explicitly and quote
   the line.
4. Do not fix anything. Do not write code. This is an audit only.
5. If a claim in a README contradicts what the code does, report the contradiction.

---

# SECTION 1 — Problem statement requirements

These are the five things NTRO asked for. Everything else is supporting.

## 1.1 Detect and characterise the spill

- [ ] Model 1 (screening) weights exist on disk. Report path, file size, format.
- [ ] Model 1 inference runs on a real input. Report the actual output.
- [ ] Model 2 (segmentation) weights exist. Report path, size, format, and
      **which model is currently deployed** (POC-holdout or full-corpus retrain).
- [ ] Model 2 inference runs and produces a binary mask.
- [ ] The two-stage cascade is real: trace the code path and confirm Model 1's
      output actually gates Model 2's input. If Model 2 runs on everything
      regardless, say so.
- [ ] Look-alikes are **reported, not dropped**. Find where they are classified and
      confirm they reach `detect_response.json` with `class: "lookalike"` and a
      `phenomenon` tag.
- [ ] Threshold fallback engine exists and runs with **no weights present**.
      Test: temporarily point the weights path at nothing and confirm the pipeline
      still returns a valid mask with `engine: "threshold_fallback"`.
- [ ] Geometry is computed, not assumed: area_km2, perimeter_km, centroid,
      major/minor axis, orientation_deg. **Read the implementation of each.**
      Confirm orientation is 0=N clockwise in [0,180).
- [ ] Damping ratio is computed from actual pixel statistics (mean sea dB minus
      mean slick dB), not a constant.
- [ ] Age estimate exists and carries an honest low `age_confidence`.

## 1.2 Hindcast to origin

- [ ] Drift code exists and runs. Which implementation — Euler fallback, OpenDrift,
      or both?
- [ ] The integration actually steps **backwards** in time. Show me the line where
      the timestep is negated.
- [ ] Windage is applied and is configurable. Report the value used.
- [ ] Diffusion is a real random walk, not a fixed spread. Show the code.
- [ ] Particles are perturbed independently — confirm the ensemble is not N copies
      of one trajectory.
- [ ] Confidence ellipses are **fitted to particle positions**, not drawn at a
      preset size. This is a common fake — check carefully.
- [ ] `origin_window_start_utc` / `end_utc` are derived from the particle cloud,
      not hardcoded.
- [ ] Velocity fields are sampled at each particle's current position and time
      (4-D interpolation), not sampled once at the slick centroid.

## 1.3 Forecast

- [ ] Forward run exists at +6/+12/+24 h.
- [ ] Contours at 50% and 90%.
- [ ] Forecast area **grows** with horizon. Run it and report the three areas.

## 1.4 Attribution

- [ ] Spatial gate: track ∩ buffered origin cloud. Read the implementation.
- [ ] Temporal gate: presence within the origin window.
- [ ] `filtered_out` is populated **with a real per-vessel reason**, not a generic
      string.
- [ ] All six sub-scores are computed from data: proximity, temporal, trajectory,
      behaviour, ais_gap, vessel_prior. **Read each one.** Report any that return a
      constant, or that are computed but never used.
- [ ] Weights load from `config/attribution_weights.yaml` and sum to 1.0.
- [ ] `total_score` equals the weighted sum. Recompute it yourself from a real
      `suspects.json` and confirm the arithmetic.
- [ ] The `reason` string is **generated from the evidence**, not a template with
      the vessel name substituted in. Show me the generating code.
- [ ] `evidence` numbers (closest_approach_km, ais_gap_minutes, course_delta_deg,
      time_in_origin_window_min, min_sog_kn) are computed from the track.

## 1.5 Visual interface

- [ ] UI builds and runs. Report the command and whether it succeeded.
- [ ] Layers present: SAR scene, slick, look-alikes, origin cloud, confidence
      ellipses, forecast, AIS tracks.
- [ ] Suspects panel renders: ranked list, six factor bars, weights displayed,
      reason, evidence numbers.
- [ ] `filtered_out` vessels are shown with their reasons.
- [ ] Provenance badges (REAL / CACHED / SYNTHETIC) are driven by the `source`
      field in the data, not hardcoded in the component.
- [ ] Time slider animates the hindcast by `step_index`.

---

# SECTION 2 — Real data path

This is the section I most expect to fail. Be strict.

## 2.1 Sentinel-1 `.SAFE` processing

- [ ] Is there code that ingests a raw `.SAFE` product? Locate it.
- [ ] Which of these nine steps are implemented? Report each individually:
      orbit file application · GRD border noise removal · thermal noise removal ·
      radiometric calibration (per-pixel LUT from annotation XML) · speckle filter ·
      terrain correction / geocoding · linear→dB · land mask · subset + dB clip
- [ ] **Critical:** is calibration a per-pixel LUT read from
      `annotation/calibration/*.xml`, or a scalar multiplier? Quote the code.
- [ ] Is there a land mask? If not, say so plainly — a coastal scene will produce a
      city-shaped slick without one.
- [ ] Has any run in the run history actually used a full `.SAFE` scene, or do all
      runs use Trujillo chips / the mock scene? **Check the run manifests and
      report the counts.**
- [ ] Does the code handle a ~25000×16000 raster with windowed reads, or does it
      call `src.read()` on the whole array?

## 2.2 Normalisation consistency

- [ ] Locate `config/normalisation.yaml`. Report its contents.
- [ ] Confirm **both** the training pipeline and the inference path read this same
      file. If they have separate constants anywhere, that is a critical finding —
      report the file and line.
- [ ] Has the real-scene dB histogram ever been compared against this clip range?
      Is there a script or a recorded result?

## 2.3 Met-ocean

- [ ] Do the adapters select **reanalysis vs forecast products by scene date**, or
      is one product hardcoded? For a 2017 scene, CMEMS GLORYS is correct and the
      forecast product returns nothing.
- [ ] Is there a coverage check that the NetCDF time axis spans the full backtrack
      window before drift runs? If not, the hindcast silently extrapolates.
- [ ] Are `currents.nc` / `wind.nc` variable names exactly `uo`,`vo` / `u10`,`v10`
      with dims `(time, lat, lon)`?
- [ ] Report the temporal resolution of what is actually fetched.

## 2.4 AIS

- [ ] Does `mc_ingest.py` (MarineCadastre) exist and has it ever been run on a real
      file? Look for evidence in the data directory or run history.
- [ ] Does `dma_ingest.py` exist and has it been run?
- [ ] Is the AIS store **spatially indexed** (PostGIS GiST, or partitioned parquet),
      or does attribution do a full scan? Show the query.
- [ ] Does the synthetic generator produce **varied** tracks — turns, speed changes,
      lane following — or constant bearing and constant speed? Read the code.
- [ ] Does it generate **hard negatives**: innocent vessels with natural AIS gaps,
      innocent vessels that legitimately slow down, more than one plausible suspect?
- [ ] Does `benchmark.py` exist, does it run 50 scenarios, and **has it ever
      produced a top-1 accuracy number**? If a result exists, report it. If it has
      never been run, say so.

---

# SECTION 3 — System integrity

## 3.1 Contracts

- [ ] Run `pytest contracts/tests -q`. Report the exact result.
- [ ] Are contracts validated at runtime on stage boundaries, or only in tests?
      Find the call sites.
- [ ] Do all real engine outputs validate against their schemas? Take the most
      recent real run's artefacts and validate each one. Report failures.

## 3.2 Orchestration

- [ ] Locate the run state machine. List the states it implements.
- [ ] Are stages **idempotent** — is there an idempotency key or content hash? Show it.
- [ ] Are retries implemented? What happens on a stage failure?
- [ ] Does the manifest record provenance per stage: provider used, fallback level,
      model version, parameters?
- [ ] Count the runs on disk. How many completed all stages? How many used real
      scenes vs chips vs mocks? Report a breakdown.

## 3.3 Degradation

For each chain, confirm the fallback is **wired**, not merely written:

- [ ] Scene: CDSE → ASF → LocalCache
- [ ] Currents: CMEMS → HYCOM → StaticCache
- [ ] Wind: ERA5 → Open-Meteo → StaticCache
- [ ] AIS: real → synthetic
- [ ] Detection: ML → threshold_fallback
- [ ] Drift: OpenDrift → Euler

Then test at least one for real: break a provider (bad credential or blocked host)
and report whether the chain handed over and whether `provider_status.json` reflected
it.

- [ ] Is there a Monitoring page in the UI, and does it read real
      `provider_status.json` files?

## 3.4 Deployment

- [ ] Dockerfile(s)? docker-compose.yml? Report presence.
- [ ] `/healthz` / `/readyz` endpoints?
- [ ] Are weights in git? Check `git ls-files` for `*.pt`, `*.pth`, `*.onnx` and
      report any tracked model files with sizes.
- [ ] Are secrets in git? Check for committed `.env`, keys, or tokens. **Report
      immediately and prominently if found.**
- [ ] Is there an ONNX export, and has CPU inference been benchmarked?
- [ ] Scheduler / AOI watcher — present?

---

# SECTION 4 — Honesty checks

These are where a system quietly lies. Look specifically.

- [ ] Is anything labelled REAL that is actually synthetic or mock? Trace the
      `source` field from generation to UI.
- [ ] Are there hardcoded results anywhere in the pipeline — a fixed suspect list,
      a fixed origin, a fixed confidence, a demo-mode branch that shortcuts real
      computation? **Search for this specifically.**
- [ ] Does any evaluation report **pixel accuracy** for segmentation? It should
      report IoU, precision, recall. Report any accuracy metric you find.
- [ ] Are metrics stored from real evaluation runs, or written into a slide/README
      by hand? Find the source of every number quoted anywhere in the repo.
- [ ] Does any code path produce a **single point** origin rather than a cloud?
- [ ] Is super-resolution applied anywhere in the measurement path (area, perimeter,
      axes, orientation, centroid)?

---

# Report format

```
## VERDICT SUMMARY
WORKS: n    EXISTS-UNVERIFIED: n    STUB: n    MISSING: n

## CRITICAL FINDINGS
Things that would break the demo, invalidate a claim, or that I am currently
assuming work when they do not. Ordered by severity.

## SECTION-BY-SECTION
Each checklist item, verdict, and the evidence — file:line, command run, actual
output. Quote code where a verdict is STUB or where behaviour contradicts a claim.

## WHAT I COULD NOT VERIFY
Each item, and exactly what blocked it.

## THE FIVE THINGS TO FIX FIRST
Ordered by impact on delivering the problem statement's five requirements.
```

End with a plain-language paragraph: if I demoed this repository tomorrow to
NTRO evaluators, what would break, and what claim would not survive a hard question?
