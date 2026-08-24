# OceanTrace — Complete Developer Handbook (Final)

**SIH 2026 · Problem Statement 26143 (NTRO) · Category: Software · Theme: Space Technology**
*Leveraging satellite imagery to determine oil spills at sea along with AIS data correlations to identify the vessel responsible for the spill.*

**Companion document:** `PS26143_System_Specification.md` (architecture, module specs, fallback register). This handbook assigns ownership of that architecture — it does not redesign it.

**Team:** Indhu Priyan · Nandha Kumar · Pavitra · Keerthana · Krishnan
**Datasets locked:** DARTIS + Trujillo (Zenodo Sentinel-1)

---

## PART A — What the Complete Project Does

The system takes a Sentinel-1 SAR satellite scene, detects and characterises any oil slick in it, uses ocean-current and wind data to trace the slick backwards to its probable origin point and time (hindcast) and forwards to its future spread (forecast), reconstructs historic AIS vessel traffic around that origin window, filters irrelevant vessels, scores the remaining candidates on proximity, trajectory, behaviour anomalies and spatio-temporal correlation, and presents a ranked, explainable suspect list on a GIS investigation interface. Two additional pages — **API Monitoring** and **API Key Management** — make every external dependency observable and manageable, with automatic fallback between providers.

## PART B — Complete Project Architecture (everyone's map)

```
EXTERNAL WORLD                                OWNER
──────────────────────────────────────────────────────
Sentinel-1 SAR scene acquisition ............ Pavitra
Ocean currents + wind + weather ............. Keerthana
AIS vessel data (real + synthetic) .......... Krishnan
──────────────────────────────────────────────────────
                    │
                    ▼
   ┌────────────────────────────────────┐
   │ 1. SATELLITE IMAGERY  ..... Pavitra│
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 2. OIL SPILL DETECTION (ML) . Indhu│
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 3. SPILL CHARACTERISATION .. Nandha│
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 4. OCEAN + MET DATA ..... Keerthana│
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 5. HINDCAST + FORECAST ..... Nandha│
   │    → PROBABLE ORIGIN               │
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 6. AIS RECONSTRUCTION .... Krishnan│
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 7. VESSEL FILTERING ........ Nandha│
   │ 8. CORRELATION + SCORING ... Nandha│
   │ 9. RANKING (explainable) ... Nandha│
   └────────────────┬───────────────────┘
                    ▼
   ┌────────────────────────────────────┐
   │ 10. VISUAL INTERFACE (GIS) .. Indhu│
   │     + API MONITORING PAGE .. Indhu │
   │     + API KEY MANAGEMENT ... Indhu │
   └────────────────┬───────────────────┘
                    ▼
          FINAL INTEGRATION ..... Indhu
                    ▼
            COMPLETE SYSTEM
```

## PART C — Team Ownership Architecture

```
                         COMPLETE PROJECT
                                │
              ┌─────────────────┼──────────────────────┐
              │                 │                      │
              ▼                 ▼                      ▼
        INDHU PRIYAN      NANDHA KUMAR            API MODULES
     ML + Main System     Core Engines         ┌─────┼──────────┐
     + UI + Monitoring    Characterise /       ▼     ▼          ▼
       60% core work      Drift / Attribution  PAVITRA KEERTHANA KRISHNAN
              │             40% core work      Satellite MetOcean  AIS
              │                 │                 │      │        │
              └─────────────────┴─────────────────┴──────┴────────┘
                                │
                                ▼
                        FINAL INTEGRATION
                          INDHU PRIYAN
                                │
                                ▼
                         COMPLETE SYSTEM
```

## PART D — Core Development Principle (applies to all five)

Every component follows **INPUT → COMPONENT → OUTPUT**. Every developer builds, runs, tests, and demonstrates their component **alone**, using mocks from `contracts/mocks/` whenever an upstream component isn't ready. Nobody waits for anybody. The contract is the law: if your output matches the contract schema, integration works without touching your code.

