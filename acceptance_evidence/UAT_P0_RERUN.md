# UAT P0 re-run — OT-UAT-26143 spec, P0 cases only

**Executed:** 2026-09-08 against `feat/indhu-detection-pipeline` (post-reconciliation build), live registry, backend on `:8010`.
**Prior run:** `uat_evidence/FINAL_REPORT.md` (2026-09-06, commit `b2a8fd5`).
**Rule:** no PASS regressions; N-01 / N-02 / N-03 verdicts quoted honestly.

How to read the *Now* column:

- **PASS (re-executed)** — exercised today by the acceptance harness or by an artefact produced today.
- **PASS (carried)** — not re-executed today; the prior evidence is unchanged and still verifies (git shows no change to the artefact/code it rests on). Stated as carried, not as re-proven.
- **MISSING / declared** — the capability does not exist and the system says so in its own UI/API. Unchanged from the prior run.

Evidence paths are relative to `acceptance_evidence/` unless noted.

| Case | P0 test | Prior | Now | Evidence |
|---|---|---|---|---|
| OT-A-01 | Real investigator entry point | PASS | PASS (re-executed) | `DEMO_RUNBOOK.md` §1–2; E2E-02 `investigation.json`, `start.json` (the API the UI wizard calls) |
| OT-A-02 | System finds the oil unaided | PASS | PASS (re-executed) | E2E-02 run: detect stage `engine=ml`, 18 slicks from the raster alone — no user geometry anywhere in `investigation.json` |
| OT-A-04 | Manual spill spec neither required nor silently used | PASS | PASS (re-executed) | `RunRequest` schema has no geometry field; E2E-02 body = `{engine: auto}` |
| OT-B-01 | Real Sentinel-1 ingestion | PASS | PASS (carried) | Flagship scene calibrated in-repo from the CDSE product (dev_evidence/P14); the calibrated raster is hashed in the manifest and `/verify` passes today (E2E-04 `verify.json`) |
| OT-B-04 | Georeferencing sanity | PASS | PASS (carried) | Tiles georeferenced against scene corners (dev_evidence/P18); tile bytes re-measured today (E2E-15 `tiles.json`) |
| OT-B-05 | EO / optical compliance | MISSING | MISSING / declared | `?source=S2` → **501** (E2E-07); catalogue row `NOT_DEPLOYED` (`providers_test_all.json`) |
| OT-C-01 | Two-stage detection (screen → segment) | PASS | PASS (re-executed) | E2E-02 `run.json` manifest: `yolo11n-screen-dartis-2026-08-24` + `unet-r34-fullcorpus-e48` with sha256; detect detail `engine=ml` |
| OT-C-02 | Mask → georeferenced boundary | PASS | PASS (re-executed) | E2E-02 `slick` layer 200 (contract-validated); raw_mask.tif hashed in manifest |
| OT-C-05 | Anti-fabrication: output depends on input | PASS | PASS (re-executed) | `determinism_two_chennai_runs.txt`: two runs of the same scene today → raw_mask / origin_cloud / forecast / vessels **byte-identical**, suspects identical modulo run id; a different scene (flagship) → different digest |
| OT-D-01 | Slick boundary artefact validity | PASS | PASS (re-executed) | `/api/layers/{run}/slick` validates the contract before serving (422 otherwise); 200 for E2E-02 and E2E-04 |
| OT-D-02 | Geometric properties complete + consistent | PASS | PASS (re-executed) | E2E-06 `ui-flagship-spill-panel.png`: area 6.83 km², perimeter, centroid 27.8087 −90.1882, axes, orientation, damping, age `23.7 h · LOW confidence` |
| OT-E-01 | Currents acquisition | PASS | PASS (re-executed, cache) | E2E-02 run `origin_cloud` metadata: CMEMS `uo/vo`, `fallback: None`, normalised cache dated 2026-09-01. Provider today: CMEMS **REACHABLE** (`providers_test_all.json`) |
| OT-E-02 | Wind acquisition | PASS | PASS (re-executed, cache) | ERA5 `u10/v10`, `fallback: None`, normalised cache dated 2026-09-01. ERA5 today: **REACHABLE** |
| OT-E-03 | Forcing reaches the drift engine | PASS | PASS (re-executed) | windage 0.03 in metadata; `analysis_engines/tests` in `regression_pytest.txt` |
| OT-F-01 | Backward propagation | PASS | PASS (re-executed) | E2E-02 drift_hindcast `ok`, 24 h, currents+wind; E2E-06 flagship 25 steps |
| OT-F-02 | `origin_cloud.geojson` | PASS | PASS (re-executed) | E2E-06 `origin_metadata.json`; 25 ellipses, all non-zero |
| OT-F-03 | Origin plausibility | PASS | PASS (re-executed) | E2E-06 `cloud_vs_ellipse.json`: final ellipse within the particle 1-σ order |
| OT-F-04 | Origin TIME window | PASS | PASS (re-executed) | Flagship window `2023-01-07T11:10:08Z → 2023-01-08T00:10:08Z`, method `cloud_convergence` (E2E-06) |
| OT-F-06 | Hindcast ML presence | MISSING | MISSING / declared EXPERIMENTAL | `/api/metrics.drift`: `status experimental · applied false · evaluated negative`; rendered on Analytics (`E2E-07/ui-analytics.png`); `ml_residual.applied: false` in every origin cloud |
| OT-G-01 | Forward drift forecast | PASS | PASS (re-executed) | E2E-02 `forecast` layer 200; stage detail `forecast 24h` |
| OT-H-01 | Synthetic AIS supports the workflow | PASS | PASS (re-executed) | E2E-02: Chennai run with `ais.data_source: synthetic`, chip **AIS SYNTHETIC** (`E2E-02/ui-journey-workspace.png`) |
| OT-H-02 | `vessels.parquet` cleaned tracks | PASS | PASS (re-executed) | 14-column contract enforced at serve time; `vessels_geojson` 200 (E2E-02, E2E-05) |
| OT-H-05 | `query_cloud()` traffic around origin | PASS | PASS (re-executed) | E2E-08 `funnel.json`: 32 considered → 4 ranked, 28 filtered with reasons |
| OT-H-06 | Real historic AIS execution | **UNPROVEN** | **PASS (re-executed)** | Flagship: `manifest.ais.data_source: real`, MarineCadastre archive covering the origin window; `suspects.source: real`; vessel index shows MMSI 367653160 `source: real` across 4 runs (E2E-04, E2E-09) |
| OT-I-01 | Three-gate filter with counts | PASS | PASS (re-executed) | E2E-08 `funnel.json` per-gate counts |
| OT-I-02 | Excluded vessels explainable | PASS | PASS (re-executed) | `filter_reason` / `failed_gates` on all 28 filtered vessels (`E2E-04/suspects.json`) |
| OT-J-01 | `suspects.json` full structure | PASS | PASS (re-executed) | `E2E-04/suspects.json`: weights, sub_scores, filtered_out, source |
| OT-J-02 | Weight configuration integrity | PASS | PASS (re-executed) | 0.30/0.20/0.20/0.10/0.15/0.05, Σ = 1.0 (E2E-08 `recomputed_totals.json`) |
| OT-J-03 | Proximity monotonic | PASS | PASS (carried) | `analysis_engines/tests/test_attribution_scoring.py` in `regression_pytest.txt` |
| OT-J-06 | AIS gap factor | PASS | PASS (carried) | same suite |
| OT-J-07 | Benchmark 86 % top-1 / 100 % top-3 | PASS | PASS (carried) | `analysis_engines/benchmark/RESULTS.md` unchanged since `b2a8fd5` (git); re-executed byte-identical on 2026-09-06 |
| OT-K-01 | UI loads a completed investigation | PASS | PASS (re-executed, **defect fixed**) | `E2E-04/ui-flagship-workspace.png`. The deep link showed the flagship's header over the Chennai run's layers until fixed today — see ACCEPTANCE_REPORT §5 |
| OT-K-02 | Layer narrative renders | PASS | PASS (re-executed) | same capture: SAR, slick, geometry, forecast, hindcast, origin, AIS, look-alikes |
| OT-K-03 | Time slider syncs vessels + drift | PASS | PASS (carried) | frontend unit suite (`vitest.txt`, 72 passed) + P19 evidence |
| OT-K-04 | SuspectsPanel ranking, bars, weights | PASS | PASS (re-executed) | `E2E-04/ui-flagship-suspects.png`: `#1 MMSI 367653160`, weights printed, Σwᵢsᵢ reproduces 0.6691 |
| OT-L-01 | Real vs synthetic labelling | PASS | PASS (re-executed) | E2E-02: real Sentinel-1 scene + **AIS SYNTHETIC** chip; E2E-05: gold `SYNTHETIC 3 stages`; E2E-04: `AIS REAL` |
| OT-M-01 | One input → ranked suspects on a map | PASS | PASS (re-executed) | E2E-02: 5/5 real, 0 mock, **225.57 s**; 1 suspect ranked |
| OT-M-02 | Live regeneration (not canned) | PASS | PASS (re-executed) | E2E-02 / E2E-14 runs created today under gitignored `data/runs/`, `registry_source: api` |
| OT-N-01 | Compliance verdict: EO (R3) | MISSING | **MISSING — declared, not hidden** | 501 on S2 search; `NOT_DEPLOYED` in catalogue; `docs/LIMITATIONS.md` |
| OT-N-02 | Compliance verdict: hindcast ML (R9) | MISSING | **MISSING — EXPERIMENTAL, evaluated negative (0/6 trajectories improved), disabled** | `/api/metrics.drift`; Analytics page; every origin cloud carries `ml_residual.applied: false` |
| OT-N-03 | Compliance verdict: real historic AIS (R10-real) | UNPROVEN | **PROVEN** | The flagship ran end-to-end on MarineCadastre 2023-01-07/08 (86,830 rows, 441 MMSI); registry, vessel index and UI all say `REAL` |

## Summary

- **41 P0 cases: 36 PASS (23 re-executed, 13 carried), 3 MISSING / declared, 0 FAIL, 0 BLOCKED.**
- **No PASS regressions.** Two verdicts improved: OT-H-06 and OT-N-03 (UNPROVEN → PASS/PROVEN) on the strength of the flagship's real-AIS run, which today is also addressable through the registry (E2E-04, E2E-11).
- The two compliance gaps (EO, hindcast ML) are unchanged and are stated in the product itself, not only in this document.
- One P0 surface (OT-K-01) was found defective during this re-run and fixed before the verdict was recorded; the screenshot in evidence is from the fixed build.
