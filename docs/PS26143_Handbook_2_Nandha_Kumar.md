# OceanTrace — Personal Developer Handbook

## Developer 2 of 5 — NANDHA KUMAR

**Role:** Core Engines Developer — Characterisation · Drift · Attribution (~40% of core work)

| | |
|---|---|
| Event | SIH 2026 · Problem Statement 26143 (NTRO) |
| Title | Leveraging satellite imagery to determine oil spills at sea with AIS correlation to identify the responsible vessel |
| Category / Theme | Software / Space Technology |
| Project codename | OceanTrace |
| Companion docs | `PS26143_System_Specification` · `PS26143_Team_Split_Handbook` |
| Datasets locked | DARTIS + Trujillo (Zenodo Sentinel-1) |
| Tooling | Claude Code as primary development assistant |

> **How to use this handbook.** This is your single working document. You own the science between the detection mask and the UI: three pure file-in/file-out engines. Everything you need is here — the exact contracts with examples, the physics and maths you implement, a phased build plan, analytic test cases, and the handover rules. No network, no GPU, no teammate is required for any of your work.

**Contents:** 1 Project in one page · 2 Your mission · 3 Ground rules · 4 Contracts · 5 Day-0 setup · 6 Build plan · 7 Frozen interfaces · 8 Testing · 9 Pitfalls · 10 Integration & handover

---

## 1. The Project in One Page

### 1.1 What the finished system does

The system takes a Sentinel-1 SAR satellite scene, detects and characterises any oil slick in it, uses ocean-current and wind data to trace the slick backwards to its probable origin point and time (hindcast) and forwards to its future spread (forecast), reconstructs historic AIS vessel traffic around that origin window, filters irrelevant vessels, scores the remaining candidates (proximity, temporal and trajectory correlation, behaviour anomalies, AIS gaps, vessel prior), and presents a ranked, explainable suspect list on a GIS investigation interface. API Monitoring and Key Management pages make external dependencies observable; every stage has a fallback and the last fallback in every chain is dependency-free.

### 1.2 Full pipeline and ownership

```
Sentinel-1 scene acquisition ............ Pavitra
        ↓  scene_meta.json + calibrated GeoTIFF
Oil-spill detection (2-stage ML) ........ Indhu
        ↓  raw mask
Characterisation (Engine A) ............. NANDHA  ◄◄ you
        ↓  slick.geojson       ◄── currents.nc + wind.nc .. Keerthana
Drift hindcast + forecast (Engine B) .... NANDHA  ◄◄ you
        ↓  origin_cloud.geojson ◄── vessels.parquet ....... Krishnan
Filter + Score + Rank (Engine C) ........ NANDHA  ◄◄ you
        ↓  suspects.json
GIS UI + Monitoring + Integration ....... Indhu
```

### 1.3 Your modules highlighted

```
Scene (Pavitra) → Detection ML (Indhu)
        ↓ raw mask
╔══════════════════════════════════════════╗
║ 3. CHARACTERISATION  (Engine A)          ║ ◄◄◄ YOUR MODULE
╚══════════════════════════════════════════╝
        ↓ slick.geojson      ◄── currents.nc / wind.nc (Keerthana)
╔══════════════════════════════════════════╗
║ 5. HINDCAST + FORECAST (Engine B)        ║ ◄◄◄ YOUR MODULE
║    → PROBABLE ORIGIN                     ║
╚══════════════════════════════════════════╝
        ↓ origin_cloud.geojson ◄── vessels.parquet (Krishnan)
╔══════════════════════════════════════════╗
║ 7–9. FILTER + SCORE + RANK (Engine C)    ║ ◄◄◄ YOUR MODULE
╚══════════════════════════════════════════╝
        ↓ suspects.json
GIS UI + Integration (Indhu) → COMPLETE SYSTEM
```

## 2. Your Mission

### 2.1 Components owned — three engines, each contract-in/contract-out

