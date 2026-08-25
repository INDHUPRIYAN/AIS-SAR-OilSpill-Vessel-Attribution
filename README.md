# Ocean Trace 🛰️🌊

**AI-powered Maritime Oil-Spill Intelligence & Vessel Attribution Platform**

> From a satellite image to a ranked, evidence-backed list of suspect vessels — with every data source fallback-protected and every score explainable.

**Smart India Hackathon 2026 · Problem Statement 26143 · National Technical Research Organisation (NTRO)**
*"Leveraging satellite imagery to determine Oil spills at sea along with AIS data correlations to identify vessel responsible for the spill."*

Theme: Space Technology · Category: Software · Team: **The DevFounders**

---

## The Problem

Marine oil spills cause severe damage to coastal ecosystems, fisheries, and marine life — and in a large share of cases the polluting vessel is **never identified**. A satellite can see the slick, but a picture alone does not name the ship. Attribution requires connecting three worlds: **radar imagery**, **ocean physics**, and **vessel-tracking data** — in one automated, explainable pipeline.

## What OilGuard AI Does

```
Sentinel-1 SAR scene
        ↓
1  DETECT      Two-stage AI finds the oil and rejects look-alikes
        ↓
2  CHARACTERISE  Area · perimeter · centroid · shape · orientation · age estimate
        ↓
3  HINDCAST    Ocean-current + wind physics traces the slick BACKWARD
               to a probable origin point and time window
        ↓          (and FORWARD: spread forecast at +6 / +12 / +24 h)
4  RECONSTRUCT  Historic AIS vessel traffic around the origin window
        ↓
5  FILTER      Spatial · temporal · trajectory gates remove irrelevant vessels
        ↓
6  ATTRIBUTE   Transparent weighted scoring ranks suspect vessels —
               every score comes with a per-factor breakdown and a
               plain-language reason
        ↓
7  INVESTIGATE  GIS map interface: layers, time slider, incident replay,
               evidence panel, API monitoring
```

**Output — investigative support, not proof of guilt:**

```
1. VESSEL A — 0.86   passed the 90% origin region at 02:10 UTC,
                     slowed 14 → 6 kn, 47-minute AIS gap overlapping
                     the estimated discharge window
2. VESSEL B — 0.61
3. VESSEL C — 0.48
```

## Key Features

- 🛰️ **Two-stage AI detection** — YOLO11n screening (oil vs look-alikes: low-wind areas, biogenic films, rain cells, eddies) + U-Net pixel segmentation. CPU threshold fallback always available.
- 🌊 **Physics, not guesswork** — OpenDrift (OpenOil) particle model driven by real CMEMS currents and ERA5 wind; output is a probability cloud with an uncertainty ellipse, never a fake single point.
- 🚢 **Explainable attribution** — six transparent factors (proximity, temporal, trajectory, behaviour anomaly, AIS gap, vessel prior). No black box: no ground truth exists to train one, and investigators must see the *why*.
- 🗺️ **Investigation-grade UI** — MapLibre GIS workspace with semantic layers (spill = red, forecast = amber, hindcast = magenta, origin = gold), time slider, incident replay, per-vessel evidence panel.
- 📡 **API Monitoring + Key Management pages** — live health of every external provider, automatic fallback with a visible badge, admin key rotation with masked keys.
- 🔌 **Blocker-proof by design** — every external dependency has a fallback; the guaranteed path is always dependency-free; the full demo runs **offline from cache**.
- 🏷️ **Honesty built in** — every layer is badged REAL / CACHED / SYNTHETIC; fallbacks are never hidden; metrics are our own measured numbers.

## System Architecture

```
DATA SOURCES                      CORE ENGINE                     ATTRIBUTION & UI
─────────────────                 ─────────────────               ─────────────────
Sentinel-1 SAR ──┐                ┌─ Detection (ML) ─┐            ┌─ AIS filtering
  CDSE · ASF     │                │  YOLO11n screen  │            │  space·time·course
                 ├─ Ingestion ──► │  U-Net segment   ├─ slick ──► ├─ Weighted scoring
Ocean currents ──┤   & cache      └──────────────────┘   geo      │  + evidence text
  CMEMS · HYCOM  │                ┌─ Characterisation┐            └─ Ranked suspects
                 │                │  geometry · age  │                    │
Wind / met ──────┤                └──────────────────┘                    ▼
  ERA5·Open-Meteo│                ┌─ Drift engine ───┐            FastAPI backend
                 │                │  hindcast ◄──────┤            React + MapLibre UI
AIS vessels ─────┘                │  forecast ──────►│            API Monitoring page
  DMA·MarineCadastre·synthetic    └──────────────────┘            Key Management page
```

