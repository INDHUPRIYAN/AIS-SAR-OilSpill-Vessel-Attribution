# OceanTrace — System Specification & Build Plan

**SIH 2026 · Problem Statement 26143 (NTRO)**
*Leveraging satellite imagery to determine oil spills at sea along with AIS data correlations to identify the vessel responsible for the spill.*

**Document type:** Production-grade POC specification
**Datasets locked:** DARTIS + Trujillo (Zenodo Sentinel-1)
**Implementation tooling:** Claude Code (primary development assistant)

---

## 1. Goal

Build a complete, modular, production-grade investigation system that takes a Sentinel-1 SAR scene and ends with a ranked, explainable list of suspect vessels — with a GIS-based investigation interface, an API monitoring page, API fallback handling, and API key management built in.

Guiding principles:

1. **No single external dataset, API, library, or machine can stop the workflow.** Every stage has a fallback; the last fallback in every chain is dependency-free.
2. **Real data first.** Real Sentinel-1 SAR, real currents (CMEMS/HYCOM), real wind (ERA5/Open-Meteo), real AIS where available. Synthetic data only where real data does not exist (AIS in Indian waters), and always labelled as synthetic in the UI.
3. **Explainability over black boxes.** Attribution scores must show their factor breakdown. Drift outputs must show uncertainty.
4. **Contracts first.** Modules communicate through frozen file/JSON contracts so any module can be built, tested, stubbed, or replaced independently.

---

## 2. End-to-End Investigation Workflow

```
Satellite Imagery (Sentinel-1 SAR)
        ↓
Oil Spill Detection (2-stage ML)
        ↓
Spill Characterisation (geometry + age)
        ↓
Ocean Currents + Wind + Weather
        ↓
Hindcasting (backward drift → origin cloud)
        ↓
Probable Spill Origin (space + time window)
        ↓
AIS Historical Reconstruction
        ↓
Relevant Vessel Filtering
        ↓
Vessel Correlation & Scoring
        ↓
Vessel Attribution Ranking (explainable)
        ↓
GIS Visual Investigation Interface
```

In parallel, a forward drift **forecast** predicts future slick movement and spread.

---

## 3. System Architecture

Five layers, each independently replaceable:

| Layer | Purpose | Key components |
|---|---|---|
| 1. Data Sources | External data acquisition | CDSE/Sentinel-1, CMEMS, ERA5, AIS sources |
| 2. Preprocessing & Cache | Normalise, align, cache locally | SAR calibration, AIS cleaning, NetCDF regional cache |
| 3. Core Engine | The science | Detection ML, characterisation, drift engine |
| 4. Attribution | The investigation | Vessel filtering, scoring, ranking |
| 5. Serving | The product | FastAPI backend, database, React/Streamlit GIS UI, API monitoring |

### 3.1 Repository layout

```
oceantrace/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entry
│   │   ├── api/                    # REST endpoints (investigations, layers, apis, keys)
│   │   ├── core/                   # config, security, logging
│   │   ├── services/
│   │   │   ├── satellite/          # scene search/download/preprocess
│   │   │   ├── detection/          # ML inference (screen + segment)
│   │   │   ├── characterise/       # geometry + age
│   │   │   ├── metocean/           # currents/wind/weather adapters
│   │   │   ├── drift/              # hindcast + forecast (OpenDrift / fallback)
│   │   │   ├── ais/                # ingest, clean, interpolate, synthetic generator
│   │   │   ├── attribution/        # filter, score, rank, explain
│   │   │   └── integrations/       # provider adapters + fallback chains + health
│   │   ├── models/                 # DB models (SQLAlchemy)
│   │   └── schemas/                # Pydantic contracts
│   ├── ml/                         # training scripts, eval, export (run by team)
│   └── tests/
├── frontend/                       # React + MapLibre (or Streamlit fallback)
├── data/                           # cached scenes, NetCDF, AIS, tiles (gitignored)
├── contracts/                      # frozen JSON/GeoJSON schemas + mock files
├── docker/                         # environment (conda-based image for OpenDrift/GDAL)
└── docs/
```

