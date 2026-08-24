# OceanTrace — Team Split & Developer Handbook

**SIH 2026 · Problem Statement 26143 (NTRO)**
*Leveraging satellite imagery to determine oil spills at sea along with AIS data correlations to identify the vessel responsible for the spill.*

**Companion document:** `PS26143_System_Specification.md` (architecture, module specs, fallback register). This handbook does **not** redesign that architecture — it assigns ownership of its pieces.

---

## 1. Core Working Principle

Every developer owns one component with a frozen contract:

```
INPUT  →  COMPONENT PROCESSING  →  OUTPUT
```

- Build, run, and test your component **alone**, against mock inputs.
- Nobody waits for anybody. If your upstream isn't ready, you use the mock files in `contracts/mocks/`.
- The contract is the law: as long as your output matches the contract, integration will work without rewriting your code.
- Conventions everywhere: **WGS84 (EPSG:4326)** coordinates, **UTC** timestamps, standard error taxonomy (`AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`).

## 2. Final Split

| Developer | Component | Core-work share |
|---|---|---|
| **Indhu Priyan** | ML models (detection) + Main System (backend + UI + API monitoring) + Final Integration | 60% of core work |
| **Nandha Kumar** | Core Engines: Characterisation, Drift (hindcast/forecast), Attribution | 40% of core work |
| **Pavitra** | Satellite Scene Service (Sentinel-1 acquisition APIs) | API component |
| **Keerthana** | Met-Ocean Data Service (currents + wind + weather APIs) | API component |
| **Krishnan** | AIS Data Service (real AIS + synthetic AIS generator) | API component |

## 3. Shared Contracts (frozen on day 1 — everyone codes against these)

