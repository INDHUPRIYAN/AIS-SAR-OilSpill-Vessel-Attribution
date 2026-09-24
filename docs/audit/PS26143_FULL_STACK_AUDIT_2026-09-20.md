# OceanTrace: full frontend + backend audit, 2026-09-20

Scope: every routed page, every interactive control, every frontend API call,
the 127 backend routes, the database, the deployed ML weights, and all 172 run
directories on disk. Read-only: nothing was modified while producing this.

Method, so each claim can be re-checked:

- Route table dumped from the live FastAPI app (`app.openapi()`), not from a doc.
- Every `/api/...` string and every `api.*` method in `frontend/src` diffed against it.
- Import graph walked for files nothing imports.
- Regex sweep of `frontend/src` for coordinate / MMSI / scene-id / score literals and for mock/demo/TODO markers.
- Every `slick.geojson` on disk checked against its own `scene_meta.bbox` and a land mask.
- Every `status.json` tallied by stage status; SQLite registry cross-checked.
- Baselines: frontend unit tests 183/183 pass. Backend suite: see §9.

## 0. Headline

The frontend is honest. No fake button, no hardcoded analytical value and no
missing endpoint was found. The defects are on the backend:

| # | Severity | Finding |
|---|----------|---------|
| B1 | CRITICAL | **Fake fallback in the pipeline.** When a stage cannot run, `serve_mock()` copies a static file from `contracts/mocks/` into the run. A clean scene where the model finds **0 oil** therefore receives a fabricated slick at 80.315°E 13.052°N (off Chennai), a mock origin cloud, a mock forecast and mock suspects. It is labelled `mock`, but it is manufactured data in a real run, which §29 forbids. 28 registered runs carry it. |
| B2 | HIGH | **Tests write into the production data directory.** 48 `aoi-danish-straits-*` run folders in `data/runs/` were created by pytest (their detect error names a `pytest-of-…` temp path). 6 reached the registry. |
| B3 | HIGH | **RBAC hole: `PATCH /api/incidents/{id}`.** Guarded by authentication only. `auditor` and `zone_officer` (any zone) can retitle, re-assign and move a case to `investigating`. Separately the inline check `role not in ("reviewer","admin")` refuses `super_admin`, contradicting `IMPLICIT_ROLES`. |
| B4 | MEDIUM | **Position basis stops at the SAR Database page.** `geo_basis` (measured / assigned / synthetic) is computed in `api/sar_database.py` only. The workspace, incident page and printed report show the same assigned coordinates with no basis label. |
| F1 | LOW | Overview page carries a ±24 h playback transport whose only effect is hiding vessels and filtering incident pins; its future half can never contain data. |
| F2 | LOW | 3 dead component files; 2 mislabelled navigation links. |

Flagship `inv-gulf-flagship-20230108-2day`: 5/5 stages `ok`/`real`, engine `ml`,
top candidate and sub-scores read straight from `suspects.json`. Untouched by
every recommendation here.

## 1. Frontend → backend matrix (by surface)

Every row was verified against the live route table. "Store" is the table or
run artifact that holds the data.

