# OceanTrace — SAR Oil-Spill Detection & AIS Vessel Attribution

Welcome to **OceanTrace** (SIH 2026 · PS 26143), the system for tracking oil spills via satellite imagery (SAR), fetching metocean data, interpolating AIS vessel coordinates, running drift trajectory simulation, and attributing the spill to culprit vessels.

## Quickstart (fresh clone → demo)

```powershell
# 1. Clone + Python env (Windows; on Linux/macOS use .venv/bin/python)
git clone https://github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution.git
cd AIS-SAR-OilSpill-Vessel-Attribution
python -m venv .venv
.venv\Scripts\python -m pip install -e .

# 2. Config (optional for the offline demo — it runs with zero credentials)
copy .env.example .env        # then fill in what you have

# 3. Model weights (pulled from the GitHub release, sha256-verified, never in git)
powershell -ExecutionPolicy Bypass -File scripts\get_weights.ps1

# 4. One-command demo: backend :8000 + dashboard :5173
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

Then open **http://localhost:5173**. On Linux/macOS use `scripts/get_weights.sh` and `scripts/run_demo.sh`. If `data/runs/` is empty the demo script prints exactly how to seed a run (the dashboard replays cached run artefacts from `data/runs/`).

GPU training is a separate install — see the header of `main_system/requirements.txt` (PyTorch must come from the CUDA index, then `pip install -e ".[train,gpu]"`).

### Docker (one command, whole stack)

```bash
cp .env.example .env          # optional; the stack boots with zero credentials
docker compose up --build     # backend :8000 + frontend :5173
```

The backend container pulls weights at **start** (never baked into the image) via `scripts/get_weights.sh`; local `data/` is bind-mounted so cached scenes and runs work offline. Future-facing services (PostGIS, MinIO, Redis) sit behind a profile: `docker compose --profile extras up`.

## Architecture

A Sentinel-1 SAR scene enters through `scene_service` (CDSE → ASF → local cache), is screened for oil vs look-alike by Model 1 (YOLO, DARTIS) and delineated by Model 2 (U-Net, Trujillo) inside the `/detect` service — with a threshold+morphology fallback so detection **always** returns; the slick polygon is characterised (Engine A), drifted back/forward in time with winds and currents from `metocean_service` (Engine B, an in-house Lagrangian particle integrator — dependency-free by design; OpenDrift integration is future work), and matched against interpolated AIS trajectories from `ais_service` to score candidate culprit vessels (Engine C). The FastAPI backend orchestrates the pipeline, persists run artefacts under `data/runs/<run_id>/`, and the React/MapLibre dashboard replays them. Every external provider has a fallback chain, so the demo works fully offline from cached artefacts. Full design: **[OilGuard_System_Design_v2.md](OilGuard_System_Design_v2.md)** (deployment: Part VII).

## Service map

| Path | What it is | Runs as |
|---|---|---|
| `main_system/backend/` | FastAPI app (`backend.main:app`) — investigation API, `/detect`, replay, monitoring, key management | `uvicorn backend.main:app --port 8000` (from `main_system/`) |
| `main_system/frontend/` | React + MapLibre/deck.gl dashboard | `npm run dev` (port 5173, proxies `/api` → 8000) |
| `main_system/ml/` | Model training/eval/export (DARTIS YOLO + Trujillo U-Net) | `python -m ml.<tool>` |
| `contracts/` | Frozen schemas + mock files — the integration law | imported everywhere |
| `analysis_engines/` | Engines A/B/C: characterise, drift, attribution | `python -m engines.<name>` |
| `scene_service/` | Sentinel-1 retrieval (CDSE → ASF → LocalCache) | `python -m satellite.cli` |
| `metocean_service/` | Currents + wind (CMEMS/HYCOM, ERA5/Open-Meteo → StaticCache) | `python -m metocean.cli` |
| `ais_service/` | AIS ingest/interpolate/synthetic (DMA, MarineCadastre → Synthetic) | `python -m ais.cli` |
| `docker/` + `docker-compose.yml` | Container build + one-command local stack | `docker compose up --build` |
| `scripts/` | `get_weights.*`, `run_demo.*`, data download helpers | see Quickstart |

## System Architecture & Owners
- **`contracts/`**: Shared interfaces, schemas, and mocks. Indhu writes, all read.
- **`main_system/`** (Indhu Priyan): Central FastAPI system, front-end dashboard (React/MapLibre), Model 2 — U-Net slick segmentation (Trujillo), pipeline orchestration and final integration.
- **Model 1 — DARTIS screening detector** (Mohan Kumar M): YOLO oil-vs-look-alike screening model — DARTIS dataset preparation, training and evaluation (`main_system/ml/dartis.py`, `train_yolo.py`).
- **`analysis_engines/`** (Nandha): Physical drift simulation (in-house Lagrangian/Euler integrator), characterization, and attribution scoring.
- **`scene_service/`** (Pavitra): Sentinel-1 SAR scene retriever (CDSE & ASF APIs).
- **`metocean_service/`** (Keerthana): Winds and currents data query (GLORYS, ERA5, HYCOM, OpenMeteo).
- **`ais_service/`** (Krishnan): AIS trajectory downloader, interpolator, synthetic generator, and benchmark suite.

## Team
| Member | Module | Responsibility |
|---|---|---|
| Indhu Priyan | `main_system/` | Main system, Model 2 (U-Net segmentation), frontend, final integration |
| Mohan Kumar M | Model 1 (DARTIS) | YOLO screening detector: oil vs look-alike |
| Nandha | `analysis_engines/` | Characterisation, drift (hindcast/forecast), attribution scoring |
| Pavitra | `scene_service/` | Sentinel-1 scene retrieval (CDSE / ASF) |
| Keerthana | `metocean_service/` | Currents & wind (CMEMS, ERA5, HYCOM, Open-Meteo) |
| Krishnan | `ais_service/` | AIS ingestion, interpolation, synthetic generator |

## Environment variables

All configuration is by environment variable (or `.env`, which is gitignored and read at backend start). **[`.env.example`](.env.example)** is the authoritative, commented list — CDSE/Earthdata (scenes), CMEMS (currents), CDS/ERA5 (wind), AISStream, `SECRET_KEY`/`ADMIN_TOKEN`, `DATA_ROOT`, `HOST`/`PORT`. Two extras used by packaging:

| Variable | Used by | Meaning |
|---|---|---|
| `OILGUARD_WEIGHTS_URL` | `scripts/get_weights.*`, backend container | Base URL for `screen.onnx`/`segment.onnx` (defaults to the repo's `weights-v1` GitHub release) |
| `DATA_ROOT` | backend | Data directory (compose sets `/app/data` over the `./data` bind mount) |

Credentials can also be entered at runtime on the dashboard's Key Management page (stored encrypted in the DB; overrides `.env`). Verify everything at once: `cd main_system` then `..\.venv\Scripts\python -m backend.verify_credentials`.

## Running tests

`pytest.ini` at the repo root wires up all module roots (importlib mode — do not remove it), so from the repo root:

```bash
.venv\Scripts\python -m pytest                        # entire repo, all modules
.venv\Scripts\python -m pytest main_system/tests      # Indhu — backend + ML pipeline
.venv\Scripts\python -m pytest analysis_engines/tests # Nandha — engines A/B/C
.venv\Scripts\python -m pytest scene_service/tests    # Pavitra — scene retrieval
.venv\Scripts\python -m pytest metocean_service/tests # Keerthana — metocean chain
.venv\Scripts\python -m pytest ais_service/tests      # Krishnan — AIS suite
.venv\Scripts\python -m pytest contracts/tests        # frozen-contract validation
```

Frontend e2e (Playwright): `cd main_system/frontend` then `npx playwright test`.

## Data Directory
All downloaded datasets, cache databases, and simulation logs are stored in `data/` which is ignored by git. Model weights (`*.onnx`, `*.pt`, `*.pth`) are also ignored — fetch them with `scripts/get_weights.ps1` / `.sh`, or re-export with `python -m ml.export`.