Five layers — data sources → preprocessing/cache → core engines → attribution → serving — connected by **frozen file contracts** (`slick.geojson`, `origin_cloud.geojson`, `forecast.geojson`, `vessels.parquet`, `suspects.json`, `provider_status.json`), so every module is independently buildable, testable, and replaceable.

## Machine Learning

| Model | Base | Dataset | Task | Measured |
|---|---|---|---|---|
| **Screen** | YOLO11n (fine-tuned) | **DARTIS** (DLR) — 3,225 oil objects + 2,290 look-alike patches, E. Mediterranean Sentinel-1 | "Is this oil at all?" — reject look-alikes | mAP@0.5 **0.626** · background FP rate **3.1%** |
| **Segment** | U-Net · ResNet-34 encoder | **Trujillo** (Zenodo) — 1,200 masked oil scenes + 685 oil-free, Sigma0 dB, 2048² tiled to 256² | "Exactly which pixels are oil?" — the mask everything downstream is computed from | binary IoU / precision / recall on the held-out Part III split |

Deliberately **not** ML: drift (validated physics beats a surrogate), geometry (deterministic maths), attribution ranking (no ground truth; explainability required). Attribution is validated instead on a **50-scenario synthetic benchmark** with planted culprits → top-1 / top-3 hit rate.

## External Data Services (all with fallbacks)

| Data | Primary | Fallback | Guaranteed path |
|---|---|---|---|
| Sentinel-1 SAR scenes | Copernicus Data Space Ecosystem | ASF Vertex (NASA) | Pre-downloaded scene cache |
| Ocean currents | Copernicus Marine (GLORYS reanalysis) | HYCOM via OPeNDAP | Cached NetCDF → wind-only drift |
| Wind | ERA5 (ECMWF, Climate Data Store) | Open-Meteo (no key) | Cached NetCDF → constant wind |
| AIS vessel tracks | Danish Maritime Authority / MarineCadastre | AISStream.io (live) | Synthetic generator (labelled) |
| Basemap | OpenStreetMap via MapLibre | — | Offline tile cache / SAR as basemap |

## Tech Stack

**ML** PyTorch · Ultralytics YOLO11 · segmentation-models-pytorch · ONNX ·
**Physics** OpenDrift (OpenOil) + in-house Euler fallback ·
**Geospatial** rasterio · xarray · shapely · GeoPandas · scikit-image · pyproj ·
**Backend** Python 3.11 · FastAPI · Pydantic · SQLAlchemy · PostgreSQL + PostGIS (SQLite for POC) ·
**Frontend** React · MapLibre GL · OpenStreetMap ·
**Data access** copernicusmarine · cdsapi · asf_search ·
**Infra** Docker (conda base for GDAL) · pytest · Playwright

## Repository Layout

```
oilguard/
├── backend/
│   ├── app/
│   │   ├── api/                # REST: investigations, layers, health, keys
│   │   ├── services/
│   │   │   ├── satellite/      # CDSE + ASF scene service
│   │   │   ├── metocean/       # CMEMS/ERA5 + fallbacks
│   │   │   ├── ais/            # real ingestion + synthetic generator
│   │   │   ├── detection/      # YOLO screen + U-Net inference + threshold fallback
│   │   │   ├── characterise/   # geometry · damping ratio · Fay age
│   │   │   ├── drift/          # OpenDrift hindcast/forecast + Euler fallback
│   │   │   ├── attribution/    # gates → scoring → explanations
│   │   │   └── integrations/   # provider adapters · fallback chains · health
│   │   └── schemas/            # Pydantic contract models
│   ├── ml/                     # training / eval / export scripts
│   └── tests/
├── frontend/                   # React + MapLibre investigation UI
├── contracts/                  # frozen JSON/GeoJSON schemas + mocks/
├── data/                       # cached scenes, NetCDF, AIS, tiles (gitignored)
├── docker/                     # conda-based image (OpenDrift/GDAL)
└── docs/                       # specs, handbooks, model cards, data card
```