- **Engine A — Characterisation (deterministic, no ML).** Raw mask → `slick.geojson`: area (km²), perimeter, centroid (lat/lon), best-fit ellipse (major/minor axis, orientation), shape descriptors, polygon boundary; age proxy from backscatter damping ratio (slick vs surrounding sea) + Fay spreading-law estimate. Tools: scikit-image `regionprops`, shapely, rasterio.
- **Engine B — Drift (hindcast + forecast).** `slick.geojson` + `currents.nc` + `wind.nc` → `origin_cloud.geojson` (backward 12–24 h) and `forecast.geojson` (+6/+12/+24 h). Primary: OpenDrift `OpenOil` (particles seeded across the slick polygon; backward run = negative timestep). Fallback: OpenDrift `OceanDrift`. Guaranteed path — write it FIRST: in-house ~30-line Euler integrator, velocity = surface current + 3% wind (leeway), backward/forward stepping, Gaussian diffusion per step. Output is always a probability cloud with an uncertainty ellipse, never a single point.
- **Engine C — Attribution (explainable, deliberately NOT a trained classifier).** `origin_cloud.geojson` + `vessels.parquet` → `suspects.json`. Filtering gates (spatial / temporal / trajectory) → weighted scoring (proximity in the cloud weighted by density, temporal correlation with the discharge window, trajectory correlation with the slick major axis + path-overlap, behavioural anomalies, AIS gaps over the origin window, vessel type/draft prior) → ranked list with per-factor breakdown and a generated plain-language reason per vessel. Weights configurable and shown in the UI.

### 2.2 Where it fits

The middle of the pipeline: everything between Indhu's raw mask and the UI's final layers. Engine A feeds B; B feeds C; all three feed the UI.

### 2.3 What you own

Geometry maths; drift physics + the OpenDrift conda/Docker environment (riskiest install in the project — GDAL/cartopy chain; smoke-test it EARLY and commit `environment.yml`); the Euler fallback; filtering gates; scoring weights + explanation generator; engine unit tests and docs.

### 2.4 What you do NOT own

ML models and inference (Indhu). External data fetching (Pavitra, Keerthana, Krishnan). UI, database, monitoring pages, final integration (Indhu).

### 2.5 Why you are never blocked

All three engines are file-in/file-out. Mocks + analytic test fields cover everything; real files replace them at integration with zero code change.

## 3. Ground Rules (identical for all five developers)

| Rule | Meaning |
|---|---|
| Contract is law | If your output matches the schema in `contracts/`, integration works without touching your code. Changes to a frozen contract require team sign-off. |
| WGS84 everywhere | All vector data in EPSG:4326 (lon/lat). Convert only at ingest boundaries; one assert per boundary. |
| UTC everywhere | All timestamps UTC (`Z` suffix). Beware IST = UTC+05:30 — never let local time leak into data. |
| Error taxonomy | Standard classes: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`, plus your own classes in §4.5. Structured errors, never crashes. |
| Mocks first | `contracts/mocks/` exists from day 1 (Indhu creates). Everyone develops against mocks. |
| No blocking | Every developer builds, runs and tests alone. |
| Freeze rule | Once a component is integrated and green, nobody edits it without Indhu's sign-off. |
| Honesty | Drift output always shows uncertainty; synthetic data is labelled; no metric the system cannot back. |

## 4. Contracts — Your Inputs and Outputs

Example values are illustrative (Scene A = Chennai/Ennore 2017 demo region, acquisition 2017-02-02T00:39:42Z); field names and types are the law.

### 4.1 Inputs

| Engine | Input | Format | Required | Optional |
|---|---|---|---|---|
| A | Raw mask | GeoTIFF 0/1 + scene metadata | CRS, pixel size, dB backscatter band (for damping ratio) | look-alike flags |
| B | Slick + met-ocean | `slick.geojson`, `currents.nc` (u/v), `wind.nc` (u10/v10) | lat/lon/time grids covering slick bbox ± margin | Stokes drift |
| C | Origin + vessels | `origin_cloud.geojson`, `vessels.parquet` | particle weights; MMSI/time/lat/lon/SOG/COG | vessel type, dims |

`vessels.parquet` columns (fixed by contract; source and culprit flags always present — culprit meaningful only for synthetic): `mmsi, timestamp(UTC), lat, lon, sog_kn, cog_deg, heading_deg, vessel_name, imo, vessel_type, length_m, width_m, draft_m, status, source, culprit`.

### 4.2 Output — `slick.geojson` (Engine A)

```json
{ "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": { "type": "Polygon",
      "coordinates": [[[80.27,13.01],[80.36,13.02],[80.35,13.09],
                       [80.28,13.08],[80.27,13.01]]] },
    "properties": {
      "slick_id": "DEMO-A_slick_01",
      "scene_id": "S1A_IW_GRDH_20170202T0039_DEMO-A",
      "detected_utc": "2017-02-02T00:39:42Z", "confidence": 0.91,
      "area_km2": 14.6, "perimeter_km": 21.3,
      "centroid": [80.312, 13.052],
      "major_axis_km": 7.9, "minor_axis_km": 2.4,
      "orientation_deg": 62.0,
      "damping_ratio_db": 6.8,
      "age_hours_est": 9.5, "age_method": "damping+fay",
      "age_confidence": "low"
    } }] }