Global conventions: **WGS84 (EPSG:4326)** for all coordinates · **UTC** for all timestamps · standard error taxonomy: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`, plus component-specific classes named in each handbook.

## PART E — Shared Contracts (frozen day 1; Indhu creates the mocks first)

| Contract | Produced by | Consumed by | Content |
|---|---|---|---|
| `scene_meta.json` + calibrated GeoTIFF | Pavitra | Indhu (detection), UI | Scene ID, acquisition time (UTC), bbox, CRS, dB range, file path, provider used |
| `currents.nc`, `wind.nc` | Keerthana | Nandha (drift) | u/v surface current, u10/v10 wind on a lat/lon/time grid (scene bbox ± margin) |
| `vessels.parquet` | Krishnan | Nandha (attribution), UI | MMSI, UTC timestamp, lat, lon, SOG, COG, heading, type, dims, `source` flag (real/synthetic), `culprit` flag (synthetic only) |
| `slick.geojson` | Indhu (mask) → Nandha (characterisation) | Nandha (drift), UI | Slick polygons, confidence, area, perimeter, centroid, ellipse axes, orientation, damping ratio, age estimate |
| `origin_cloud.geojson` | Nandha (drift) | Nandha (attribution), UI | Particle points (lat, lon, time, weight), origin window, confidence ellipse per timestep |
| `forecast.geojson` | Nandha (drift) | UI | Predicted spread at +6/+12/+24 h with uncertainty |
| `suspects.json` | Nandha (attribution) | UI | Ranked vessels: total score, per-factor sub-scores, plain-language reason |
| `provider_status.json` | Pavitra, Keerthana, Krishnan | Indhu (API Monitoring page) | Per provider: name, purpose, status, last success/failure, latency, error class, active provider |

---

---

# DEVELOPER HANDBOOK 1 — INDHU PRIYAN

## 1. Role
ML / Model Training / Claude Code / Main System / Overall Integration. **~60% of the core technical work**, in two stages: independent development first, final integration second.

## 2. Component Owned
**(a)** The complete ML detection component: DARTIS screening model + Trujillo U-Net segmentation, trained, evaluated, exported, and wrapped as an inference service.
**(b)** The Main System shell: FastAPI backend, database, React/MapLibre GIS UI, **API Monitoring page**, **API Key Management page**, replay mode.
**(c)** Final integration of all five components.

## 3. Whole Project Architecture — THIS IS YOUR MODULE

```
Satellite Scene (Pavitra)
        ↓
╔══════════════════════════════════════════╗
║ 2. OIL SPILL DETECTION (ML)              ║ ◄◄◄ THIS IS YOUR MODULE
╚══════════════════════════════════════════╝
        ↓ raw mask
Characterisation → Drift → Origin (Nandha)
        ↓                    ▲
        ↓          Met-ocean (Keerthana)
AIS (Krishnan) → Filtering → Scoring → Ranking (Nandha)
        ↓
╔══════════════════════════════════════════╗
║ 10. GIS UI + API MONITORING + KEY MGMT   ║ ◄◄◄ ALSO YOUR MODULE
╚══════════════════════════════════════════╝
        ↓
╔══════════════════════════════════════════╗
║ FINAL INTEGRATION                        ║ ◄◄◄ ALSO YOUR MODULE
╚══════════════════════════════════════════╝
        ↓