| Contract file | Produced by | Consumed by | Content summary |
|---|---|---|---|
| `scene_meta.json` + calibrated scene GeoTIFF | Pavitra | Indhu (detection), UI | Scene ID, acquisition time (UTC), bbox, CRS, dB range, file path, provider used |
| `currents.nc`, `wind.nc` (regional NetCDF) | Keerthana | Nandha (drift) | u/v surface current, u10/v10 wind on lat/lon/time grid covering scene bbox ± margin |
| `vessels.parquet` | Krishnan | Nandha (attribution), UI | MMSI, timestamp (UTC), lat, lon, SOG, COG, heading, vessel type, dims, `source` flag (`real`/`synthetic`), `culprit` flag (synthetic only) |
| `slick.geojson` | Indhu (detection + Nandha's characterisation) | Nandha (drift), UI | Slick polygons, confidence, area, perimeter, centroid, major/minor axis, orientation, damping ratio, age estimate |
| `origin_cloud.geojson` | Nandha (drift) | Nandha (attribution), UI | Particle points (lat, lon, time, weight), origin window, confidence ellipse per timestep |
| `forecast.geojson` | Nandha (drift) | UI | Predicted spread at +6/+12/+24 h with uncertainty |
| `suspects.json` | Nandha (attribution) | UI | Ranked vessels: total score, per-factor sub-scores, plain-language reason |
| `provider_status.json` | Pavitra, Keerthana, Krishnan (each for their providers) | Indhu (API monitoring page) | Per provider: name, purpose, status, last success/failure, latency, active provider, error class |

Mock versions of **all** of these live in `contracts/mocks/` from day 1. Indhu creates them first (small hand-made samples); everyone develops against them.

## 4. Integration Map

```
Pavitra ──scene_meta.json + GeoTIFF──►┐
                                      │
Indhu (ML) ──raw mask──► Nandha (Characterise) ──slick.geojson──► Nandha (Drift)
                                      ▲                              │
Keerthana ──currents.nc / wind.nc─────┘                      origin_cloud.geojson
                                                                     │
Krishnan ──vessels.parquet──────────────► Nandha (Attribution) ◄─────┘
                                                    │
                                             suspects.json
                                                    │
                    Indhu (Main System: FastAPI + UI + API Monitoring + Replay)
```

Final integration order (Indhu): mocks end-to-end → swap in Pavitra → Keerthana → Krishnan → Nandha's engines → real ML weights → two demo scenes → replay mode → rehearsal.

---

# Developer Handbook — Indhu Priyan

## 1. Role
ML lead, Main System owner, final integrator. Claude Code power user.

## 2. Component Owned
**(a)** Oil-spill detection ML: DARTIS screening model + Trujillo U-Net segmentation, exported and wrapped as an inference service. **(b)** Main System: FastAPI backend skeleton, database, React/MapLibre UI, API Monitoring page, API Key Management page, replay mode. **(c)** Final integration of all components.

## 3. Where My Component Fits
Detection sits between Pavitra's scene output and Nandha's characterisation. The Main System is the shell every other component plugs into.

## 4. What I Need as Input
- Datasets: DARTIS (PANGAEA) and Trujillo (Zenodo Parts I–III) — I acquire these myself.
- For inference: a calibrated Sigma0-dB GeoTIFF + `scene_meta.json` (mock until Pavitra delivers).
- For the UI: all mock contract files.

## 5. What My Component Must Produce
- **Model artefacts:** `screen.onnx` (or .pt) and `segment.onnx`, with a `model_card.md` (metrics, dB normalisation constants, tile size).
- **Inference service:** `POST /detect` — input: scene path/ID; output: raw binary mask (GeoTIFF/PNG) + confidence + candidate boxes. This raw mask is what Nandha's characterisation converts into `slick.geojson`.
- **Main System:** running backend + UI rendering every layer from the contract files; API Monitoring page consuming `provider_status.json`; Key Management page (masked keys, test-connection button).
- `contracts/mocks/` — all mock files, created in the first two days.

## 6. What I Own
Dataset acquisition/preparation, model training/evaluation/selection, inference API, backend skeleton + DB + auth, UI, monitoring/key pages, replay mode, mock files, final integration, demo rehearsal.

## 7. What I Do NOT Own
Drift physics, characterisation maths, attribution scoring (Nandha). External data fetching (Pavitra/Keerthana/Krishnan). I integrate their outputs; I do not rewrite their components.

## 8. How I Can Work Independently
Datasets are public — no dependency on teammates. The UI is built entirely on mocks. The inference service is tested on Trujillo test tiles, not on Pavitra's live scenes.

## 9. Mock/Test Input
Trujillo Part III test tiles (150 oil / 150 look-alike / 150 no-oil) as inference input; hand-made `scene_meta.json`; all UI mocks are my own deliverable.

## 10. Testing Requirements
- Segmentation: binary IoU on Trujillo Part III (report the real number; never pixel accuracy).
- Screening: mAP@0.5 + per-phenomenon false-positive table on DARTIS.
- Inference API: golden-file test — fixed input tile → expected mask hash; latency < 5 s per scene tile batch on the 4050; CPU fallback path (threshold + morphology) returns a valid mask when no GPU.
- UI: every layer renders from mocks; monitoring page shows a failed provider correctly.

## 11. Final Deliverables
Trained weights + model card; inference service with `/detect`; Main System (backend + UI + monitoring + key management + replay); `contracts/` folder with schemas and mocks; integration test suite; final integrated system.

## 12. How My Component Integrates With Main System
I **am** the main system. Integration protocol per component: (1) main system calls component with contract input, (2) validates output against the Pydantic schema, (3) on schema failure or error class, falls back (cached/mock output) and surfaces a UI badge — the pipeline never halts. Errors I must handle from others: missing files, schema mismatch, provider `AUTH_FAILED`/`TIMEOUT`, empty results.

## 13. Handover Requirements
Nothing handed over — I receive. My acceptance rule for others' handovers: component runs from a one-command script, passes its own tests against the mocks, output validates against the schema, README with setup + known issues.

## Model Training Workflow (Steps 1–10)

1. **Obtain datasets.** DARTIS from PANGAEA (doi:10.1594/PANGAEA.980773). Trujillo from Zenodo: Part III (record 13761290) **first**, then Part I (8346860) and Part II (8253899) with the tile-and-discard script (download → tile → delete source).
2. **Inspect.** Open 20 random image/mask pairs from each set; confirm mask alignment, dB value ranges, look-alike categories in DARTIS; record counts in `docs/data_card.md`.
3. **Prepare splits.** Trujillo: tile to 256×256, keep tiles ≥1% oil + matched hard negatives; store as uint8 memory-mapped `.npy`; dB clip constants (≈ −35…0 dB) into `config/normalisation.yaml`; splits = Trujillo's own train/val + Part III as untouched test. DARTIS: Ultralytics YOLO format; stratify val split across look-alike phenomena.
4. **Train.** Model 1: U-Net (ResNet-34, ImageNet weights, `segmentation-models-pytorch`), Dice+BCE, AMP fp16, batch 12–16, ~40 epochs, checkpoint every epoch (~2 h). Model 2: YOLOv8n/11n on DARTIS, ~50 epochs (~2–3 h). Sequential on the 4050; laptop plugged in, Turbo mode.
5. **Evaluate.** Model 1: binary IoU, precision, recall on Part III. Model 2: mAP@0.5 + per-phenomenon FP table.
6. **Compare.** Optional second baseline (plain U-Net or DeepLabv3+) for the metrics slide; pick by test IoU + visual inspection of 20 predictions.
7. **Select.** Freeze the chosen checkpoint per model; tag in git.
8. **Prepare for integration.** Export ONNX/TorchScript; bake normalisation into the preprocessing function; write `model_card.md`.
9. **Interface.** Implement `/detect` exactly per the Model I/O section below.
10. **Independent test.** Golden-file inference test + throughput benchmark + CPU fallback test; commit weights + tests.

## Model Input/Output (frozen)

**Input:** `{ "scene_path": "<GeoTIFF, Sigma0 dB, EPSG:4326>", "scene_id": "...", "mode": "full|tile" }`
**Output:** `{ "scene_id": "...", "mask_path": "<GeoTIFF, 0/1>", "confidence": 0.0–1.0, "candidates": [ {"bbox": [...], "class": "oil|lookalike", "score": ...} ], "model_version": "...", "engine": "ml|threshold_fallback" }`
Consumers never need to know the model internals — they read the mask and the flags.

---

# Developer Handbook — Nandha Kumar

## 1. Role
Core engines developer (Claude Code user). Owns the science between detection and the UI.

## 2. Component Owned
Three pure-Python engines, each contract-in/contract-out:
- **Engine A — Characterisation:** raw mask → `slick.geojson` (area, perimeter, centroid, ellipse axes, orientation, damping ratio, Fay age estimate).
- **Engine B — Drift:** `slick.geojson` + `currents.nc` + `wind.nc` → `origin_cloud.geojson` (backward 12–24 h) and `forecast.geojson` (+6/+12/+24 h). Primary OpenDrift OpenOil; fallback OceanDrift; guaranteed path = in-house Euler integrator (current + 3% wind + Gaussian diffusion) written **first**.
- **Engine C — Attribution:** `origin_cloud.geojson` + `vessels.parquet` → `suspects.json`. Filtering gates (spatial/temporal/trajectory) → weighted scoring (proximity, temporal, trajectory, anomalies, AIS gaps, vessel prior) → ranked list with per-factor breakdown + plain-language reason.

## 3. Where My Component Fits
The middle of the pipeline: everything between Indhu's raw mask and the UI's final layers.

## 4. What I Need as Input
Mock raw mask (hand-drawn polygon raster), mock/synthetic small NetCDF current+wind fields, mock `vessels.parquet` — all in `contracts/mocks/` from day 1. Real files replace them at integration with zero code change.

## 5. What My Component Must Produce
`slick.geojson`, `origin_cloud.geojson`, `forecast.geojson`, `suspects.json` — exactly per schema, plus a CLI per engine (`python -m engines.drift --slick ... --currents ... --wind ...`).

## 6. What I Own
Geometry maths, drift physics + OpenDrift environment (conda/Docker, smoke-tested early — this is the riskiest install in the project), Euler fallback, filtering + scoring + explanation generator, configurable weights, engine unit tests, engine docs.

## 7. What I Do NOT Own
ML models, external API fetching, UI, database, final integration.

## 8. How I Can Work Independently
All three engines are file-in/file-out. Nothing requires a network, a GPU, or another teammate.

## 9. Mock/Test Input
Hand-drawn slick polygon; synthetic uniform + rotating current fields (analytic ground truth: with a known constant current, the backtracked origin is computable by hand — the engine must reproduce it); synthetic vessels.parquet with one planted culprit.

## 10. Testing Requirements
- Characterisation: known-shape test (a drawn ellipse must return its own axes/area within tolerance).
- Drift: analytic test (constant current field → hand-computable origin); forward+backward round-trip returns near the start; fallback integrator matches OpenDrift direction on the same field.
- Attribution: planted-culprit test — synthetic scenario where the known culprit must rank top-1; gate tests (a vessel outside the time window must be filtered with the recorded reason).
- Error cases: empty mask, missing NetCDF variable, zero vessels — engines return structured errors, never crash.

## 11. Final Deliverables
Three engines with CLIs, `environment.yml`/Dockerfile for OpenDrift, unit tests, weight-config file, README per engine, sample outputs committed.

## 12. How My Component Integrates With Main System
Main system calls each engine (function or CLI) with contract file paths; engines return contract files + a status object `{ok, engine_used (primary/fallback), warnings}`. Errors: `MISSING_INPUT`, `BAD_GRID`, `NO_VESSELS_IN_WINDOW` — main system shows the warning badge and, where defined, retries with the fallback engine. Outputs go to the UI layers and to Engine C.

## 13. Handover Requirements
One-command run per engine against mocks; all tests green; sample outputs match schemas; known-issues list; 30-minute walkthrough with Indhu.

---

# Developer Handbook — Pavitra

## 1. Role
API developer — Satellite Scene Service.

## 2. Component Owned
Sentinel-1 scene acquisition: search, download, cache, and describe SAR scenes.

## 3. Where My Component Fits
The very start of the pipeline: my output is what the detection model consumes.

## 4. What I Need as Input
A request: `{ "bbox": [W,S,E,N], "start": "UTC", "end": "UTC", "scene_id": optional }`. No dependency on any teammate.

## 5. What My Component Must Produce
- Calibrated scene GeoTIFF in `data/scenes/<scene_id>/` (pre-processed scenes are acceptable for the POC; raw GRD download is the stretch goal).
- `scene_meta.json`: scene ID, acquisition time (UTC), bbox, CRS, dB range, file path, provider used.
- `provider_status.json` entries for my providers (CDSE, ASF) for the monitoring page.

## 6. What I Own
- **Primary:** Copernicus Data Space Ecosystem — account, OAuth client, **token-refresh wrapper** (access token lives ~10 min — downloads fail without it), OData search + download.
- **Fallback:** ASF Vertex via `asf_search` (Earthdata login).
- Local scene cache (never re-download), download progress/retry, provider health reporting, the two demo scenes downloaded and verified.

## 7. What I Do NOT Own
SAR preprocessing science (calibration constants come from `config/normalisation.yaml`), detection, UI. If raw-GRD calibration proves too heavy, the team pivots to pre-calibrated scenes — flag it early, don't fight it alone.

## 8. How I Can Work Independently
My component talks only to external APIs. I test with any public Sentinel-1 scene over any ocean region.

## 9. Mock/Test Input
Fixed request JSONs in `tests/requests/`; a known scene ID with a known file size/checksum as the golden test.

## 10. Testing Requirements
Search returns results for a known bbox/date; download completes and checksum matches; token expiry mid-download recovers; CDSE failure automatically falls back to ASF and records it in `provider_status.json`; cached scene is served without network (airplane-mode test).

## 11. Final Deliverables
`satellite_service` module + CLI (`fetch-scene --bbox ... --start ... --end ...`), the two demo scenes cached in `data/scenes/`, provider status reporting, tests, README with account-setup steps.

## 12. How My Component Integrates With Main System
Main system sends the request JSON → I return `scene_meta.json` (+ file on disk) → detection reads the GeoTIFF. Format: contract §3. Errors: `AUTH_FAILED` (bad OAuth), `UNAVAILABLE` (both providers down), `NOT_FOUND` (no scene for window), `TIMEOUT`. Main-system handling: fall back to the cached demo scenes and badge "CACHED".

## 13. Handover Requirements
One-command fetch works; both demo scenes present with metadata; airplane-mode test passes; README with credentials setup; walkthrough with Indhu.

---

# Developer Handbook — Keerthana

## 1. Role
API developer — Met-Ocean Data Service.

## 2. Component Owned
Ocean currents, wind, and weather acquisition + regional caching.

## 3. Where My Component Fits
Feeds Nandha's drift engine — without my NetCDFs the hindcast/forecast cannot run on real physics.

## 4. What I Need as Input
`{ "bbox": [W,S,E,N], "start": "UTC", "end": "UTC" }` (scene bbox ± margin, time window ± 48 h). No teammate dependency.

## 5. What My Component Must Produce
- `currents.nc` — u/v surface current on lat/lon/time grid (CF-compliant variable names documented in the README).
- `wind.nc` — u10/v10 wind, same grid convention.
- Cached copies for both demo scenes in `data/metocean/`.
- `provider_status.json` entries (CMEMS, ERA5, Open-Meteo).

## 6. What I Own
- **Currents primary:** Copernicus Marine toolbox — **GLORYS multiyear** (`GLOBAL_MULTIYEAR_PHY_001_030`) for historical scene dates (the analysis/forecast product does not cover old dates — this is the #1 trap). Fallback: HYCOM via OPeNDAP (no key).
- **Wind primary:** ERA5 via the **new** CDS (`cdsapi`; personal access token; **licence must be accepted on the dataset page** or requests fail with a misleading error). Fallback: Open-Meteo (no key).
- Subsetting to region, unit normalisation, caching, provider health reporting.

## 7. What I Do NOT Own
Drift physics (Nandha), scene selection, UI.

## 8. How I Can Work Independently
External APIs only. Any ocean bbox works for development; the demo bbox/dates come later from the chosen scenes.

## 9. Mock/Test Input
Fixed request JSONs; a tiny hand-built NetCDF as the format reference (also serves as Nandha's mock — coordinate with him once on variable names, then both work independently).

## 10. Testing Requirements
Retrieve a real 3-day window for a test bbox from CMEMS and ERA5; verify grids open in xarray with expected variables/units; kill the primary (wrong credentials) and confirm automatic fallback + status record; cached files served offline; **date-range test:** a 2017 date must route to GLORYS, not the forecast product.

## 11. Final Deliverables
`metocean_service` module + CLI (`fetch-metocean --bbox ... --start ... --end ...`), cached NetCDFs for both demo scenes, provider status reporting, tests, README with account setup (CMEMS login, CDS token + licence).

## 12. How My Component Integrates With Main System
Main system sends the request → I return paths to `currents.nc`/`wind.nc` → drift engine opens them with xarray. Errors: `AUTH_FAILED`, `LICENCE_NOT_ACCEPTED`, `NO_DATA_FOR_PERIOD`, `TIMEOUT`, `UNAVAILABLE`. Main-system handling: fall back to cache; if no cache, drift engine runs wind-only or constant-wind mode with a UI badge.

## 13. Handover Requirements
One-command fetch works for both demo scene windows; cached NetCDFs committed to `data/`; offline test passes; README; walkthrough with Nandha (variable names) and Indhu.

---

# Developer Handbook — Krishnan

## 1. Role
API developer — AIS Data Service.

## 2. Component Owned
Vessel-track data: real AIS ingestion + cleaning + interpolation, **and** the synthetic AIS generator (mandatory infrastructure — it provides the ground truth for the attribution benchmark).

## 3. Where My Component Fits
Feeds Nandha's attribution engine and the UI's vessel layers.

## 4. What I Need as Input
`{ "bbox": [W,S,E,N], "start": "UTC", "end": "UTC", "mode": "real|synthetic", "n_vessels": ..., "culprit": {...} (synthetic) }`. No teammate dependency.

## 5. What My Component Must Produce
- `vessels.parquet` per the contract: MMSI, timestamp (UTC), lat, lon, SOG, COG, heading, vessel type, dims, `source` flag, `culprit` flag (synthetic only). Schema identical for real and synthetic data.
- A 50-scenario synthetic benchmark set (each with one planted culprit) for the attribution hit-rate metric.
- `provider_status.json` entries for AIS sources.

## 6. What I Own
- **Real AIS:** Danish Maritime Authority open archive (proof scene, dense EU traffic) and/or MarineCadastre (US waters only — note: no real AIS exists for Indian waters; that is exactly why the synthetic path exists). Parsing, dedup, outlier removal, 5-minute interpolation, per-vessel track assembly, AIS-gap detection fields.
- **Synthetic generator:** shipping-lane-style tracks + a culprit whose behaviour matches a discharge (passes through a given origin region at a given time, optional slowdown and AIS gap), in the exact MarineCadastre schema.
- Optional stretch: AISStream.io live websocket for a "live" demo tab.

## 7. What I Do NOT Own
Attribution scoring (Nandha), origin estimation, UI.

## 8. How I Can Work Independently
Real archives are public bulk downloads; the generator needs nothing external. The "origin region" input for synthetic scenarios is just a lat/lon/time JSON — mocked until Nandha's drift output exists.

## 9. Mock/Test Input
Sample day of Danish DMA CSV; a mock origin-region JSON for culprit planting.

## 10. Testing Requirements
Real path: parse a raw archive day → valid parquet (no dupes, monotonic timestamps per MMSI, UTC-verified); interpolation test (known gap filled at 5-min steps). Synthetic path: planted culprit's track verifiably passes through the requested origin region/time; schema-equality test — real and synthetic parquet have identical columns/dtypes; 50-scenario benchmark generates reproducibly from a seed.

## 11. Final Deliverables
`ais_service` module + CLI (`fetch-ais` / `generate-ais`), cleaned real AIS for the proof scene, synthetic AIS for the headline scene, the 50-scenario benchmark, tests, README.

## 12. How My Component Integrates With Main System
Main system sends the request → I return `vessels.parquet` → attribution engine and UI consume it. `source` flag drives the UI's REAL/SYNTHETIC badge. Errors: `ARCHIVE_UNAVAILABLE`, `EMPTY_REGION`, `PARSE_ERROR`. Main-system handling: `mode=real` failure falls back to `mode=synthetic` with the badge switched.

## 13. Handover Requirements
One-command generation and one-command real-ingest work; benchmark set committed with seed; schema tests green; README; walkthrough with Nandha (attribution consumes my columns) and Indhu.

---

## 5. No-Blocking Guarantee (summary)

| Developer | Blocked by anyone? | Why not |
|---|---|---|
| Indhu | No | Public datasets; UI runs on his own mocks |
| Nandha | No | File-in/file-out engines on mocks; analytic test data |
| Pavitra | No | External APIs only |
| Keerthana | No | External APIs only |
| Krishnan | No | Public archives + self-contained generator |

## 6. Handover & Integration Checklist (Indhu enforces)

1. Component runs from one command against `contracts/mocks/`.
2. All component tests green; output validates against the Pydantic schema.
3. README: setup, credentials, known issues.
4. 30-minute walkthrough recorded/held.
5. Integration order: mocks end-to-end → Pavitra → Keerthana → Krishnan → Nandha's engines → real ML weights → two demo scenes → replay mode → full rehearsal.
6. Freeze rule: after a component is integrated and green, nobody edits it without Indhu's sign-off.