### 3.2 Frozen data contracts (defined at hour 0)

| Contract | Producer | Consumer | Content |
|---|---|---|---|
| `slick.geojson` | Detection + Characterisation | Drift, UI | Slick polygon(s), confidence, area km², perimeter, centroid, major/minor axis, orientation, damping ratio, age estimate |
| `origin_cloud.geojson` | Drift (hindcast) | Attribution, UI | Particle cloud as points with (lat, lon, time, weight); origin window summary; confidence ellipse per timestep |
| `forecast.geojson` | Drift (forecast) | UI | Predicted slick positions/spread at +6/+12/+24 h with uncertainty |
| `vessels.parquet` | AIS pipeline | Attribution, UI | Cleaned, interpolated tracks: MMSI, timestamp (UTC), lat, lon, SOG, COG, heading, vessel type, dims, source flag (real/synthetic) |
| `suspects.json` | Attribution | UI | Ranked vessels with total score + per-factor breakdown + plain-language reason |

Mock versions of all five files live in `contracts/mocks/` from day one so the UI and every downstream module can be developed before upstream modules are finished.

Conventions enforced everywhere: **WGS84 (EPSG:4326)** for all vector data, **UTC** for all timestamps, conversion only at ingest boundaries, one assert-test per boundary.

---

## 4. Module Specifications

### 4.1 Satellite Imagery Ingestion

- **Primary:** Copernicus Data Space Ecosystem (OData / Sentinel Hub Processing API), OAuth client, token-refresh wrapper (access token ~10 min).
- **Fallback:** ASF Vertex via `asf_search` (Earthdata login, no OAuth complexity).
- **Guaranteed path:** pre-downloaded scenes in `data/scenes/` — the demo never depends on live download.
- Preprocessing: calibration to Sigma0 (dB), speckle filtering, land masking. **SNAP is avoided**; scenes are pre-processed before the event (pyroSAR or prepared calibrated products). Normalisation constants (dB clip range) live in one config shared by training and inference.

### 4.2 Oil Spill Detection ML (two-stage)

| Stage | Model | Trained on | Output |
|---|---|---|---|
| Screen | YOLOv8/11 detector or ResNet-18 patch classifier | **DARTIS** (oil vs look-alike; look-alikes categorised: low wind, internal waves, biogenic films, rain cells, eddies, RFI) | Candidate boxes + confidence; look-alike rejection |
| Delineate | U-Net (ResNet-34 encoder, ImageNet pretrained, AMP fp16) | **Trujillo** (binary masks, Sigma0 dB, tiled to 256×256) | Pixel segmentation mask + per-pixel confidence |

- Training is done **by the team before the event** on the local RTX 4050 (batch 12–16 at 256², checkpoints every epoch); weights are committed. Claude Code produces training/preprocessing/eval scripts and integrates the exported model (ONNX or TorchScript) into the FastAPI inference service.
- **Guaranteed detection path (Plan C):** adaptive thresholding + morphological cleanup (scikit-image), no GPU, no model — always available.
- Honest metrics reported: binary IoU on Trujillo test split; per-phenomenon false-positive rates on DARTIS. Pixel accuracy is never reported (sea-class dominance makes it meaningless).

### 4.3 Spill Characterisation (deterministic, no ML)

From the mask: area (km²), perimeter, centroid (lat/lon), best-fit ellipse (major/minor axis, orientation), shape descriptors, polygon boundary (GeoJSON). Age proxy: backscatter damping ratio (slick vs surrounding sea) + Fay spreading law estimate. Implemented with scikit-image `regionprops`, shapely, rasterio.

### 4.4 Met-Ocean Data Service