COMPLETE SYSTEM
```

## 4. Where My Module Fits
Detection sits between Pavitra's scene output and Nandha's characterisation — my raw mask is Nandha's Engine A input. The Main System is the shell everything plugs into: it calls each component with its contract input, validates the output, renders the layers, and shows provider health.

## 5. What I Receive
- Datasets (I acquire): DARTIS (PANGAEA doi:10.1594/PANGAEA.980773) and Trujillo (Zenodo records 13761290, 8346860, 8253899).
- Inference input: calibrated Sigma0-dB GeoTIFF + `scene_meta.json` (mock until Pavitra delivers).
- UI inputs: all eight contract files (mocks first — my own deliverable).

## 6. What I Produce
- **Model artefacts:** `segment.onnx` + `screen.onnx` (or .pt) with `model_card.md` (metrics, dB normalisation constants, tile size).
- **Inference API (frozen):**
  - Input: `{ "scene_path": "<GeoTIFF, Sigma0 dB, EPSG:4326>", "scene_id": "...", "mode": "full|tile" }`
  - Output: `{ "scene_id", "mask_path": "<GeoTIFF 0/1>", "confidence": 0–1, "candidates": [{"bbox", "class": "oil|lookalike", "score"}], "model_version", "engine": "ml|threshold_fallback" }`
  - Consumers read the mask and flags; they never need the model internals.
- **Main System:** running backend + UI rendering every layer; Monitoring page consuming `provider_status.json`; Key Management page (masked keys, change/save/test, admin-only, keys never in frontend code or full display).
- **`contracts/mocks/`** — all mock files, delivered in the first two days (everyone else's independence depends on this).

## 7. What I Need To Do
Dataset acquisition → inspection → preparation → training → evaluation → comparison → selection → export → interface definition → independent testing → Main System build → final integration (full workflow in §16 below).

## 8. What I Own
Both models end-to-end; inference service + CPU fallback (threshold + morphology); backend, DB, auth; UI + Monitoring + Key Management + replay mode; all mocks; the integration process; demo rehearsal.

## 9. What I Do NOT Own
Characterisation maths, drift physics, attribution scoring (Nandha). Scene fetching (Pavitra). Met-ocean fetching (Keerthana). AIS fetching/generation (Krishnan). I integrate their outputs through the contracts; I do not rewrite their components.

## 10. How I Work Independently
Datasets are public — zero teammate dependency. The UI is built entirely on my own mocks. Inference is tested on Trujillo Part III tiles, not live scenes. Nothing in my Stage-1 work waits on anyone.

## 11. Mock/Test Input
Trujillo Part III (150 oil / 150 look-alike / 150 no-oil tiles) for inference; hand-made `scene_meta.json`; hand-made versions of all eight contracts for the UI.

## 12. Testing
- Segmentation: binary IoU / precision / recall on Part III (never pixel accuracy — sea-class dominance makes it meaningless).
- Screening: mAP@0.5 + per-phenomenon false-positive table on DARTIS.
- Inference API: golden-file test (fixed tile → expected mask hash); throughput on the RTX 4050; CPU fallback returns a valid mask with no GPU.
- UI: every layer renders from mocks; Monitoring page correctly shows a killed provider; Key Management test-connection reports the right error class.
- Failure tests: corrupt GeoTIFF, empty scene, missing metadata → structured errors, never a crash.

## 13. Final Deliverables
☐ Prepared datasets + `docs/data_card.md` ☐ Training pipeline (scripts, config, seeds) ☐ Trained models + checkpoints ☐ Evaluation results ☐ Selected model + model card ☐ Frozen model I/O contract ☐ Independent test suite ☐ Integration-ready inference service ☐ Main System (backend + UI + Monitoring + Key Management + replay) ☐ `contracts/` with schemas + mocks ☐ Final integrated system.

## 14. How My Component Integrates With Main System

```
Main System
     ↓  scene_meta.json + GeoTIFF
[ML DETECTION COMPONENT]
     ↓  mask + confidence + flags
