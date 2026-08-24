# TASKS — Module 2: Nandha Engines

**Owner:** Nandha Kumar · **Scope:** `2_nandha_engines/` only — Engine A (Characterisation), Engine B (Drift), Engine C (Attribution).
**Source of truth:** `docs/PS26143_Handbook_2_Nandha_Kumar.md` (§4 contracts, §6 build plan, §7 frozen CLIs, §8 tests, §9 pitfalls).
**Not mine:** ML/detection, external API fetching, UI, DB, monitoring pages, final integration, the shared `contracts/` folder.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **P0** blocks everything else · **P1** core deliverable · **P2** polish/stretch.

---

## Phase 0 — Scaffolding & environments

- [x] **P0** Fix `config/attribution_weights.yaml` to the frozen 6 factors: `proximity 0.30, temporal 0.20, trajectory 0.20, anomaly 0.10, ais_gap 0.15, prior 0.05` (current keys `drift_distance/temporal_alignment/vessel_history` are off-contract).
- [x] **P0** Expand `requirements.txt` (Env 1, plain venv): numpy, scipy, shapely, rasterio, scikit-image, geopandas, pyproj, xarray, netcdf4, pandas, pyarrow, pydantic, pytest, PyYAML.
- [x] **P0** Create the Env 1 venv, install, verify every import.
- [x] **P0** Restructure to the frozen CLI shape (handbook §7): `engines/characterise/__main__.py`, `engines/drift/__main__.py`, `engines/attribution/__main__.py` so `python -m engines.<x>` works. Decide the fate of `cli.py` (keep as a thin wrapper or delete).
- [x] **P0** Add `engines/common/` for shared helpers: geo conversions (km↔deg with the latitude cosine), UTC parse/format (`Z` suffix), status-object builder, error classes.
- [x] **P0** Define the status object `{ok, engine_used, warnings[]}` and error classes `MISSING_INPUT`, `BAD_GRID`, `EMPTY_MASK`, `NO_VESSELS_IN_WINDOW`. Structured returns, never raise to the caller.
- [~] **P1** Pydantic schemas for all four outputs (`slick.geojson`, `origin_cloud.geojson`, `forecast.geojson`, `suspects.json`) in `engines/schemas/`, plus a `validate()` called by every writer.
- [ ] **P1** Env 2 (conda, OpenDrift) — **riskiest install in the project, do it early**: `conda create -n drift python=3.11`, `conda install -c conda-forge opendrift`, smoke-test `import opendrift`, then `conda env export > environment.yml`. The current `environment.yml` is hand-written and untested — replace it with a real export.
- [ ] **P2** Dockerfile (conda base) for the OpenDrift environment.
- [ ] **P2** Escalation rule: if the conda/GDAL chain fights back for more than half a day, tell the team and proceed on the Euler fallback — the project must not stall on GDAL.
- [x] **P1** `tests/` package + pytest config; one-command test run documented in the README.

## Phase 0b — Mock inputs (unblocks everything; do not wait for teammates)

- [x] **P0** Hand-drawn slick mask: 0/1 GeoTIFF with known CRS + pixel size, plus a dB backscatter band for the damping ratio.
- [x] **P0** `scene_meta.json` mock (scene_id, acquisition UTC, bbox, CRS, dB range).
- [x] **P0** Synthetic **uniform** current NetCDF + wind NetCDF — analytic ground truth: with a constant current the backtracked origin is hand-computable.
- [x] **P1** Synthetic **rotating / sheared** current field NetCDF (the harder case).
- [ ] **P1** Synthetic `vessels.parquet` with exactly the contract columns and **one planted culprit** (passes the origin region in-window, slows down, has an AIS gap).
- [~] **P1** Deliberately broken fixtures for the failure tests: empty mask, NetCDF missing `u`/`v`, zero vessels in window.
- [~] **P1** Mock-generator scripts (seeded, reproducible) under `tests/fixtures/`.

---

## Engine A — Characterisation (Phase 1)

- [x] **P1** Read the mask GeoTIFF with rasterio; read CRS + pixel size; assert EPSG:4326 at the boundary (convert only at ingest).
- [x] **P1** Label connected components; drop specks below a configurable min-area threshold.
- [x] **P1** Per component via `regionprops`: area (px→km² using the pixel size **at that latitude**), perimeter km, centroid lat/lon, ellipse major/minor axis km, orientation deg.
- [x] **P1** Polygonise the mask boundary (shapely) → WGS84 GeoJSON Polygon.
- [x] **P1** Damping ratio: mean dB inside the mask vs mean dB in an outside ring buffer → `damping_ratio_db`.
- [x] **P1** Fay spreading-law age estimate from area → `age_hours_est`, `age_method: "damping+fay"`, `age_confidence: "low"`; document every assumption.
- [x] **P1** Write `slick.geojson` exactly per handbook §4.2 (slick_id, scene_id, detected_utc, confidence, area_km2, perimeter_km, centroid, major/minor_axis_km, orientation_deg, damping_ratio_db, age fields); validate against the schema.
- [x] **P1** Handle multiple slicks in one scene (one Feature each, stable `slick_id` numbering).
- [x] **P1** Return `EMPTY_MASK` when nothing survives thresholding.
- [x] **P1** CLI: `python -m engines.characterise --mask <tif> --scene-meta <json> --out slick.geojson`.
- [x] **P1** **Test:** known-shape — a drawn ellipse returns its own axes/area within tolerance.
- [x] **P1** **Test:** empty mask → `EMPTY_MASK`, never a crash.
- [ ] **P2** Extra shape descriptors (eccentricity, solidity, elongation) if the UI wants them.