## Quick Start

### Prerequisites
- Python 3.11+, Node 18+, conda (for the OpenDrift/GDAL environment)
- Free accounts (only needed for live fetching — the demo runs fully from cache):
  Copernicus Data Space · Copernicus Marine · ECMWF/CDS (**accept the ERA5 licence on the dataset page**) · NASA Earthdata

### Run

```bash
# 1. clone + environments
git clone <repo-url> && cd oilguard
conda env create -f docker/environment.yml && conda activate oilguard
pip install -r backend/requirements.txt

# 2. credentials (never committed)
cp .env.example .env          # add keys, or skip for offline mode

# 3. backend
uvicorn backend.app.main:app --reload      # http://localhost:8000/docs

# 4. frontend
cd frontend && npm install && npm run dev  # http://localhost:5173

# 5. run an investigation end-to-end from the bundled demo cache (no network)
#    UI → Investigations → open a demo incident → Run (Replay mode)
```

### Tests

```bash
pytest backend/tests                 # unit + contract tests
npx playwright test                  # UI end-to-end (runs on contracts/mocks)
```

## Honest Limitations

- **Retrospective by nature** — attribution is a hindcasting problem; Sentinel-1 revisits any point only every few days, so even operational systems (e.g. EMSA CleanSeaNet) work near-real-time, not live. A watcher/scheduler for continuous monitoring is on the roadmap; the analysis itself doesn't change.
- **Screening is domain-limited** — the YOLO screen is trained on Mediterranean DARTIS imagery and fails safe (steps aside) outside it; a cross-domain fine-tune on Trujillo Parts 1–2 is planned.
- **No real AIS exists for Indian waters** — the headline scene uses clearly-labelled synthetic AIS (explicitly permitted by the problem statement); a second scene demonstrates the pipeline on real AIS.
- **Drift carries uncertainty** — we display it (growing confidence ellipse) instead of hiding it.
- **Scores are attribution likelihood, not proof of guilt.**

## Team — The DevFounders

| Member | Ownership |
|---|---|
| **Indhu Priyan** | ML lead (Model 2 — U-Net segmentation) · main system (backend, GIS UI, monitoring) · final integration |
| **Mohan Kumar** | Model 1 — YOLO11n screening detector on DARTIS (trained & evaluated end-to-end) |
| **Nandha Kumar** | Core engines: characterisation · drift · attribution |
| **Pavitra** | Satellite Scene Service (CDSE / ASF) |
| **Keerthana** | Met-Ocean Data Service (CMEMS / ERA5 + fallbacks) |
| **Krishnan** | AIS Data Service (real ingestion + synthetic generator + benchmark) |

## References & Data Credits

1. Smart India Hackathon 2026 — Problem Statement 26143 (NTRO) — https://sih.gov.in
2. Copernicus Data Space Ecosystem — Sentinel-1 SAR — https://dataspace.copernicus.eu
3. Copernicus Marine Service — GLORYS reanalysis — https://data.marine.copernicus.eu/product/GLOBAL_MULTIYEAR_PHY_001_030
4. ERA5, Copernicus Climate Data Store (ECMWF) — https://cds.climate.copernicus.eu
5. MarineCadastre AccessAIS — https://marinecadastre.gov/accessais/
6. Yang & Singha (DLR) — DARTIS Sentinel-1 oil-spill dataset — https://doi.org/10.1594/PANGAEA.980773
7. Trujillo-Acatitla et al. — Sentinel-1 oil-spill dataset — Zenodo records [8346860](https://zenodo.org/records/8346860) · [8253899](https://zenodo.org/records/8253899) · [13761290](https://zenodo.org/records/13761290)
8. Ronneberger et al. (2015) — U-Net — https://arxiv.org/abs/1505.04597
9. Dagestad et al. (2018) — OpenDrift — https://doi.org/10.5194/gmd-11-1405-2018

Contains modified Copernicus Sentinel data and Copernicus Marine/Climate service information. Datasets used under their respective open licences with attribution.

## Links

- 🎬 **Demo video:** *[link coming soon]*
- 🌐 **Live demo:** *[link coming soon]*

---

*Built for Smart India Hackathon 2026 by The DevFounders. Ocean Trace supports investigators — final attribution always rests with the competent maritime authority.*