Nandha's Engine A (characterisation) → ... → UI
```

I am also the main system, so my integration protocol for every component is: call with contract input → validate output against the Pydantic schema → on failure or a declared error class, fall back (cache/mock) and show a UI badge — the pipeline never halts. Integration order: mocks end-to-end → Pavitra → Keerthana → Krishnan → Nandha's engines → real ML weights → two demo scenes → replay mode → rehearsal.

## 15. Handover Requirements
Nothing handed over — I receive. My acceptance gate is Part G (common handover checklist) + Part H (Definition of Done).

## 16. Dedicated Model-Training Workflow (Steps 1–12)

1. **Dataset acquisition.** DARTIS from PANGAEA. Trujillo from Zenodo — Part III (13761290) **first** (small; becomes the test harness), then Parts I (8346860) and II (8253899) via the tile-and-discard script (download → tile → delete source; never hold 40–60 GB on disk).
2. **Dataset audit.** Open 20 random image/mask pairs per set; verify mask alignment, dB value ranges, DARTIS look-alike categories; record all counts in `docs/data_card.md`.
3. **Dataset preparation.** Trujillo: tile 256×256; keep tiles ≥1% oil + matched hard negatives; store as uint8 memory-mapped `.npy`; dB clip constants (≈ −35…0 dB) frozen in `config/normalisation.yaml` (shared by training AND inference). DARTIS: YOLO format. Never merge the two datasets (different radiometry, format, label geometry).
4. **Splitting.** Trujillo train/val from Parts I–II; Part III untouched as test. DARTIS val split stratified across look-alike phenomena.
5. **Training.** Model 1: U-Net (ResNet-34, ImageNet weights, `segmentation-models-pytorch`), Dice+BCE, AMP fp16, batch 12–16, ~40 epochs, checkpoint every epoch (~2 h on the 4050). Model 2: YOLOv8n/11n on DARTIS, ~50 epochs (~2–3 h). Sequential; laptop plugged in, Turbo mode.
6. **Evaluation.** Model 1: binary IoU + precision/recall on Part III. Model 2: mAP@0.5 + per-phenomenon FP table.
7. **Comparison.** Optional second segmentation baseline (plain U-Net / DeepLabv3+) for the metrics slide; compare by test IoU + visual inspection of 20 predictions.
8. **Selection.** Freeze the winning checkpoint per model; tag in git.
9. **Model preparation.** Export ONNX/TorchScript; bake normalisation into preprocessing; write `model_card.md`.
10. **Model I/O.** Implement `/detect` exactly per §6 — frozen; changes require team sign-off.
11. **Independent testing.** Golden-file inference test, throughput benchmark, CPU-fallback test; commit weights + tests.
12. **Integration.** Wire the inference service into the Main System; replace the mock mask; run the two demo scenes end-to-end.

---

# DEVELOPER HANDBOOK 2 — NANDHA KUMAR

## 1. Role
Core Engines / Claude Code. **~40% of the core technical work** — the science between the detection mask and the UI.

## 2. Component Owned
Three pure file-in/file-out engines (each independently testable):
- **Engine A — Characterisation:** raw mask → `slick.geojson` (area, perimeter, centroid, ellipse axes, orientation, damping ratio, Fay spreading-law age estimate).
- **Engine B — Drift (hindcast + forecast):** `slick.geojson` + `currents.nc` + `wind.nc` → `origin_cloud.geojson` (backward 12–24 h) + `forecast.geojson` (+6/+12/+24 h). Primary: OpenDrift OpenOil. Fallback: OpenDrift OceanDrift. Guaranteed path (write it **first**): in-house Euler integrator — velocity = current + 3% wind, Gaussian diffusion.
- **Engine C — Attribution:** `origin_cloud.geojson` + `vessels.parquet` → `suspects.json`. Gates (spatial/temporal/trajectory) → weighted scoring (proximity, temporal correlation, trajectory correlation, behaviour anomalies, AIS gaps, vessel prior) → ranked list with per-factor breakdown and a plain-language reason per vessel.

## 3. Whole Project Architecture — THIS IS YOUR MODULE

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

## 4. Where My Module Fits
Everything between Indhu's raw mask and the UI's final layers. Engine A feeds Engine B; Engine B feeds Engine C; all three feed the UI.

## 5. What I Receive
| Engine | Input | Format | Required fields | Optional |
|---|---|---|---|---|
| A | Raw mask | GeoTIFF 0/1 + scene metadata | CRS, pixel size, dB backscatter band (for damping ratio) | look-alike flags |
| B | Slick + met-ocean | `slick.geojson`, `currents.nc` (u/v), `wind.nc` (u10/v10) | lat/lon/time grids covering slick bbox ± margin | Stokes drift |
| C | Origin + vessels | `origin_cloud.geojson`, `vessels.parquet` | particle weights; MMSI/time/lat/lon/SOG/COG | vessel type, dims |

All mocks live in `contracts/mocks/` from day 1; real files replace them with zero code change.

## 6. What I Produce
`slick.geojson`, `origin_cloud.geojson`, `forecast.geojson`, `suspects.json` — exactly per schema — plus a status object per run: `{ok, engine_used: primary|fallback, warnings[]}` and one CLI per engine (e.g. `python -m engines.drift --slick ... --currents ... --wind ...`).
**Error classes:** `MISSING_INPUT`, `BAD_GRID`, `EMPTY_MASK`, `NO_VESSELS_IN_WINDOW` — returned as structured errors, never crashes.

## 7. What I Need To Do
Build the three engines; define/respect the contracts; set up the OpenDrift conda/Docker environment **early** (riskiest install in the project — GDAL/cartopy chain) and commit `environment.yml`; write the Euler fallback first; implement configurable scoring weights + the explanation generator; create mocks, tests (success/failure/edge), docs; prepare integration-ready CLIs.

## 8. What I Own
Geometry maths; drift physics + OpenDrift environment + Euler fallback; filtering gates; scoring weights + explanations; engine tests + docs.

## 9. What I Do NOT Own
ML models and inference (Indhu). External data fetching (Pavitra/Keerthana/Krishnan). UI, database, monitoring pages, final integration (Indhu).

## 10. How I Work Independently
All three engines are file-in/file-out — no network, no GPU, no teammate needed. Mocks + analytic test fields cover everything.

## 11. Mock/Test Input
Hand-drawn slick polygon raster; synthetic uniform and rotating current fields (with a constant current the backtracked origin is hand-computable — analytic ground truth); a tiny hand-built NetCDF (variable names agreed once with Keerthana); synthetic `vessels.parquet` with one planted culprit.

## 12. Testing
- **A:** known-shape test — a drawn ellipse must return its own axes/area within tolerance.
- **B:** analytic test (constant current → hand-computable origin); forward+backward round-trip returns near start; Euler fallback matches OpenDrift direction on the same field.
- **C:** planted-culprit must rank top-1; gate test — a vessel outside the time window is filtered with the reason recorded.
- **Failure:** empty mask / missing NetCDF variable / zero vessels → structured errors.

## 13. Final Deliverables
☐ Three engines + CLIs ☐ I/O contracts respected (sample outputs committed) ☐ `environment.yml` / Dockerfile for OpenDrift ☐ Euler fallback ☐ Mock inputs ☐ Unit + failure tests ☐ Weight-config file ☐ README per engine ☐ Integration-ready handover.

## 14. How My Component Integrates With Main System

```
Main System
     ↓  contract file paths