| Data | Primary | Fallback 1 | Fallback 2 (dependency-free) |
|---|---|---|---|
| Ocean currents | Copernicus Marine toolbox — **GLORYS multiyear** (`GLOBAL_MULTIYEAR_PHY_001_030`) for historical scenes; `GLOBAL_ANALYSISFORECAST_PHY_001_024` only for recent dates | HYCOM GOFS via OPeNDAP (no key) | Zero-current, wind-only drift |
| Wind | ERA5 via new CDS (`cdsapi`, personal access token, licence accepted per dataset) | Open-Meteo (no key, instant) | Constant wind from scene metadata |
| Waves/weather | CMEMS wave products / Open-Meteo Marine | NOAA GFS/NOMADS | Omitted (documented) |

All met-ocean data for the demo scenes is downloaded once and cached as regional NetCDF in `data/metocean/`; the live APIs remain available but the demo path reads from cache.

### 4.5 Drift Engine (Hindcast + Forecast)

- **Primary:** OpenDrift `OpenOil` — particles seeded across the slick polygon; backward run (negative timestep) 12–24 h → origin probability cloud; forward run → forecast with confidence ellipse growing over time. Environment built in conda/Docker **before the event** (GDAL/cartopy chain is the highest-risk install).
- **Fallback:** OpenDrift `OceanDrift` (fewer dependencies).
- **Guaranteed path:** in-house ~30-line integrator — velocity = surface current + 3% wind (leeway), backward/forward Euler, Gaussian diffusion per step. Physically defensible and dependency-free.
- Output is always a **probability cloud with an uncertainty ellipse**, never a single point. No accuracy claim is made for drift; uncertainty is displayed honestly.

### 4.6 AIS Pipeline

- **Real AIS:** Danish Maritime Authority open archive (dense European traffic) and/or MarineCadastre (US waters only) for the proof scene. Schema reference: MarineCadastre CSV (MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, VesselType, Status, Length, Width, Draft).
- **Synthetic AIS (mandatory infrastructure, not just fallback):** generator producing tracks in the identical schema for the Indian-waters headline scene and for the evaluation harness (50 simulated spill events with a known culprit → top-1/top-3 hit-rate metric). Synthetic data is flagged in the schema and labelled in the UI.
- Processing: dedup, outlier removal, 5-minute interpolation, per-vessel trajectory assembly, AIS-gap detection.
- Optional live garnish: AISStream.io websocket feed on a separate "live" tab.

### 4.7 Vessel Filtering

Applied before scoring so irrelevant traffic never reaches attribution:

1. **Spatial gate:** track must intersect a buffered origin cloud (high-probability region + margin).
2. **Temporal gate:** presence within the origin time window ± buffer.
3. **Trajectory gate:** heading/course roughly compatible with slick major axis (discharge trails behind a moving vessel).
4. Vessels failing all gates are excluded with the reason recorded (visible in UI as "filtered out: outside time window").

### 4.8 Vessel Attribution (explainable scoring)

Transparent weighted score — deliberately **not** a trained classifier (no ground truth exists; explainability is required by the use case):

| Factor | Signal |
|---|---|
| Proximity | Depth of track inside the origin probability cloud (weighted by cloud density) |
| Temporal correlation | Time alignment between presence and estimated discharge window |
| Trajectory correlation | Angle between vessel course and slick major axis; path-overlap length |
| Behavioural anomalies | Unusual slowdown, course change, loitering |
| AIS gaps | Transmission blackout over the origin window (strong suspicion signal) |
| Vessel prior | Type/draft prior (tanker, bulk carrier > passenger ferry) |

Output per vessel: total score, per-factor sub-scores, and a generated plain-language explanation ("Vessel A passed through the 90% origin region at 02:10 UTC, slowed from 14 to 6 kn, and had a 47-minute AIS gap overlapping the estimated discharge window"). Weights are configurable and shown in the UI. Validation: top-1/top-3 hit rate on the synthetic injected-event benchmark.

### 4.9 GIS Investigation Interface

