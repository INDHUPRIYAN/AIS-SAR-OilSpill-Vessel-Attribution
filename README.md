# OceanTrace — SAR Oil-Spill Detection & AIS Vessel Attribution

Welcome to **OceanTrace** (SIH 2026 · PS 26143), the system for tracking oil spills via satellite imagery (SAR), fetching metocean data, interpolating AIS vessel coordinates, running drift trajectory simulation, and attributing the spill to culprit vessels.

## System Architecture & Owners
- **`contracts/`**: Shared interfaces, schemas, and mocks. Indhu writes, all read.
- **`main_system/`** (Indhu Priyan): Central FastAPI system, front-end dashboard (React/MapLibre), Model 2 — U-Net slick segmentation (Trujillo), pipeline orchestration and final integration.
- **Model 1 — DARTIS screening detector** (Mohan Kumar M): YOLO oil-vs-look-alike screening model — DARTIS dataset preparation, training and evaluation (`main_system/ml/dartis.py`, `train_yolo.py`).
- **`analysis_engines/`** (Nandha): Physical drift simulation (OpenDrift/Euler), characterization, and attribution scoring.
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

## Data Directory
All downloaded datasets, cache databases, and simulation logs are stored in `data/` which is ignored by git.