[ENGINE A | ENGINE B | ENGINE C]
     ↓  contract output + status {ok, engine_used, warnings}
Next engine / UI layers
```

Main system calls each engine as a function or CLI; on my declared error classes it shows a warning badge and, where defined, retries with my fallback engine.

## 15. Handover Requirements
One-command run per engine against mocks; all tests green; outputs validate against schemas; known-issues list; 30-minute walkthrough with Indhu. Then Part G + Part H apply.

---

# DEVELOPER HANDBOOK 3 — PAVITRA

## 1. Role
API Fetching / API Integration — **Satellite Scene Service** (Sentinel-1 acquisition).

## 2. Component Owned
The satellite-imagery data source: search, download, cache, and describe Sentinel-1 SAR scenes, with provider fallback and health reporting.

## 3. Whole Project Architecture — THIS IS YOUR MODULE

```
EXTERNAL: Copernicus Data Space (primary) / ASF Vertex (fallback)
        ↓
╔══════════════════════════════════════════╗
║ 1. SATELLITE SCENE SERVICE               ║ ◄◄◄ THIS IS YOUR MODULE
║    search → download → cache → describe  ║
╚══════════════════════════════════════════╝
        ↓ scene_meta.json + calibrated GeoTIFF
Detection ML (Indhu) → Characterisation → Drift → Origin (Nandha)
        ↑                                   ▲
        │                          Met-ocean (Keerthana)
AIS (Krishnan) → Filter + Score + Rank (Nandha)
        ↓
GIS UI + Integration (Indhu) → COMPLETE SYSTEM
```

## 4. Where My Module Fits
The very start of the pipeline. My GeoTIFF is what the detection model reads; my `scene_meta.json` carries the acquisition time and bbox that every later stage (drift window, AIS window) is anchored to. My `provider_status.json` entries feed Indhu's API Monitoring page.

## 5. What I Receive
`{ "bbox": [W,S,E,N], "start": "<UTC>", "end": "<UTC>", "scene_id": "<optional>" }` — from the main system (mocked as fixed JSON files during development). No teammate dependency.

## 6. What I Produce
- Calibrated Sigma0-dB scene GeoTIFF in `data/scenes/<scene_id>/` (pre-processed scenes acceptable for the POC; raw-GRD download is the stretch goal).
- `scene_meta.json`: scene ID, acquisition time (UTC), bbox, CRS, dB range, file path, provider used.
- `provider_status.json` entries for CDSE and ASF.
**Error classes:** `AUTH_FAILED`, `NOT_FOUND` (no scene in window), `TIMEOUT`, `UNAVAILABLE` (both providers down).

## 7. What I Need To Do
Create the CDSE account + OAuth client; build the **token-refresh wrapper** (access token lives ~10 minutes — long downloads fail without it); implement OData search + download; implement the ASF `asf_search` fallback; build the local scene cache (never re-download); retry/progress handling; provider health reporting; download and verify the **two demo scenes**; tests; documentation.

## 8. What I Own
Everything in §7 — the full path from external providers to a cached, described scene on disk.

## 9. What I Do NOT Own
SAR calibration science (constants come from `config/normalisation.yaml`), detection (Indhu), drift/attribution (Nandha), met-ocean (Keerthana), AIS (Krishnan), UI (Indhu). If raw-GRD calibration proves too heavy, the team pivots to pre-calibrated scenes — flag it early, don't fight it alone.

## 10. How I Work Independently
My component talks only to external public APIs. Any Sentinel-1 scene over any ocean works for development; I never wait for a teammate.

## 11. Mock/Test Input
Fixed request JSONs in `tests/requests/`; one known scene ID with a known checksum as the golden test; a saved OData response JSON for offline unit tests.

## 12. Testing
☐ Successful search for a known bbox/date ☐ Successful download, checksum matches ☐ Failed request handled ☐ Invalid request rejected cleanly ☐ Invalid credentials → `AUTH_FAILED` ☐ Token expiry mid-download recovers ☐ CDSE down → automatic ASF fallback recorded in `provider_status.json` ☐ Missing/invalid response data handled ☐ **Airplane-mode test:** cached scene served with zero network ☐ Output matches the `scene_meta.json` schema.

## 13. Final Deliverables
☐ `satellite_service` module + CLI (`fetch-scene --bbox ... --start ... --end ...`) ☐ Working fetching with fallback ☐ I/O contract + example input/output ☐ Error handling ☐ Tests ☐ Both demo scenes cached with metadata ☐ Provider status reporting ☐ README with account-setup steps ☐ Integration-ready component.

## 14. How My Component Integrates With Main System

```
Main System
     ↓  request JSON