| Surface | Action | Endpoint | Store / provider / model | Status | Decision |
|---|---|---|---|---|---|
| Sign in | submit, evaluator mode | `POST /auth/login`, `GET /auth/mode`, `/auth/me` | `users` | CONNECTED | KEEP |
| Header | palette search | `GET /search` | runs, incidents, investigations, vessels, scenes | CONNECTED | KEEP |
| Header | alert bell, provenance chips, digest copy | `/alerts/summary`, `/runs/{id}` | `alerts`, manifest | CONNECTED | KEEP |
| Status bar | providers, AIS, system, workers | `/apis/status`, `/ais/status`, `/system/health`, `/workers` | measured | CONNECTED | KEEP |
| Overview `/` | tiles, feed, layers, selection cards | `/ais/live`, `/incidents`, `/alerts`, `/runs`, `/zones/geojson`, `/layers/*` | db + run artifacts + AISStream | CONNECTED | KEEP |
| Overview `/` | time transport (play, speed, step, scrub) | none | local clock | FRONTEND ONLY | **REMOVE** (F1) |
| Overview `/` | tile "Investigations" → `/dashboard` | n/a | n/a | MISLABELLED | **FIX** → `/investigations` |
| Alerts | refresh, view filter, ack, dismiss+reason | `/alerts`, `/alerts/{id}/ack`, `/dismiss` | `alerts`, `audit_log` | CONNECTED | KEEP |
| Incidents | refresh, new, filter, status select, links | `/incidents` GET/POST/PATCH | `incidents` | CONNECTED (server guard weak: B3) | KEEP + FIX B3 |
| Incident Replay | incident picker, modes, layer toggles, globe/map, play/step/speed | `/replay/runs`, `/layers/*`, `/runs/{id}/forcing_field`, `/vessels_geojson` | run artifacts | CONNECTED | KEEP |
| My Desk | queues, links | `/zones/mine`, `/alerts`, `/incidents` | db | CONNECTED | KEEP |
| Global View | zone draw/reshape/undo/redo/save, search, layers | `/zones` CRUD, `/zones/lookup`, `/search` | `zones`, `zone_revisions` | CONNECTED | KEEP |
| Vessels | refresh, search, source filter, dossier | `/vessels`, `/vessels/{mmsi}` | `vessels`, `vessel_appearances` | CONNECTED | KEEP |
| Satellite | run select, mask toggle, export | `/runs`, `scene_png`, `mask_png`, `/runs/{id}/export` | run artifacts | CONNECTED | KEEP |
| SAR Database | local search, catalogue search, upload, metadata, Analyse, Find vessels | `/sar/scenes`, `/scenes/search` (CDSE), `/sar/upload`, `POST /investigations` + `/run` | scenes, uploads, ONNX models | CONNECTED | KEEP |
| Environment | run select, refresh | `/runs/{id}/forcing_field`, `/apis/status` | NetCDF forcing (CMEMS/HYCOM/ERA5/Open-Meteo) | CONNECTED | KEEP |
| Zones | filters, select, links | `/zones`, `/zones/{id}/revisions` | `zones` | CONNECTED | KEEP |
| Workspace (Analysis) | run / rerun / replay / cancel, stages, layers, decisions, auto-incident, compose report, export | `/investigations/*`, `/runs/*`, `/jobs/*`, `/events/runs/*`, `/tiles/*`, `/incidents/auto/*`, `/reports` | full pipeline | CONNECTED | **KEEP, untouched** |
| Investigations | list, filters, open | `/investigations`, `/runs`, `/reports`, `/users` | db | CONNECTED | KEEP |
| Run registry `/dashboard` | create+run, open, replay | `POST /investigations`, `/run`, `/runs` | db | CONNECTED | KEEP (see §6) |
| Reports / Report | compose, submit, publish, revise, CSV, JSON, print | `/reports/*` | `reports` (versioned) via `report_compose.py` | CONNECTED | KEEP |
| Analytics | read-only | `/metrics` | `ml.evaluate` output | CONNECTED | KEEP |
| API Monitor | test one, test all, call log | `/apis/*` | `api_providers`, `api_calls` | CONNECTED | KEEP |
| Data Sources | tabs | `/catalog` | catalog json | CONNECTED | KEEP |
| ML Models | refresh, tabs | `/models`, `/metrics`, `/models/hindcast` | model card, ONNX metadata, benchmark | CONNECTED | KEEP |
| System Ops | refresh, tabs, log filter, clear logs | `/workers`, `/system/health`, `/logs`, `/logs/clear` | process + ring buffer | CONNECTED | KEEP |
| Audit Trail | filters, paging, verify, export | `/audit`, `/audit/verify`, `/audit/export` | `audit_log` (hash chain) | CONNECTED | KEEP |
| Users & Roles | create, role, activate, zone assign | `/users`, `/zones/{id}/assignments` | `users`, `zone_assignments` | CONNECTED | KEEP |
| Credentials | set key, test | `PUT /keys`, `/keys/{p}/test` | `api_keys` (encrypted) | CONNECTED | KEEP |

Contract check: **0** frontend calls to a missing endpoint, **0** method mismatches.

## 2. Frontend button audit

**KEEP, connected.** Every control in §1 not listed below. No handler in the
codebase is empty, toast-only, or a bare local `status = "completed"`. "Run
analysis" posts to `/investigations/{id}/run` and follows the job over SSE;
"Compose report" posts to `/reports` and the server composes from run
artifacts; downloads are real `GET` exports; "Retry" is `POST /runs/{id}/rerun`.

**"Find vessels" (SAR Database).** Does navigate, but to the *same run*, whose
AIS filtering, ranking and attribution stages were started by "Analyse". It is
not a link to a vessel list. KEEP; the helper text already says what it does.

**FIX.** Overview tile "Investigations" opens the run registry, not the
investigations list. Header nav calls `/globe` "Operations" while the side nav
calls `/` "Overview" and `/globe` "Global View".

**CONNECT, backend exists with no UI.** Deliberately left as API-only, none is
needed by the workflow: AIS stream start/stop/flush, AOI create/edit/delete,
alert assign, run archive, zone delete, incident zone backfill, provider call
log per provider.