```

### 4.3 Output — `origin_cloud.geojson` and `forecast.geojson` (Engine B)

`origin_cloud.geojson`: particle Point features `{ "time_utc": "...", "weight": 0.0–1.0, "timestep_h": -8 }`, plus one Polygon feature per backward timestep `{ "kind": "confidence_ellipse", "level": 0.9, "timestep_h": -8 }`, plus one summary feature:

```json
{ "type": "Feature",
  "geometry": { "type": "Point", "coordinates": [80.301, 13.048] },
  "properties": { "kind": "origin_window",
    "start_utc": "2017-02-01T14:00:00Z",
    "end_utc":   "2017-02-01T18:00:00Z",
    "peak_utc": "2017-02-01T16:10:00Z", "engine_used": "openoil" } }
```

`forecast.geojson`: one predicted-extent Polygon per horizon with `{ "horizon_h": 6|12|24, "uncertainty_growth": ... }`.

### 4.4 Output — `suspects.json` (Engine C)

```json
{ "investigation_id": "inv-001",
  "generated_utc": "2026-08-24T10:12:00Z",
  "weights": { "proximity": 0.30, "temporal": 0.20, "trajectory": 0.20,
               "anomaly": 0.10, "ais_gap": 0.15, "prior": 0.05 },
  "vessels": [
    { "rank": 1, "mmsi": 419001234, "name": "MV DEMO TRADER",
      "vessel_type": "Tanker",
      "score_total": 0.87,
      "scores": { "proximity": 0.95, "temporal": 0.90,
                  "trajectory": 0.82,
                  "anomaly": 0.70, "ais_gap": 1.00, "prior": 0.80 },
      "filtered": false,
      "reason": "Passed through the 90% origin region at
        2017-02-01 16:04 UTC, slowed from 13.8 to 5.9 kn, and had a
      47-minute AIS gap overlapping the estimated discharge window." },
    { "mmsi": 419009876, "name": "COASTAL FERRY 7", "filtered": true,
      "filter_reason": "outside time window" } ] }
```

### 4.5 Your status object and error classes

Every engine run returns `{ "ok": true|false, "engine_used": "primary|fallback", "warnings": [] }` alongside its file. Error classes: `MISSING_INPUT`, `BAD_GRID` (NetCDF lacks expected variable/coverage), `EMPTY_MASK`, `NO_VESSELS_IN_WINDOW`. Structured errors, never crashes — main system shows a badge and, where defined, retries with your fallback engine.

## 5. Day-0 Setup

### 5.1 Accounts and credentials

None. Your engines never touch a network.

### 5.2 Environment — two environments, on purpose

```
# Env 1 (plain venv, Engines A + C + Euler fallback)
pip install numpy scipy shapely rasterio scikit-image geopandas pyproj \
            xarray netcdf4 pandas pyarrow pydantic pytest

# Env 2 (conda, Engine B primary)
# DO THIS ON DAY 0-1 — riskiest install in the project
conda create -n drift python=3.11
conda install -c conda-forge opendrift
conda env export > environment.yml   # commit; also write Dockerfile
python -c "import opendrift; print(opendrift.__version__)"  # smoke
```

If the conda chain fights back for more than half a day, escalate to the team and proceed on the Euler fallback — the project does not stall on GDAL.

### 5.3 Your folders

```
backend/app/services/characterise/   # Engine A
backend/app/services/drift/          # Engine B
                                     # (euler_fallback.py FIRST)
backend/app/services/attribution/    # Engine C
engines/                             # CLI wrappers — see §7
config/attribution_weights.yaml
contracts/mocks/                     # dev inputs from Indhu (day 1–2)
```

### 5.4 Mock/test inputs

Hand-drawn slick polygon raster; synthetic uniform and rotating current fields (with a constant current the backtracked origin is hand-computable — analytic ground truth); a tiny hand-built NetCDF (variable names agreed ONCE in writing with Keerthana); synthetic `vessels.parquet` with one planted culprit.

## 6. Build Plan — Phase by Phase

| Phase | Deliverable |
|---|---|
| 0 | Both environments up; OpenDrift smoke-tested; `environment.yml` committed |
| 1 | Engine A complete + known-shape test green |
| 2 | Euler fallback integrator + analytic tests green |
| 3 | OpenDrift path (OceanDrift → OpenOil) behind the same interface |
| 4 | Forecast mode + uncertainty ellipses |
| 5 | Engine C gates (filtering) + gate tests |
| 6 | Engine C scoring + explanation generator + planted-culprit test |
| 7 | Benchmark run on Krishnan's 50 scenarios → top-1/top-3 hit rate |
| 8 | CLIs, docs, failure tests, handover |

### Phase 1 — Engine A

☐ Read mask GeoTIFF with rasterio; label connected components; drop specks below a min-area threshold.
☐ Per component: `regionprops` → area (convert px→km² via pixel size at that latitude), perimeter, centroid, ellipse axes + orientation; polygonise boundary (shapely) → WGS84 GeoJSON.
☐ Damping ratio: mean dB inside mask vs ring buffer outside (needs the scene's dB band).
☐ Fay age estimate from area (document assumptions; mark `age_confidence: "low"`).
☐ Write `slick.geojson` per §4.2; validate against schema.

### Phase 2 — Euler fallback (before OpenDrift)

☐ Seed N particles uniformly inside the slick polygon.
☐ Step: `v = current(x,t) + 0.03 * wind10(x,t)`; backward = negative dt; add Gaussian diffusion per step; bilinear interpolation of the NetCDF grids in space and time.
☐ Weight decay/spread → per-timestep weighted cloud; fit a covariance ellipse per timestep; derive the origin window (when/where cloud density peaks over the backward run of 12–24 h).
☐ Analytic tests (see §8) green.

### Phase 3 — OpenDrift path

☐ Wrap `OceanDrift` first (fewer deps), then `OpenOil`, behind the SAME function signature as the Euler fallback; reader = the two NetCDFs.
☐ Backward run via negative timestep; export particles → same `origin_cloud.geojson` writer.
☐ Selection order at runtime: OpenOil → OceanDrift → Euler; record `engine_used`.

### Phase 4 — Forecast

☐ Forward run from the detected slick at +6/+12/+24 h; concave-hull or convex-hull extent per horizon; uncertainty ellipse growing over time → `forecast.geojson`.

### Phase 5 — Engine C gates

☐ Spatial gate: track intersects buffered high-probability origin region.
☐ Temporal gate: presence within origin window ± buffer.
☐ Trajectory gate: course roughly compatible with slick major axis (discharge trails behind a moving vessel).
☐ A vessel failing gates is excluded WITH the reason recorded (`filter_reason`) — the UI shows "filtered out: outside time window".

### Phase 6 — Scoring + explanations

☐ Six factor scores per §2.1, each normalised 0–1; total = weighted sum with weights from `config/attribution_weights.yaml`.
☐ AIS-gap factor: transmission blackout overlapping the origin window is a strong suspicion signal.
☐ Explanation generator: template over the factor evidence → one plain-language sentence per vessel (see §4.4 example).

### Phase 7 — Benchmark

☐ Run Engine C over Krishnan's 50 seeded scenarios (each has a known culprit) → report top-1/top-3 hit rate. This number goes on the metrics slide.

## 7. Frozen Interfaces You Implement

```
python -m engines.characterise --mask <tif> --scene-meta <json> \
        --out slick.geojson
python -m engines.drift --slick slick.geojson \
        --currents currents.nc --wind wind.nc \
        --mode hindcast|forecast --hours 24 --out <geojson>
python -m engines.attribution --origin origin_cloud.geojson \
        --vessels vessels.parquet \
        --weights config/attribution_weights.yaml --out suspects.json
```

Each CLI (and its callable function) returns the status object of §4.5. Main system calls these as function or CLI with contract file paths.

## 8. Testing — the must-pass list

☐ A: known-shape test — a drawn ellipse must return its own axes/area within tolerance.
☐ B analytic: constant current field → backtracked origin equals the hand-computed point.
☐ B round-trip: forward then backward returns near the start.
☐ B fallback: Euler matches OpenDrift direction on the same field.
☐ C: planted culprit ranks top-1 on the synthetic scenario.
☐ C gate test: a vessel outside the time window is filtered with the reason recorded.
☐ Failure cases: empty mask / missing NetCDF variable / zero vessels in window → `EMPTY_MASK` / `BAD_GRID` / `NO_VESSELS_IN_WINDOW`, never a crash.
☐ All four output files validate against the Pydantic schemas.

## 9. Pitfalls and Traps

1. OpenDrift's GDAL/cartopy chain is the project's riskiest install — do it day 0–1 inside conda/Docker, commit `environment.yml`, and never attempt it with pip on system Python.
2. Write the Euler fallback FIRST. It is your development harness, your guaranteed demo path, and your sanity check on OpenDrift.
3. Degrees are not metres: convert km↔degrees with the latitude cosine; compute areas properly (pixel size varies with latitude).
4. Backward drift = negative timestep, and wind leeway is ~3% of 10 m wind — keep the sign conventions of u/v (eastward/northward positive) straight.
5. Never output a single origin point — always the weighted cloud + ellipse + time window.
6. Agree NetCDF variable names with Keerthana ONCE, in writing; her tiny hand-built NetCDF doubles as your mock.
7. The parquet columns are fixed by contract — code against them, not against a sample file's accidents.
8. Attribution is deliberately not a trained classifier (no ground truth exists; explainability is required) — resist the urge.
9. `NO_VESSELS_IN_WINDOW` is a valid, expected outcome — return it structured, don't treat it as a bug.

## 10. Integration and Handover

### 10.1 How the main system calls you

```
Main System ── contract file paths ──► [Engine A | B | C]
            ◄── contract output + {ok, engine_used, warnings} ──
```

On your declared error classes it shows a warning badge and, where defined, retries with your fallback engine. Your outputs go to the UI layers and (for A and B) to the next engine.

### 10.2 Coordination points

Keerthana (NetCDF variable names — once, in writing) and Krishnan (parquet columns — already fixed by contract, confirm once). Everything else flows through files.

### 10.3 Handover checklist (before handing to Indhu)

One-command run per engine against mocks · all tests green · sample outputs committed and schema-valid · `environment.yml`/Dockerfile committed · known-issues list · 30-minute walkthrough with Indhu. Then the common 10-point checklist applies: one-command run; input schema; output schema; example inputs; example outputs; test results; error behaviour per class; README; run instructions; integration instructions.

### 10.4 Definition of Done

Works independently · mock input works · real input works where applicable · output follows the contract · tests exist incl. failure cases · documentation exists · another developer can consume the output using only the contract · you can demonstrate each engine without the main system.