[SATELLITE SCENE SERVICE]
     ↓  scene_meta.json + GeoTIFF
Detection ML (Indhu) → rest of pipeline
```

On my error classes, the main system falls back to the cached demo scenes and badges the layer "CACHED".

## 15. Handover Requirements
One-command fetch works; both demo scenes present with metadata; airplane-mode test passes; README with credentials setup; walkthrough with Indhu. Then Part G + Part H apply.

---

# DEVELOPER HANDBOOK 4 — KEERTHANA

## 1. Role
API Fetching / API Integration — **Met-Ocean Data Service** (ocean currents + wind + weather).

## 2. Component Owned
The environmental data source: fetch, subset, normalise, and cache ocean-current and wind data with provider fallback and health reporting.

## 3. Whole Project Architecture — THIS IS YOUR MODULE

```
EXTERNAL: Copernicus Marine / ERA5 (primary) — HYCOM / Open-Meteo (fallback)
        ↓
╔══════════════════════════════════════════╗
║ 4. MET-OCEAN DATA SERVICE                ║ ◄◄◄ THIS IS YOUR MODULE
║    fetch → subset → normalise → cache    ║
╚══════════════════════════════════════════╝
        ↓ currents.nc + wind.nc
Scene (Pavitra) → Detection (Indhu) → Characterisation (Nandha)
                                             ↓ slick.geojson
                              HINDCAST + FORECAST (Nandha)  ◄── my NetCDFs feed this
                                             ↓ origin_cloud.geojson
AIS (Krishnan) → Filter + Score + Rank (Nandha) → GIS UI (Indhu)
        ↓
COMPLETE SYSTEM
```

## 4. Where My Module Fits
I feed Nandha's drift engine. Without my NetCDFs the hindcast/forecast cannot run on real physics — it degrades to wind-only or constant-wind mode. My `provider_status.json` entries feed Indhu's API Monitoring page.

## 5. What I Receive
`{ "bbox": [W,S,E,N], "start": "<UTC>", "end": "<UTC>" }` — scene bbox ± margin, time window ± 48 h. Mocked as fixed JSONs during development. No teammate dependency.

## 6. What I Produce
- `currents.nc` — u/v surface current on a lat/lon/time grid (variable names documented in README and agreed once with Nandha).
- `wind.nc` — u10/v10 wind, same grid convention.
- Cached copies for both demo scenes in `data/metocean/`; `provider_status.json` entries for CMEMS, ERA5, Open-Meteo.
**Error classes:** `AUTH_FAILED`, `LICENCE_NOT_ACCEPTED`, `NO_DATA_FOR_PERIOD`, `TIMEOUT`, `UNAVAILABLE`.

## 7. What I Need To Do
- **Currents:** Copernicus Marine toolbox — use **GLORYS multiyear** (`GLOBAL_MULTIYEAR_PHY_001_030`) for historical scene dates; the analysis/forecast product does **not** cover old dates (this is the #1 trap — a 2017 request to the wrong product returns "no data" and looks like an outage). Fallback: HYCOM via OPeNDAP (no key).
- **Wind:** ERA5 via the **new** CDS (`cdsapi`; personal access token in `~/.cdsapirc` with `url: https://cds.climate.copernicus.eu/api`; the dataset **licence must be accepted on its download page** or requests fail misleadingly). Fallback: Open-Meteo (no key, instant).
- Subset to region, normalise units, cache, report provider health, tests, docs.

## 8. What I Own
Everything in §7 — the full path from providers to cached, drift-ready NetCDFs.

## 9. What I Do NOT Own
Drift physics (Nandha), scene selection (Pavitra/team), detection (Indhu), AIS (Krishnan), UI (Indhu).

## 10. How I Work Independently
External APIs only. Any ocean bbox works for development; demo bbox/dates arrive later from the chosen scenes.