**BUILD BACKEND.** None. No essential control lacks a backend.

**REMOVE, frontend only.** Overview time transport (play, pause, speed, step,
scrub, reset) plus its "PAST" warning banner.

**REMOVE, unnecessary.** Dead files: `components/NewInvestigation.jsx`,
`components/workspace/StageStepper.jsx`, `components/workspace/TimeSlider.jsx`
(nothing imports them, tests included).

**DEFER.** UI for AOI watch management; UI for AIS stream control.

## 3. Hardcoded data found

PRODUCTION HARDCODE (frontend): **none.**

| File | Value | Class | Note |
|---|---|---|---|
| `pages/Investigation.jsx:173` | view `80.32, 13.05` | UI CONSTANT | Initial camera only, replaced on load. It happens to equal the mock slick centroid; once B1 is fixed, re-centre on the theatre default used by the globe. |
| `components/globe/GlobeScene.jsx:129` | view `88.0, 13.0` | UI CONSTANT | Bay of Bengal default camera. |
| `lib/replay.js:391` `makeParticles` | seeded positions | SAFE | Flow tracers. Seeds are arbitrary; every velocity is `sampleField()` over the run's real u/v grid. Not data. |
| `lib/replay.js:312` `guessPlace` | named regions | UI CONSTANT | Falls back to formatted coordinates. |
| `contracts/mocks/*` | slick, origin, forecast, suspects, vessels | **PRODUCTION HARDCODE (backend)** | Served into real runs by `serve_mock()`. This is B1. |

No hardcoded MMSI, score, area, confidence, wind, current, AIS count, scene id
or model name. Model identity on screen is `detect_response.model_version`.

## 4. Backend gaps

**CRITICAL: B1, remove the mock fallback.**
Feature: stage failure handling. Frontend: already renders `failed` and
"no region segmented". Required service change: `stage_mocked()` call sites in
`pipeline/run.py` (lines ~1195–1357) mark the stage `failed` or `skipped` with
the reason and write nothing; downstream stages that need the missing artifact
are `skipped`. "0 oil detected" becomes a first-class **clean scene** result,
not a failure. Database: none. Reason: §29, §13.

**HIGH: B2, test isolation.** `conftest.py` must point `settings.data_root` at
`tmp_path` for the scheduler/AOI tests. Then delete the 48 polluted folders and
their 6 registry rows (needs your confirmation: it deletes data).

**HIGH: B3, incident PATCH guard.** Route-level
`require_role("investigator","analyst","reviewer")`, zone scope for
`zone_officer`, and the concluding-status check rewritten on `IMPLICIT_ROLES`.

**MEDIUM: B4, position basis everywhere.** Move `classify()` out of
`api/sar_database.py` into a service, add `geo_basis` + note to
`GET /investigations/{id}` and to the composed report, render the existing
badge in the workspace case brief and the report header.

**LOW.** 4 run folders have no `detect_response.json` engine recorded
(pre-contract runs); 41 registry runs sit at `pending` forever (created, never
started). Neither is shown misleadingly; archive candidates.

## 5. Real-data traceability

| UI value | API | Service | Source | Flagship value |
|---|---|---|---|---|
| Model / engine | `/layers/{run}/detect` | `detection/service.py::run_detection` | `weights/segment.onnx` (U-Net R34) + `screen.onnx` (YOLO), ONNX Runtime, fingerprint-checked normalisation | `ml`, 31 oil + 361 look-alike |
| Mask overlay | `/runs/{run}/mask_png` | `api/replay.py` | `raw_mask.tif`, written with the scene's own rasterio profile | n/a |
| Slick area, axes, centroid | `/layers/{run}/slick` | Engine A characterise | polygonised `raw_mask.tif` in scene CRS → WGS84 | 62 slicks |
| Wind / current arrows and "at slick" speed | `/runs/{run}/forcing_field` | `api/replay.py` | run NetCDF forcing; provider per `provider_status.json` | currents+wind |
| Origin cloud | `/layers/{run}/origin_cloud` | Engine B `[euler]` hindcast | slick geometry + forcing, 24 h | real |
| Forecast | `/layers/{run}/forecast` | Engine B forecast | same state, 23 h | real |
| Candidates, sub-scores, reason | `/investigations/{id}/suspects` | Engine C | `vessels.parquet` (MarineCadastre) ∩ origin cloud; weights from `/attribution/weights` | 4 candidates, top 0.669 |
| Funnel counts | `/runs/{run}/funnel` | `run.py` | `suspects.filtered_out`, `total_vessels_considered` | real |
| Report body | `/reports/{id}` | `report_compose.py` | the artifacts above, frozen per version | real |