## Engine B — Drift, Euler fallback (Phase 2) — **WRITE THIS FIRST**

- [x] **P0** Rewrite `engines/drift/euler_fallback.py`: the current version is a single particle with constant scalar fields — it needs particles plus gridded fields.
- [x] **P1** Seed N particles uniformly inside the slick polygon (configurable N, seeded RNG).
- [x] **P1** Open `currents.nc` / `wind.nc` with xarray; validate the expected variables (`u`,`v` / `u10`,`v10`) and that the grid covers the slick bbox ± margin → else `BAD_GRID`.
- [x] **P1** Bilinear interpolation of both grids in **space and time**.
- [x] **P1** Step: `v = current(x,t) + 0.03 * wind10(x,t)`; keep the u/v sign conventions straight (eastward/northward positive); backward = negative dt.
- [x] **P1** Gaussian diffusion added per step (configurable coefficient).
- [x] **P1** Correct metre→degree conversion per step using the latitude cosine — never treat degrees as metres.
- [x] **P1** Per-timestep weighted particle cloud; fit a covariance/confidence ellipse per timestep (90% level).
- [x] **P1** Derive the origin window: where and when cloud density peaks over the 12–24 h backward run → `start_utc`, `end_utc`, `peak_utc`.
- [x] **P1** `origin_cloud.geojson` writer per §4.3: particle Points `{time_utc, weight, timestep_h}` + one `confidence_ellipse` Polygon per timestep + the `origin_window` summary Point carrying `engine_used`.
- [x] **P1** Never emit a single origin point — always cloud + ellipse + time window.
- [x] **P1** CLI: `python -m engines.drift --slick ... --currents ... --wind ... --mode hindcast|forecast --hours 24 --out <geojson>`.
- [x] **P1** **Test (analytic):** constant current field → the backtracked origin equals the hand-computed point.
- [x] **P1** **Test (round-trip):** forward then backward returns near the start.
- [x] **P1** **Test:** missing NetCDF variable → `BAD_GRID`; missing file → `MISSING_INPUT`.
- [ ] **P2** Optional Stokes-drift term if Keerthana supplies it.

## Engine B — OpenDrift path (Phase 3)

- [ ] **P1** Wrap `OceanDrift` (fewer deps) behind the **same function signature** as the Euler fallback; readers = the two NetCDFs.
- [ ] **P1** Wrap `OpenOil` behind that same signature.
- [ ] **P1** Backward run via negative timestep; export particles through the **same** `origin_cloud.geojson` writer.
- [ ] **P1** Runtime selection order: OpenOil → OceanDrift → Euler; record `engine_used` in the status object and in the output.
- [ ] **P1** **Test:** Euler matches OpenDrift direction on the same field (fallback-agreement test).
- [ ] **P2** Oil-type / weathering parameters for OpenOil (document the defaults).

## Engine B — Forecast (Phase 4)

- [ ] **P1** Forward run from the detected slick at +6 / +12 / +24 h.
- [ ] **P1** Concave- or convex-hull predicted extent Polygon per horizon.
- [ ] **P1** Uncertainty ellipse growing over time → `uncertainty_growth` property.
- [ ] **P1** `forecast.geojson` writer per §4.3 (`horizon_h: 6|12|24`); validate against the schema.

## Engine C — Filtering gates (Phase 5)

- [ ] **P1** Load `vessels.parquet`; code against the **contract columns** (`mmsi, timestamp, lat, lon, sog_kn, cog_deg, heading_deg, vessel_name, imo, vessel_type, length_m, width_m, draft_m, status, source, culprit`), not a sample file's accidents.
- [ ] **P1** Assemble per-MMSI tracks; assert UTC; assert EPSG:4326.
- [ ] **P1** Spatial gate: track intersects the buffered high-probability origin region.
- [ ] **P1** Temporal gate: presence within the origin window ± buffer.
- [ ] **P1** Trajectory gate: course roughly compatible with the slick major axis (discharge trails behind a moving vessel).
- [ ] **P1** Excluded vessels stay in the output with `filtered: true` + `filter_reason` (UI shows "filtered out: outside time window").
- [ ] **P1** `NO_VESSELS_IN_WINDOW` returned structured — a valid, expected outcome, not a bug.
- [ ] **P1** **Test:** a vessel outside the time window is filtered with the reason recorded.

