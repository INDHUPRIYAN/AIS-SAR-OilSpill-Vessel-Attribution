# AIS-SAR Oil Spill Vessel Attribution (oceantrace)

Welcome to the **oceantrace** system for tracking oil spills via satellite imagery (SAR), fetching metocean data, interpolating AIS vessel coordinates, running drift trajectory simulation, and attributing the spill to culprit vessels.

## System Architecture & Owners
- **`contracts/`**: Shared interfaces, schemas, and mocks. Indhu writes, all read.
- **`1_indhu_main_system/`** (Indhu): Central FastAPI system, front-end dashboard (React/MapLibre), ML detection models (DARTIS/Trujillo).
- **`2_nandha_engines/`** (Nandha): Physical drift simulation (OpenDrift/Euler), characterization, and attribution scoring.
- **`3_pavitra_scene_service/`** (Pavitra): Sentinel-1 SAR scene retriever (CDSE & ASF APIs).
- **`4_keerthana_metocean_service/`** (Keerthana): Winds and currents data query (GLORYS, ERA5, HYCOM, OpenMeteo).
- **`5_krishnan_ais_service/`** (Krishnan): AIS trajectory downloader, interpolator, synthetic generator, and benchmark suite.

## Data Directory
All downloaded datasets, cache databases, and simulation logs are stored in `data/` which is ignored by git.