## 11. Mock/Test Input
Fixed request JSONs; a tiny hand-built NetCDF as the format reference (it doubles as Nandha's mock — agree variable names with him once, then both work independently).

## 12. Testing
☐ Real 3-day retrieve for a test bbox from CMEMS and ERA5 ☐ Grids open in xarray with expected variables/units ☐ Failed request handled ☐ Invalid request rejected ☐ Wrong credentials → `AUTH_FAILED` + automatic fallback recorded ☐ Missing period → `NO_DATA_FOR_PERIOD` ☐ **Date-range test:** a 2017 date routes to GLORYS, not the forecast product ☐ Cached files served offline ☐ Output schema matches the contract.

## 13. Final Deliverables
☐ `metocean_service` module + CLI (`fetch-metocean --bbox ... --start ... --end ...`) ☐ Working fetching with fallback ☐ I/O contract + example input/output ☐ Error handling ☐ Tests ☐ Cached NetCDFs for both demo scenes ☐ Provider status reporting ☐ README with account setup (CMEMS login; CDS token + licence acceptance) ☐ Integration-ready component.

## 14. How My Component Integrates With Main System

```
Main System
     ↓  request JSON
[MET-OCEAN DATA SERVICE]
     ↓  currents.nc + wind.nc
Drift Engine (Nandha) → origin + forecast → rest of pipeline
```

On my error classes, the main system falls back to cache; if no cache exists, the drift engine runs wind-only/constant-wind mode with a UI badge.

## 15. Handover Requirements
One-command fetch works for both demo scene windows; cached NetCDFs committed; offline test passes; README; variable-name walkthrough with Nandha + handover walkthrough with Indhu. Then Part G + Part H apply.

---

# DEVELOPER HANDBOOK 5 — KRISHNAN

## 1. Role
API Fetching / API Integration — **AIS Data Service** (real AIS + synthetic AIS generator).

## 2. Component Owned
The vessel-track data source: real AIS ingestion + cleaning + interpolation, **and** the synthetic AIS generator. The generator is mandatory infrastructure, not a fallback — it supplies the ground truth (known culprit) for the attribution benchmark.

## 3. Whole Project Architecture — THIS IS YOUR MODULE

```
EXTERNAL: Danish DMA / MarineCadastre archives (real) — Generator (synthetic)
        ↓
╔══════════════════════════════════════════╗
║ 6. AIS DATA SERVICE                      ║ ◄◄◄ THIS IS YOUR MODULE
║    ingest → clean → interpolate          ║
║    + synthetic generator (known culprit) ║
╚══════════════════════════════════════════╝
        ↓ vessels.parquet
Scene (Pavitra) → Detection (Indhu) → Characterise → Drift → Origin (Nandha)
                                                        ↓ origin_cloud.geojson
                    FILTER + SCORE + RANK (Nandha)  ◄── my parquet feeds this
                                                        ↓ suspects.json
                              GIS UI + Integration (Indhu) → COMPLETE SYSTEM
```

## 4. Where My Module Fits
I feed Nandha's attribution engine and the UI's vessel layers. My `source` flag (real/synthetic) drives the UI's REAL/SYNTHETIC badge; my 50-scenario benchmark is what the team's top-1/top-3 attribution metric is computed on. My `provider_status.json` entries feed Indhu's Monitoring page.

## 5. What I Receive
`{ "bbox": [W,S,E,N], "start": "<UTC>", "end": "<UTC>", "mode": "real|synthetic", "n_vessels": <int>, "culprit": {origin lat/lon/time, behaviour options} }` (culprit only in synthetic mode). The origin-region JSON is mocked until Nandha's drift output exists. No teammate dependency.

## 6. What I Produce
- `vessels.parquet` per contract: MMSI, UTC timestamp, lat, lon, SOG, COG, heading, vessel type, dims, `source` flag, `culprit` flag (synthetic only). **Identical schema for real and synthetic.**
- The 50-scenario synthetic benchmark (each with one planted culprit), reproducible from a seed.
- `provider_status.json` entries for AIS sources.
**Error classes:** `ARCHIVE_UNAVAILABLE`, `EMPTY_REGION`, `PARSE_ERROR`.

## 7. What I Need To Do
- **Real path:** obtain Danish Maritime Authority open archive (proof scene — dense EU traffic) and/or MarineCadastre bulk CSV (US waters only; note there is **no real AIS for Indian waters** — that is exactly why the synthetic path exists). Parse, dedup, remove outliers, interpolate to 5-minute steps, assemble per-vessel trajectories, compute AIS-gap fields.
- **Synthetic path:** generator producing shipping-lane-style traffic + one culprit whose behaviour matches a discharge (passes through the given origin region at the given time; optional slowdown and AIS blackout), in the exact MarineCadastre schema.
- Optional stretch: AISStream.io live websocket for a "live" demo tab.
- Tests, docs, provider health reporting.

## 8. What I Own
Everything in §7 — both data paths, the benchmark set, and the schema guarantee.

## 9. What I Do NOT Own
Attribution scoring and filtering (Nandha), origin estimation (Nandha), detection (Indhu), scenes (Pavitra), met-ocean (Keerthana), UI (Indhu).

## 10. How I Work Independently
Real archives are public bulk downloads; the generator needs nothing external; the origin-region input is a mocked JSON. I never wait for a teammate.

## 11. Mock/Test Input
One sample day of Danish DMA CSV; a mock origin-region JSON for culprit planting; a fixed random seed for reproducible generation.

## 12. Testing
☐ Real: raw archive day → valid parquet (no duplicates, monotonic timestamps per MMSI, UTC verified) ☐ Interpolation: a known gap filled at 5-minute steps ☐ Failed/invalid request handled ☐ Missing archive → `ARCHIVE_UNAVAILABLE` ☐ Empty region → `EMPTY_REGION` ☐ Synthetic: planted culprit verifiably passes through the requested origin region/time ☐ **Schema-equality test:** real and synthetic parquet have identical columns and dtypes ☐ Benchmark regenerates identically from its seed ☐ Output matches the contract.

## 13. Final Deliverables
☐ `ais_service` module + CLIs (`fetch-ais` / `generate-ais`) ☐ Working real-AIS ingestion ☐ Synthetic generator ☐ I/O contract + example input/output ☐ Error handling ☐ Tests ☐ Cleaned real AIS for the proof scene ☐ Synthetic AIS for the headline scene ☐ 50-scenario benchmark + seed ☐ Provider status reporting ☐ README ☐ Integration-ready component.

## 14. How My Component Integrates With Main System

```
Main System
     ↓  request JSON
[AIS DATA SERVICE]
     ↓  vessels.parquet
Attribution Engine (Nandha) + UI vessel layers
```

On a `mode=real` failure, the main system falls back to `mode=synthetic` and switches the badge to SYNTHETIC.

## 15. Handover Requirements
One-command generation AND one-command real-ingest both work; benchmark committed with seed; schema tests green; README; column walkthrough with Nandha + handover walkthrough with Indhu. Then Part G + Part H apply.

---

---

## PART F — No-Blocking Guarantee (who depends on whom)

| Developer | Depends on (during development) | Who depends on their output |
|---|---|---|
| Indhu | Nobody (public datasets, own mocks) | Nandha (Engine A consumes the mask); everyone (mocks, main system) |
| Nandha | Nobody (mocks + analytic fields) | Indhu's UI (all four of his contract files) |
| Pavitra | Nobody (external APIs) | Indhu (detection input), UI |
| Keerthana | Nobody (external APIs) | Nandha (drift input) |
| Krishnan | Nobody (public archives + generator) | Nandha (attribution input), UI |

Only two one-time coordination points exist: (1) Keerthana ↔ Nandha agree NetCDF variable names in writing; (2) Krishnan ↔ Nandha confirm the parquet columns (already fixed by the contract).

## PART G — Common Handover Checklist (every developer, before handing to Indhu)

1. Working code, runnable from **one command**.
2. Input definition (schema + description).
3. Output definition (schema + description).
4. Example input file(s) committed.
5. Example output file(s) committed.
6. Test results (success, failure, edge cases) recorded.
7. Error/failure behaviour documented (which error class, when).
8. README documentation (setup, credentials, known issues).
9. Instructions to run the component.
10. Instructions to integrate the component (what the main system calls, what comes back).

## PART H — Definition of Done (every component)

A component is DONE only when: it works independently; mock input works; real input works where applicable; the output follows the agreed contract; tests exist and failure cases are tested; documentation exists; another developer can consume the output using only the contract; and the owner can demonstrate the component **without** the unfinished main system.

## PART I — Final Team Principle

```
INDHU     → ML + Main System        → mask, UI, mocks
NANDHA    → Core Engines            → slick, origin, forecast, suspects
PAVITRA   → Satellite Scene Service → scene_meta + GeoTIFF
KEERTHANA → Met-Ocean Service       → currents.nc + wind.nc
KRISHNAN  → AIS Service             → vessels.parquet + benchmark

        All completed components
                  ↓
        FINAL INTEGRATION (Indhu)
   mocks end-to-end → Pavitra → Keerthana → Krishnan
   → Nandha's engines → real ML weights → two demo scenes
   → replay mode → rehearsal
                  ↓
        COMPLETE WORKING POC
```

Everyone owns a component. Everyone works independently. Everyone tests independently. Everyone produces a defined output. Nobody sits idle. Integration happens through contracts. Freeze rule: once a component is integrated and green, nobody edits it without Indhu's sign-off.