## Engine C — Scoring, explanation, ranking (Phase 6)

- [ ] **P1** Factor `proximity` — depth of the track inside the origin cloud, weighted by cloud density.
- [ ] **P1** Factor `temporal` — alignment between presence and the estimated discharge window.
- [ ] **P1** Factor `trajectory` — angle vs the slick major axis + path-overlap length.
- [ ] **P1** Factor `anomaly` — unusual slowdown, course change, loitering (rule-based).
- [ ] **P1** Factor `ais_gap` — transmission blackout overlapping the origin window (strong suspicion signal).
- [ ] **P1** Factor `prior` — vessel type/draft prior (tanker, bulk carrier > passenger ferry).
- [ ] **P1** Normalise every factor to 0–1; total = weighted sum with weights loaded from `config/attribution_weights.yaml`.
- [ ] **P1** Rank descending; assign `rank`; emit the per-factor `scores` breakdown.
- [ ] **P1** Explanation generator: template over the factor evidence → one plain-language sentence per vessel (see §4.4 example).
- [ ] **P1** `suspects.json` writer per §4.4 (`investigation_id`, `generated_utc`, `weights`, `vessels[]` including the filtered ones); validate against the schema.
- [ ] **P1** CLI: `python -m engines.attribution --origin ... --vessels ... --weights config/attribution_weights.yaml --out suspects.json`.
- [ ] **P1** **Test:** the planted culprit ranks top-1 on the synthetic scenario.
- [ ] **P1** Keep it explainable — **no trained classifier** (no ground truth exists; explainability is required by the use case).
- [ ] **P2** Weight-sensitivity check: results stay sane when the weights are re-tuned.

## Phase 7 — Benchmark

- [ ] **P1** Run Engine C over Krishnan's 50 seeded scenarios (each has a known culprit).
- [ ] **P1** Report the top-1 / top-3 hit rate — this number goes on the metrics slide.
- [ ] **P1** Commit the benchmark script + results table to `docs/`.
- [ ] **P2** Error analysis: which scenarios miss, and which factor is responsible.

## Phase 8 — Docs, failure tests, handover

- [ ] **P1** README per engine: purpose, inputs, outputs, one-command run, known issues.
- [ ] **P1** Update the module `README.md` (the current one describes the empty skeleton).
- [ ] **P1** Commit sample inputs **and** sample outputs for all four contract files.
- [ ] **P1** All four outputs validate against the Pydantic schemas (test).
- [ ] **P1** Full failure-case suite green: `MISSING_INPUT`, `BAD_GRID`, `EMPTY_MASK`, `NO_VESSELS_IN_WINDOW` — never a crash.
- [ ] **P1** One-command run per engine against the mocks.
- [ ] **P1** Known-issues list.
- [ ] **P1** 30-minute walkthrough with Indhu.
- [ ] **P1** Common handover checklist (Part G): runnable code · input schema · output schema · example inputs · example outputs · test results · error behaviour per class · README · run instructions · integration instructions.

---

## Coordination — only two real touch-points (everything else flows through files)

- [ ] **P0** Agree the NetCDF variable names with **Keerthana** ONCE, in writing — her tiny hand-built NetCDF doubles as my mock.
- [ ] **P1** Confirm the `vessels.parquet` columns with **Krishnan** once (already fixed by contract).
- [ ] **P1** Raise with **Indhu**: `contracts/schemas/` currently holds only `sar_detection.json` + `vessel_attribution.json`, whose field names do not match the handbook's `slick.geojson` / `suspects.json`. His folder to fix — I code to handbook §4.2–4.4 in the meantime.

## Standing rules (apply to every task above)

- WGS84 (EPSG:4326) everywhere; convert only at ingest boundaries, one assert per boundary.
- UTC everywhere with the `Z` suffix — never let IST (UTC+05:30) leak into the data.
- Contract is law; changing a frozen contract needs team sign-off.
- Structured errors, never crashes.
- Drift output always shows uncertainty; synthetic data is labelled; no metric the system cannot back.
- Freeze rule: once a component is integrated and green, no edits without Indhu's sign-off.

## Pitfalls checklist (handbook §9 — re-read before each phase)

1. The OpenDrift GDAL/cartopy chain is the riskiest install — conda/Docker only, never pip on system Python.
2. Euler fallback FIRST — it is the dev harness, the guaranteed demo path, and the OpenDrift sanity check.
3. Degrees are not metres; pixel area varies with latitude.
4. Backward drift = negative timestep; leeway = 3% of the 10 m wind; keep the u/v signs straight.
5. Never output a single origin point.
6. NetCDF variable names agreed with Keerthana in writing, once.
7. The parquet columns are fixed by contract — code against them, not a sample file.
8. Attribution is deliberately not a trained classifier.
9. `NO_VESSELS_IN_WINDOW` is a valid, expected outcome.
