# Hindcast (BAYES-TRACK) inside OceanTrace

The forward pipeline answers *what is this slick and who was near it*. The
hindcast answers the question it cannot: **where and when did the oil enter the
water**, as a posterior distribution with a credible region rather than one
back-trajectory. It lives in OceanTrace as its own sidebar section,
**Hindcast → Monitoring Engines** (`/hindcast`).

```
ingest → bounds → forcing → drift → shape → verify → posterior
         Stage 0  Stage 1  Stage 2  Stage 3  Stage 4   Stage 5
```

## Using it

Open **Hindcast** in the left rail. Two ways to start a job (investigator or
analyst; every role can watch):

* **Run hindcast**: pick a completed OceanTrace run. The job takes that run's
  scene time and its **largest segmented slick**, and resolves wind and
  currents from the same metocean cache the run's own drift stage used
  (`resolve_metocean`). Runs whose slick is a legacy `mock` file are refused.
* **Run demo job**: a SYNTHETIC spill. Oil is released at a known point and
  time, run forward, outlined, and handed to the engines, which must find the
  release again. The result panel states how close they got. The forcing given
  to the hindcast is deliberately biased (true wind ×1.08, veered 6°); Stage 1
  has to recover that from the SAR wind, and the Forcing tile shows it.

Each tile shows the engine, its stage, status (IDLE / RUNNING / SUCCEEDED /
FAILED / SKIPPED), progress, the live step, its headline metrics and the last
run. Click a tile for the full log, every metric, and run history.

## What an honest result looks like on real data

The search window can never reach further back than the forcing on disk.
For the flagship Gulf scene the cache starts 24 h before the pass, so heavy
oil's 336 h ceiling is **clipped to 23 h**, logged by the forcing engine and
shown as *Search window* on its tile. A scene with no currents grid runs as a
**WIND ONLY** hindcast and is badged so. Real runs carry no SAR-derived wind
yet, so Stage 1 reports "model wind used uncorrected" rather than a zero bias.

## Where things are

| | |
|---|---|
| Engines, physics, orchestration | `main_system/backend/services/hindcast/` |
| Tables (`hindcast_jobs`, `hindcast_engine_runs`, `hindcast_archive_scenes`, `hindcast_particle_snapshots`) | `main_system/backend/models/hindcast.py` |
| API + WebSocket | `main_system/backend/api/hindcast.py` |
| Job working folders | `<DATA_ROOT>/hindcast/<job_id>/` (L_drift GeoTIFF, particle parquet, forcing.npz) |
| Page, tiles, drawer, result | `frontend/src/pages/HindcastEngines.jsx`, `frontend/src/components/hindcast/`, `frontend/src/hindcast.css` |
| Tests | `main_system/tests/test_hindcast_science.py`, `test_hindcast_engines.py`, `test_hindcast_api.py` |

### API

| | |
|---|---|
| `POST /api/hindcast/jobs` | Start from an explicit request: `scene_meta`, `slick_polygon_geojson`, `oil_type?`, `config?`, `forcing?`, `archive?` |
| `POST /api/hindcast/from_run/{run_id}` | Start from an OceanTrace run |
| `POST /api/hindcast/demo` | Start the synthetic demo |
| `GET /api/hindcast/jobs`, `GET /api/hindcast/jobs/{id}` | Jobs; one job with full logs and the result (MAP, HDR contours, P(τ), modes) |
| `GET /api/hindcast/jobs/{id}/particles?tau=` | Backtracked particle cloud at one age, GeoJSON |
| `GET /api/engines`, `/api/engines/status?job_id=`, `/api/engines/{id}/runs` | Registry, live state, run history |
| `WS /ws/engines/{job_id}` | Snapshot on connect, then every engine update. Same session cookie as `/api`; refused (4401) without one |

The three `POST`s are in the RBAC matrix as investigator/analyst, and each one
writes an audit row (`hindcast.start`, `hindcast.from_run`, `hindcast.demo`).

## How it fits this system (and what it deliberately does not do)

* **One process, no broker.** Jobs run in a background thread and publish to
  the WebSocket through an in-process bus. The original brief asked for Celery,
  Redis and PostGIS; OceanTrace is SQLite and single-process, so those were not
  brought in. The drift engine keeps its prepare / run_members / finalize split,
  so member chunks can be fanned out to workers later without touching it.
  A job cannot survive a restart: anything left `running` is swept to `failed`
  at boot.
* **No NetCDF between engines.** HDF5 is not thread-safe in this process and
  has taken the server down before. Engines hand forcing to each other as
  `.npz`; the only NetCDF reads (provider files) happen while holding
  `routes._pipeline_gate`, the same gate the forward pipeline holds.
* **Geometry is GeoJSON text**; the archive lookup filters by time in SQL and
  intersects in shapely.
* **Forcing download is not implemented.** `services/hindcast/downloaders.py`
  documents the exact ERA5 / CMEMS requests and raises; a job without local or
  cached forcing fails at Stage 1 with that instruction.
* **OpenDrift is optional and its adapter is untested** (the package is not
  installed). The numpy RK4 integrator is what runs. `HINDCAST_DRIFT_BACKEND=
  auto|numpy|opendrift`.

## Decisions worth knowing about

* **Candidates for Stage 4 are stratified by age.** The global top-N cells of
  L_drift·L_shape·P_age go almost entirely to the youngest ages, whose clouds
  are compact and so have the tallest density peaks: a normalisation artefact.
* **MAP = most probable age, then most probable place at that age.** The raw
  argmax over (x, τ) cells leans young for the same reason; it is still
  reported as `joint_cell_map`.
* **L_fwd is raised to `fwd_exponent` (default 3).** It is a 0–1 score, not a
  likelihood.
* **Age and diffusivity are degenerate** (slick area grows like K·τ). The MAP
  age is the least certain number produced; the credible window is the honest one.
* **Stage 0 and Stage 3 are heuristics** with wide tolerances and named
  constants. They nudge; drift and forward verification decide.
* **Wording.** The result is an *estimated origin* with a credible region. It
  is not "the source", and it names no vessel.