Scores are shown as scores with sub-score bars. No probability wording found.

## 6. Geolocation and land check

113 runs have a slick. For each, the first feature's centroid was tested
against the run's own `scene_meta.bbox` and `global_land_mask`:
**0 outside footprint, 0 on land, 0 property/geometry centroid mismatches.**
No lon/lat swap, no tile-offset error.

The two ways a slick can still look wrong on the map are B1 (a Chennai mock in
an unrelated run; 42 such runs have no bbox at all, so nothing constrains it)
and B4 (about 20 corpus scenes carry an *assigned* time and position, labelled
only on the SAR Database page).

## 7. Persistence

| Item | State |
|---|---|
| Investigations, runs, jobs, incidents, alerts, reports (versioned), decisions, zones + revisions, users, assignments, vessels + appearances, API keys, provider calls, audit log | PERSISTED (SQLite) |
| Scene meta, detection, mask, slick, origin, forecast, suspects, AIS ingest, manifest + digest | PERSISTED (run folder, hash-verified by `/runs/{id}/verify`) |
| Live AIS positions | PERSISTED table, currently 0 rows: AISStream delivers nothing over the Bay of Bengal, and the UI says so via `stream.note` |
| Logs | TEMPORARY ring buffer, stated as such on the page |

## 8. RBAC

All 20 routers are mounted with the `authenticated` dependency; writes use
`require_role` / `require_super_admin`; the public evaluator has a server-side
write blocklist. `test_rbac_matrix.py` (11) and `test_public_evaluator.py` (7)
cover it. The one hole is B3.

## 9. What was changed, and verification

| # | Change | Where |
|---|---|---|
| B1 | `serve_mock()` removed. `stage_unavailable()` records `failed` + reason, writes nothing, deletes a partial output. `data_source` for a stage that did not run is `none`, not `synthetic`. `mock` survives only as a legacy status on runs sealed before today. | `pipeline/run.py`, `tests/test_no_fake_fallback.py` |
| B2 | Already fixed in code: `test_scheduler.py` isolates `DATA_ROOT`, and a full suite run today created no folder under `data/runs`. The 48 leftover `aoi-danish-straits-*` folders and 6 registry rows are historical. **Not deleted: needs the owner's go-ahead.** | n/a |
| B3 | Incident PATCH: `auditor` refused; `zone_officer` only inside assigned zones; concluding statuses for `reviewer` + `IMPLICIT_ROLES` (so `super_admin` works). | `api/incidents.py`, `tests/test_incidents.py` |
| B4 | `basis_for_meta()`; both `scene_meta` layer endpoints add a response-only `basis` block (sealed files untouched). Case brief shows a POSITION ASSIGNED / SYNTHETIC badge and caveat; the printed report labels the analysis area and no longer prints "Sentinel-1 SAR (GRD)" for corpus, uploaded or synthetic scenes. Across run folders: 17 measured, 47 assigned, 51 synthetic. | `api/sar_database.py`, `api/routes.py`, `api/investigation_page.py`, `CaseBrief.jsx`, `IncidentReport.jsx` |
| F1 | Overview time transport, its warning banner, the `TimeTransport` component and its CSS removed. | `Operations.jsx`, `GlobeChrome.jsx`, `globe.css` |
| F2 | 3 dead files deleted; Overview "Investigations" tile opens `/investigations`; header nav uses the same names as the side nav. | `lib/shell.jsx` |

Verification after all changes:

- Backend: **1250 passed, 4 skipped**.
- Frontend unit: **183 passed**. Production build clean.
- Playwright (isolated DB harness): **15 / 15**, including W6, which ran a live
  pipeline on a clean scene: `1/5 stages real, 0 from mocks, 4 failed`, no
  `slick.geojson` written, no incident opened.
- Flagship `inv-gulf-flagship-20230108-2day`: `--verify` OK, digest
  `fd42e078f8366110`, 8 artefacts unchanged.

## 10. Still open

1. Purge the 48 pytest-created run folders + 6 registry rows (destructive; awaiting confirmation).
2. `api/routes.py` ~L372: an investigation whose scene meta has no `file_path` still runs on `contracts/mocks/scene_sigma0_db.tif`. It is labelled SYNTHETIC end to end, but by the same principle as B1 it should be refused with "scene has no raster".
3. 28 registered legacy runs carry `mock` stages (a Chennai slick in an unrelated run). They are badged; archiving them would remove them from default lists.
4. 41 runs sit at `pending` (created, never started).
5. Deferred UI: AOI watch management, AIS stream start/stop.