- **Stack:** React + MapLibre GL (primary) with OpenStreetMap raster tiles **pre-downloaded for the demo bounding boxes** (offline-capable); the SAR scene itself is available as a basemap layer. Fallback UI: Streamlit + Folium.
- Investigation workflow: create/select investigation → pick scene → run/replay pipeline → explore layers.
- Layers (individually toggleable): SAR scene, detected slick mask + boundary, geometry annotations, origin probability heatmap + confidence ellipse, hindcast particle animation, forecast spread (+6/+12/+24 h), AIS tracks (coloured by suspicion), filtered-out vessels (dimmed), ranked suspect panel with factor bars, time slider.
- Analytics: metrics table (IoU, per-phenomenon FP rates, top-k hit rate), spill statistics, data-source badges (REAL / CACHED / SYNTHETIC per layer).

---

## 5. API Integration Layer

Adapter pattern; the application never talks to a provider directly.

```
Application code  →  Service interface (e.g. WindProvider)
                        →  Adapter: ERA5    (primary)
                        →  Adapter: OpenMeteo (fallback 1)
                        →  Adapter: StaticCache (fallback 2, always succeeds)
```

Requirements per provider adapter:

- Configuration (base URL, credentials reference, timeouts, retry policy) stored in DB/`.env`, never in code, never in the frontend bundle.
- Standard error taxonomy: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`.
- Circuit breaker: after N consecutive failures the chain skips to the next provider for a cooldown period.
- Every call logged to an `api_calls` table (provider, endpoint, status, latency, error) — this powers the monitoring page.
- Fallback events are surfaced to the UI ("Wind: served by Open-Meteo — ERA5 unavailable"), never hidden in logs.

---

## 6. API Monitoring Page

A dedicated page listing **every** external dependency. Per API:

| Field | Example |
|---|---|
| API name / purpose | ERA5 — historical wind for drift |
| Current status | WORKING / FAILED / DEGRADED |
| Last response code / time | 200 · 340 ms |
| Last successful request | 2026-08-24 14:02 UTC |
| Last failed request + error | 2026-08-24 13:55 UTC · AUTH_FAILED (licence not accepted) |
| Primary / alternatives | ERA5 (primary), Open-Meteo, StaticCache |
| **Currently active provider** | Open-Meteo ✅ (fallback in use — badge shown) |
| Actions | Test now · View recent calls · Switch provider (admin) |

Implementation: background health-check scheduler (lightweight ping per provider every 60 s + passive status from real traffic), `/api/health` endpoints, WebSocket or polling for live status on the page. Status chips are also mirrored as a small strip in the investigation UI header so a judge can see system health at a glance.

## 7. API Key Management Page (admin)

- List providers with masked keys (`••••1234` — last 4 only, never the full key).
- Actions: change key, save (encrypted at rest / stored server-side in `.env`/DB secret table), **Test connection** (runs a real authenticated ping and reports the exact failure class), view auth status.
- Security rules: keys never appear in frontend code or bundles; never returned in full by any API; all key endpoints require admin auth; audit log of key changes.

---

## 8. Datasets

| Dataset | Role | Format | Access |
|---|---|---|---|
| **DARTIS** (Yang & Singha, DLR — Eastern Mediterranean 2019) | Screening / look-alike discrimination; ~1,365 oil patches (3,225 oil objects) + ~2,290 look-alike/other patches; look-alikes categorised by phenomenon | 640×640 JPEG, object annotations | PANGAEA, doi:10.1594/PANGAEA.980773 (open) |
| **Trujillo** (Zenodo, 3 parts: 8346860 / 8253899 / 13761290) | Segmentation; 1,200 oil train/val images + masks, 685 no-oil, test split 150/150/150 | 2048×2048×2 TIFF, Sigma0 dB, binary masks | Zenodo (open, but 40–60 GB total) |

Preparation pipeline (team executes; Claude Code writes the scripts):

1. Download DARTIS fully; download Trujillo **Part III first** (test harness), then Parts I–II with a **tile-and-discard** script (download → tile 256×256 → keep tiles ≥1% oil + matched hard negatives → delete source TIFF).
2. Store tiles as uint8/float16 memory-mapped `.npy` with dB clip constants recorded in config.
3. Never merge the two datasets into one training set (different radiometry, format, label geometry) — one model per dataset per stage.
4. Verify labels visually on a random sample; freeze train/val/test splits; record counts in `docs/data_card.md`.

---

## 9. External Services & Fallback Register

| # | Dependency | Primary | Fallback | Guaranteed path | Status source |
|---|---|---|---|---|---|
| 1 | Sentinel-1 scenes | CDSE (OData/Sentinel Hub) | ASF Vertex | Pre-downloaded scenes | Monitoring page |
| 2 | Ocean currents | CMEMS (GLORYS for historical) | HYCOM OPeNDAP | Cached NetCDF → zero-current mode | Monitoring page |
| 3 | Wind | ERA5 (new CDS) | Open-Meteo (no key) | Cached NetCDF → constant wind | Monitoring page |
| 4 | Weather/waves | CMEMS / Open-Meteo Marine | NOAA NOMADS | Omitted, documented | Monitoring page |
| 5 | AIS | Danish DMA / MarineCadastre | AISStream live | Synthetic generator (known culprit) | Monitoring page |
| 6 | Basemap | MapLibre + OSM tiles | — | Pre-downloaded tile cache / SAR-as-basemap | Monitoring page |
| 7 | Detection model | U-Net + DARTIS screen | U-Net only | Threshold + morphology (no GPU) | Pipeline log |
| 8 | Drift model | OpenDrift OpenOil | OpenDrift OceanDrift | 30-line current+3%-wind integrator | Pipeline log |
| 9 | GPU | Local RTX 4050 | Kaggle/Colab | Plan-C detection needs no GPU | — |
| 10 | Demo | Live run from cache | Replay mode (pre-computed contract files) | Recorded video | — |

---

## 10. Blocker → Resolution Register

### Data blockers (resolved)

| # | Blocker | Resolution |
|---|---|---|
| 1 | MKLab dataset requires supervisor request | Removed; DARTIS + Trujillo locked |
| 2 | Real AIS limited (none for Indian waters) | Danish DMA/MarineCadastre for proof scene; synthetic AIS (identical schema, labelled) for headline scene + eval |
| 3 | Historical currents | CMEMS **GLORYS multiyear** for historical dates + HYCOM fallback + cache |
| 4 | Wind data | ERA5 (new CDS, licence accepted) + Open-Meteo + cache |
| 5 | Live satellite risk | Scenes pre-downloaded; live path optional |
| 6 | Map service failure | MapLibre + pre-downloaded OSM tiles + SAR basemap |
| 7 | No drift-ML dataset | Physics model (OpenDrift); ML surrogate explicitly out of scope |
| 8 | No AIS-anomaly labels | Rule-based anomaly flags + unsupervised outliers; validated on synthetic benchmark |
| 9 | GPU/training infra | Pre-trained on RTX 4050 before event; Kaggle backup; Plan-C needs no GPU |
| 10 | No real ground truth for attribution | Synthetic injected-event benchmark (top-1/top-3 hit rate) + held-out dataset test splits |

### Execution blockers (the second half)

| # | Blocker | Resolution |
|---|---|---|
| 11 | Trujillo size (40–60 GB) | Part III first; tile-and-discard; uint8 memory-mapped store |
| 12 | OpenDrift/GDAL install chain | Conda/Docker env built and smoke-tested before event; `environment.yml` committed; Plan-C integrator exists from hour 1 |
| 13 | SAR preprocessing domain gap | No SNAP; pre-calibrated demo scenes; one shared normalisation config for train + inference |
| 14 | CRS/UTC misalignment (IST offset!) | WGS84 + UTC everywhere; convert at ingest only; boundary asserts |
| 15 | Integration collapse | Contracts frozen at hour 0; UI on mocks from hour 1; modules swappable |
| 16 | Demo-day failure | Degradation ladder: live-from-cache → replay mode → recorded video; zero-network capable |
| 17 | Model weak on chosen scene | Guaranteed threshold detector; demo scene selected by verifying model output on it first |

---

## 11. Responsibility Split

### Claude Code builds

- **Frontend:** React app, pages (Investigation, API Monitoring, API Keys), MapLibre map + layers, charts/tables, navigation, loading/error states.
- **Backend:** FastAPI endpoints, SQLAlchemy models + DB (SQLite for POC, Postgres-ready), auth (admin for key management), integration layer with adapters/fallback chains/circuit breakers, health scheduler, ML inference service, AIS processing, drift orchestration, characterisation module.
- **Integration:** frontend↔backend, ML↔backend, contracts, WebSocket/polling status, replay mode.
- **Quality:** unit + integration tests, API tests, error handling, refactoring, docs, seed/mock data, Dockerfiles.

### Team must do (Claude Code cannot)

| Task | Notes |
|---|---|
| Dataset acquisition | Download DARTIS + Trujillo, verify labels, organise splits |
| Model training & selection | Run training on RTX 4050, evaluate, pick deployed weights, judge "good enough" |
| API credentials | Register CDSE, ECMWF/CDS (accept ERA5 licence!), Copernicus Marine, AISStream, GFW; configure via Key Management page |
| Real AIS acquisition | Pull Danish DMA / MarineCadastre archives for proof scene |
| Real satellite scenes | Choose + download the two demo scenes; verify model performs on them |
| Met-ocean data pulls | One-time cached downloads for scene regions/dates |
| Real-world validation & demo rehearsal | Run smoke tests, rehearse degradation ladder |

---

## 12. Demo Strategy

- **Scene A (headline):** documented Indian-waters spill (e.g. Chennai/Ennore 2017 area) — real SAR + real currents + real wind + synthetic AIS (labelled). NTRO relevance.
- **Scene B (proof):** Gulf of Mexico or Danish waters — real SAR **and real AIS**, pre-empting the "is the AIS real?" question.
- **Benchmark:** 50 synthetic injected spill events with known culprits → report top-1/top-3 attribution hit rate.
- Degradation ladder rehearsed: (1) live pipeline from cached inputs (~20–30 s), (2) replay mode loading pre-computed contract files, (3) recorded video.
- Honest metrics slide: binary IoU on Trujillo test, per-phenomenon FP table from DARTIS, top-k hit rate, drift uncertainty ellipse — no inflated claims.

---

## 13. Tech Stack Summary

| Area | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy, Pydantic, SQLite (POC) |
| ML | PyTorch + segmentation-models-pytorch (U-Net/ResNet-34), Ultralytics YOLO, ONNX export, AMP |
| Geospatial | rasterio, xarray, shapely, geopandas, scikit-image, pyproj |
| Drift | OpenDrift (OpenOil/OceanDrift) in conda/Docker; in-house Euler integrator fallback |
| Data access | copernicusmarine, cdsapi (new CDS), asf_search, requests |
| Frontend | React + MapLibre GL + deck.gl layers (fallback: Streamlit + Folium) |
| Infra | Docker (conda base for GDAL), .env secrets, pytest, GitHub |

---

## 14. Success Criteria

1. End-to-end run on both demo scenes from the UI with no live network required.
2. Every UI layer renders from the frozen contracts; replay mode works.
3. API Monitoring page shows live status of all providers; killing a primary visibly fails over to a fallback with a UI badge.
4. API keys changeable and testable from the admin page; keys never exposed.
5. Metrics reported: segmentation IoU, per-phenomenon false-positive rates, top-1/top-3 attribution hit rate on the synthetic benchmark.
6. Attribution output is explainable: every ranked vessel shows its factor breakdown and a plain-language reason.
7. Synthetic layers clearly labelled; no claim the system cannot back.
