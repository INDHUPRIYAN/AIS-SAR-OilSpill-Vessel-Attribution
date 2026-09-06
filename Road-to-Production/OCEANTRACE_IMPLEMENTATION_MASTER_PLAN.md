# OCEANTRACE — IMPLEMENTATION MASTER PLAN

**Role:** Lead Product Architect / Senior Full-Stack Engineer
**Inputs digested:** capability audit (`audit_evidence/OCEANTRACE_CAPABILITY_AUDIT.md`, executed live 2026-09-06 against `AIS-SAR-OilSpill-Vessel-Attribution` @ `b2a8fd58`), Production UI Spec (P0–P18), OSINT UI/UX Spec, UI reference images (visual inspiration only), and the architect brief with its 17 Truth Rules.
**Companion file:** `OCEANTRACE_CLAUDE_CODE_PROMPTS.md` — the phased execution prompt pack (Deliverable 13).

**Grounding statement.** Every claim in this plan traces to the audit. The repo is NOT greenfield: FastAPI backend (`main_system/backend`, 34 routes), React 18 + Vite + MapLibre/deck.gl frontend (8 routes), SQLite (`data/oceantrace.db`, 8 tables), engines A/B/C (`analysis_engines/`), adapters (`scene_service/`, `metocean_service/`, `ais_service/`), frozen contracts (`contracts/`), 654 passing tests. A real S1 scene runs 5/5 stages in 24.03 s producing 8 sha256-sealed artefacts. We are adding around this core, not rebuilding it.

---

## 1 · CURRENT-STATE MATRIX

### A — Already implemented (reuse as-is; do not rebuild)

| Capability | Where | Proof |
|---|---|---|
| Pipeline orchestration, 5 stages, per-stage provenance, idempotency keys | `backend/services/pipeline/run.py` | 5/5 real in 24.03 s (C3) |
| YOLO11n screen + U-Net/ResNet-34 seg (ONNX RT 1.23.2, CUDA), look-alike rejection | `backend/services/detection/service.py`, `weights/` | live inference, conf 0.9332 |
| Characterisation: area/perimeter/centroid/axes/orientation/damping/age(Fay) | `analysis_engines/engines/characterise/` | live values (C18) |
| Physics hindcast/forecast, backward time verified, forecast p50/p90 footprints | `engines/drift/`, `backends.py` (euler) | timestamps strictly decrease; 6/12/24 h horizons |
| Origin window interval (`cloud_convergence`), origin summary | `run.py:451-530`, native artefact | 4 h window on live run |
| AIS ingest (MarineCadastre+DMA), clean, interpolate-with-gap-flags, partitioned index | `ais_service/ais/` | real 319 MB archive → 185,132 rows, all `source: real` |
| Three-gate filtering + measured `filter_reason` ledger + funnel | `attribution/gates.py`, `explain.py` | 43→27→10(+17) live |
| Six-factor weighted attribution (.30/.20/.20/.15/.10/.05, sum 1.0), generated evidence strings, neutral language | `attribution/scoring.py`, `attribution_weights.yaml` | Σwᵢsᵢ verified, max Δ 0.0003 |
| Benchmark, rerunnable | `analysis_engines/benchmark/` | re-run: 86 % top-1 / 100 % top-3, identical ranks |
| Artefact hashing, immutability, `/verify`, export zip, replay of 93 runs | `services/pipeline/provenance.py`, `routes.py` | `/verify` ok, 8 checked |
| Provider registry w/ live probes, circuit breaker, call history, fallback chain | `core/config.py PROVIDERS`, `providers/health.py` | 10 providers, 7,559 call rows |
| Credential vault (Fernet, masked, DB>env) | `core/security.py`, keys routes | verified live |
| Investigation entity + create/run/status APIs, deep links `?run=` | `models/db.py`, `routes.py` | 135 investigations |
| Honest degradation vocabulary (ok/mock/fallback/failed; WORKING/DEGRADED/UNCONFIGURED) | `run.py`, `health.py` | mock-scene run degraded honestly |
| UTC discipline, loading/empty/error states, `statusTone()` | `frontend/src/lib/api.js`, `ui.jsx` | audit G-09/G-20 |

### B — Partially implemented (extend, don't replace)

| Item | Gap | Audit ID |
|---|---|---|
| Runs history | no search/filter/sort/pagination; counts 89 DB / 93 replay / 96 disk; 6 CLI runs invisible | RH-01..05 |
| Report page | missing exec summary, environment, forecast, methodology, uncertainty/limitations, hash+provenance annex, decisions rendering, versioning, CSV | RP-04..13 |
| Age honesty | categorical `low`→0.25 flattening; `age_method` dropped by `normalise.py`; no interval | C-05..07 |
| Hindcast uncertainty | published ellipses all zero-radius; `origin_uncertainty_km` (0.9857 @ 0.908) dropped | H-06 |
| Forcing provenance | provider block replaced by filenames in contract | H-11 |
| Scene visualisation | single decimated PNG (1024²), no tiles | G-13/D-13 |
| Provider semantics | "WORKING" = landing-page reachable; `has_credentials` true for keyless; no coverage metadata | PC-03/05/10 |
| Audit log | 2/10 event types; free-text unauthenticated `actor`; no IP; no search/export; no tamper-evidence | AL-02..08 |
| Decisions | persisted with digest+weights but not rendered; no review state machine | RP-07 |
| Model provenance | version string in artefacts; no model sha256 or git SHA in manifest | M-03/13, X-02 |
| Weights config | YAML-only; no sum=1.0 validation/renormalisation; no read API | S-04/05 |
| Rerun | new run works; no rerun-with-same-inputs affordance | R-10 |
| Real-AIS run | `gulf-real-ais` exists but bypassed orchestrator (no manifest, synthetic slick) | A-06 |

### C — Missing (build new)

Auth/session/JWT/RBAC (G-01..04, AD-01..03/06..08) · Incident entity+CRUD (I-01..04) · Cross-run vessel index + identity fields imo/flag/call_sign (V-01..09, A-04) · AOI CRUD/draw/upload + geofence (G-14/18/19, N-03..07) · Scene search+download HTTP routes (N-08/09→route) · Alerts entity/feed/notifications (P2, G-08) · SSE run progress (N-15→stream) · Job cancel/retry (N-16) · Run archive (RH-08) · Global search + command palette (G-06/07) · 3D globe mode (G-11) · Measure tools (G-17) · Host+model health (SH-04/07) · Model registry (M-01) · CSV export (C-11/RP-11) · Report versioning (RP-12) · Tile server (G-13) · Temporal scene comparison (C-10, defer).

### D — Broken (fix precisely)

1. **M-05** `/api/metrics` serves epoch-29 metrics (`analytics.py:202` → `data/runs/training/metrics.json`); deployed is `unet-r34-fullcorpus-e48` (oil-tile IoU 0.5723, not 0.8781).
2. **N-14** `Dashboard.jsx:29-33` hardcodes `contracts/mocks/scene_meta.json` → 1/5-real runs from the UI.
3. **P2 scheduler** `watcher.py:139` `ModuleNotFoundError: No module named 'satellite'` — backend `sys.path` lacks `scene_service`; tests pass only via `pytest.ini`.
4. **H-06** `normalise.py:168-169` defaults absent ellipse axes to 0.0.
5. **X-05** `_lite_origin` O(n²) → `?lite=true` 3.407 s vs 0.070 s full.
6. **PC-11** mock `contracts/mocks/provider_status.json` copied into every run dir.
7. **PC-05** `has_credentials` unconditionally true for keyless providers (`health.py:80-81`).

### E — Experimental / un-deployed (label, never ship as production)

- **Drift ML residual** `drift-residual-mlp-20260906`: trained, evaluated **negative** (held-out 0/6 better), `use_ml_residual=False` by design → list as `EXPERIMENTAL · evaluated negative · disabled`. `ml_origin.py` unreachable.
- **Sentinel-2/EO** `s2_adapter.py` + `optical_detect.py`: real, 11 tests, one manual 809 MB acquisition, wired into nothing → `EO: NOT DEPLOYED`.
- **OpenDrift/OpenOil**: not installed, 3 skips.
- **AISStream**: credentials field with no adapter → remove field or build adapter; never show live-AIS online.
- **Postgres/MinIO/Redis**: compose `extras` only, unused → never display in health.

### F — Needs validation (one execution each, capture evidence)

`.SAFE` calibration end-to-end (D-02) · threshold_fallback engine (D-08) · HYCOM pull (O-02) · live CMEMS pull (O-01) · benchmark sensitivity re-run (M-10) · ONNX↔PyTorch parity re-run (M-11) · full-pipeline real-AIS Gulf run (A-06 — Stage 4 flagship).

---

## 2 · UI → BACKEND DEPENDENCY MAP

Legend: ✅ exists (reuse) · 🔧 extend · 🆕 build.

| Page | Requires | State |
|---|---|---|
| **P0 Sign In** | `users` table 🆕 · `POST /api/auth/login|logout|me` 🆕 · session cookie/JWT 🆕 · router-level guard 🆕 |
| **P1 Global Monitor** | incidents list 🆕(I) · `GET /api/apis/status` ✅ · `GET /api/metrics` 🔧(fix M-05) · alerts feed 🆕 · runs summary ✅ · 2D map ✅ · 3D globe mode 🆕 · SSE events 🆕 |
| **P2 Alerts** | scheduler import fix 🔧 · `alerts` entity 🆕 · `POST /api/aois/poll` ✅(after fix) · notification channel (SSE) 🆕 · assign→user 🆕(needs P0) |
| **P3 New Investigation** | `InvestigationCreate`+AOI+window 🔧 · AOI CRUD 🆕 · draw/upload FE 🆕 · `GET /api/scenes/search` 🆕(wraps `fetch_scene` ✅) · scene download job 🆕 · run launch ✅ · progress SSE 🆕(status.json ✅) · demo scene replace mock 🔧 |
| **P4 Incidents** | `incidents` table+CRUD+lifecycle 🆕 · `incident_id` FK on Investigation/Run 🆕 · search/filter/paginate 🆕 · status log→audit 🆕 |
| **P5 Run Overview** | run detail ✅ · manifest ✅ · verify ✅ · export ✅ · stage status ✅ · SSE 🆕 · rerun-same-inputs 🔧 · incident link 🆕 · manifest+git SHA+model hash 🔧 |
| **P6 Detection** | layers ✅ · scene_png ✅ · **tile server 🆕** · detect params surface 🔧 · EO tab = NOT DEPLOYED label 🔧 |
| **P7 Characterization** | slick layer ✅ · age_method+categorical conf through normalise 🔧 · age interval (optional, from thickness sensitivity) 🔧 · CSV 🆕 |
| **P8 Drift** | origin_cloud ✅ · **real ellipse axes 🔧** · origin_uncertainty into contract 🔧 · forcing provider block into contract 🔧 · `_lite_origin` fix 🔧 · forecast ✅ |
| **P9 AIS** | vessels_geojson ✅ · funnel (structured object) 🔧 · filtered ledger ✅ · SYNTHETIC badge (from `suspects.source`) ✅ data/🔧 UI · viewport decimation 🔧 · real-AIS flagship run (data op) 🔧 |
| **P10 Attribution** | suspects ✅ · weights read API 🆕 · weight sum validation 🔧 · comparison view FE 🆕 · decisions render 🔧 |
| **P11 Vessel Dossier** | `vessels`+`vessel_appearances` 🆕 · post-run indexer 🆕 · `GET /api/vessels/{mmsi}` 🆕 · identity fields in AIS contract 🔧 · cross-run tracks route 🆕 |
| **P12 Report** | report compose service 🆕 · provenance annex (manifest ✅→render 🔧) · methodology/limitations (from `docs/LIMITATIONS.md` ✅) · review state machine 🆕(needs P0) · versioned `reports` table 🆕 · CSV 🆕 · PDF=print ✅ |
| **P13 Runs History** | query params+indexes 🔧 · offset pagination 🔧 · backfill 6 orphans (`backfill_runs.py` ✅ run it) · CLI writes DB row 🔧 · top-suspect/area denorm 🔧 · archive flag 🆕 |
| **P14 Data Catalog** | providers ✅ · REACHABLE wording 🔧 · tri-state credentials 🔧 · coverage metadata 🆕 · EO/live-AIS = NOT DEPLOYED rows 🔧 · stop mock provider_status copy 🔧 |
| **P15 Models** | metrics fix 🔧(M-05) · `models` registry table 🆕 · checkpoint hashes ✅(computed)→persist 🆕 · drift-ML = EXPERIMENTAL card 🔧 · benchmark surface ✅ |
| **P16 Admin** | users/roles CRUD 🆕 · keys vault ✅ · config read route 🆕 · weight-profile editor 🆕(optional) |
| **P17 Health** | `/health|readyz` ✅ · provider status ✅ · host metrics (psutil) 🆕 · model-load health 🆕 · **no** storage/queue tiles (not deployed) |
| **P18 Audit** | table ✅ · 10 event types 🆕 hooks · authenticated actor 🔧(needs P0) · IP/session 🔧 · search/filter/export 🆕 · hash chain 🆕 · own route `GET /api/audit` 🆕 |

---

## 3 · BACKEND / DOMAIN ARCHITECTURE

**Principle: additive, reuse-first.** The pipeline, engines, contracts, provenance and provider layers are proven — we wrap and extend. New code lands in new modules; existing modules change only at the audit-named lines.

```
main_system/backend/
├── main.py                    # + sys.path fix (scene_service), + router auth deps, + SSE mount
├── core/
│   ├── config.py              # + JWT settings, + Sentinel-2/AISStream registry honesty
│   ├── security.py            # ✅ Fernet vault (keep) + NEW password hashing (argon2/bcrypt)
│   └── authz.py               # 🆕 roles, get_current_user, require_role deps
├── models/db.py               # + User, Incident, Vessel, VesselAppearance, Alert, Aoi,
│                              #   Report, ModelRecord, JobRecord; + AuditLog cols (ip, prev_hash, row_hash)
├── api/
│   ├── auth.py                # 🆕 login/logout/me/users CRUD
│   ├── incidents.py           # 🆕 CRUD + lifecycle + search
│   ├── vessels.py             # 🆕 dossier + cross-run tracks
│   ├── scenes.py              # 🆕 search (wraps scene_service chain) + download job + local catalog
│   ├── aois.py                # 🔧 scheduler_routes → full CRUD + geofence eval
│   ├── alerts.py              # 🆕 feed, ack/assign/dismiss
│   ├── audit.py               # 🆕 /api/audit search/export (replaces nesting under /keys)
│   ├── system.py              # 🆕 host+model health
│   ├── events.py              # 🆕 SSE: run progress, alerts
│   ├── tiles.py               # 🆕 /api/tiles/{run}/{z}/{x}/{y}.png (rio-tiler over calibrated GeoTIFF)
│   ├── reports.py             # 🆕 compose/version/review + CSV
│   └── routes.py              # 🔧 runs query params, rerun, weights read, funnel object
├── services/
│   ├── pipeline/              # ✅ run.py/provenance.py (+ manifest v2 fields, ensure_vessels real-first)
│   │   └── normalise.py       # 🔧 carry ellipse axes, origin_uncertainty, forcing block, age_method, categorical conf
│   ├── jobs.py                # 🆕 JobManager: id, thread, cancel Event, progress, persistence
│   ├── vessel_index.py        # 🆕 post-run indexer (suspects.json+vessels.parquet → Vessel tables)
│   ├── audit.py               # 🆕 record(actor, action, resource, detail, ip) + hash chain
│   └── report_compose.py      # 🆕 report.json builder incl. provenance annex
```

**Reuse contracts (binding):** artefact names stay (`manifest.json` aliased as `run_manifest.json` in export only, R-12); layer routes stay; engines A/B/C interfaces stay; `attribution_weights.yaml` stays the weight source (plus loader validation); `status.json` stays the progress source (SSE tails it); pipeline stage names stay exactly `detect → characterise → drift_hindcast → drift_forecast → attribution` (UI narrates the 11-step semantic chain over these 5 executable stages — do not rename either).

**Jobs:** keep in-process threads (POC-honest) but wrap in `JobManager` giving every run a `job_id`, a `threading.Event` cancel handle checked between stages (`run.py` already flushes status per stage — add a cancel check at each flush point), progress %, and a `jobs` DB row. Celery/Redis is explicitly out of scope pre-SIH (SH-05: don't deploy what you won't use).

**SSE over WebSocket:** one-directional progress/alert streams → `sse-starlette`. Frontend `EventSource` with poll fallback (the existing polling keeps working — SSE is an upgrade, not a replacement).

---

## 4 · DATABASE / ENTITY MODEL (SQLite, additive migration)

Existing (keep): `investigations, runs, decisions, aoi_watch, api_providers, api_calls, api_keys, audit_log`.

New/changed tables (SQLAlchemy in `models/db.py`; one idempotent migration script `backend/migrations/m001_production.py` — no Alembic dependency needed at this scale):

```
users              id PK · email UNIQUE · password_hash NULL(if OIDC) · display_name
                   role ENUM(admin,investigator,analyst,reviewer,auditor) · active BOOL
                   created_utc · last_login_utc
incidents          id PK ("INC-<yyyy>-<seq>") · title · geometry_json (Point/Polygon)
                   detected_utc · status ENUM(open,investigating,attributed,closed,archived)
                   assignee_id FK users NULL · region TEXT · notes · created_utc · updated_utc
investigations     + incident_id FK incidents NULL · + created_by FK users NULL
                   + aoi_geojson TEXT NULL · + window_start_utc NULL · + window_end_utc NULL
runs               + incident_id FK NULL (stamped from investigation at creation)
                   + top_suspect_mmsi NULL · + top_score REAL NULL · + slick_area_km2 REAL NULL
                   + archived BOOL DEFAULT 0        (RH-05/08 denorm at seal time)
vessels            mmsi PK · vessel_type · length_m · width_m · draught_m
                   name NULL · imo NULL · flag NULL · call_sign NULL · source ENUM(real,synthetic)
                   first_seen_utc · last_seen_utc
vessel_appearances id PK · mmsi FK · run_id FK · incident_id NULL · rank NULL · total_score NULL
                   filtered BOOL · filter_reason NULL · ais_gap_minutes NULL · source
aois               id PK · name · geojson · created_by FK · created_utc · archived BOOL
alerts             id PK · kind ENUM(new_scene,detection,system) · aoi_id NULL · scene_id NULL
                   run_id NULL · confidence NULL · status ENUM(new,assigned,investigating,dismissed)
                   assignee_id NULL · dismiss_reason NULL · created_utc · updated_utc
reports            id PK · run_id FK · version INT · artefact_digest · body_json · status
                   ENUM(draft,in_review,published) · author_id FK · reviewer_id NULL
                   reviewed_utc NULL · created_utc          (immutable once published)
models             id PK · name · version · kind ENUM(screen,segment,drift_residual)
                   file · sha256 · bytes · dataset_note · metrics_json · status
                   ENUM(production,experimental,superseded) · deployed_utc
jobs               id PK · kind ENUM(pipeline,scene_download,ingest,benchmark) · run_id NULL
                   status ENUM(queued,running,done,failed,cancelled) · progress REAL
                   created_utc · finished_utc · error TEXT NULL
audit_log          + actor_user_id FK NULL · + ip TEXT NULL · + resource TEXT
                   + prev_hash TEXT · + row_hash TEXT      (chain: row_hash = sha256(prev_hash‖canonical_row))
```

Relationships: `incidents 1—N investigations 1—N runs`; `runs N—N vessels` via `vessel_appearances`; `reports N—1 runs` (versioned); `alerts` optionally link aoi/scene/run. Seed models table from `weights/model_card.md` + computed sha256s (audit already lists them: screen `9a6ff8df…`, segment `a4aac81f…`, drift `drift-residual-mlp-20260906` EXPERIMENTAL).

Reconciliation: run `backend/backfill_runs.py` (exists) over the 6 orphan dirs; patch the pipeline CLI (`run.py:1035-1071`) to upsert a `runs` row so DB(89)=replay(93)=disk(96, minus non-run dirs) converge.

---

## 5 · API CONTRACT PLAN (new/changed; all JSON; all guarded per §8)

```
AUTH        POST /api/auth/login        {email,password} → sets HttpOnly cookie; body {user}
            POST /api/auth/logout       → 204
            GET  /api/auth/me           → {id,email,role,display_name}
            GET/POST/PATCH /api/users   (admin)  create/deactivate/role-change

INCIDENTS   POST /api/incidents         {title, geometry, detected_utc, region?, assignee_id?}
            GET  /api/incidents         ?status=&region=&assignee=&q=&from=&to=&sort=&offset=&limit=
                                        → {total, items[]}
            GET  /api/incidents/{id}    → incident + linked investigations/runs summary
            PATCH /api/incidents/{id}   status transitions (reviewer+ for attributed/closed) → audited

SCENES      GET  /api/scenes/search     ?bbox=&start=&end=&source=S1 → wraps SceneRetrievalChain
                                        [{product_id, acquired_utc, footprint, source, size}]
            POST /api/scenes/download   {product_id} → {job_id}   (JobManager)
            GET  /api/scenes/local      → cached/demo scenes catalog (replaces mock hardcode)

INVESTIGATIONS (extend)
            POST /api/investigations    + {incident_id?, aoi:GeoJSON?, window_start_utc?,
                                           window_end_utc?, scene_product_id? | scene_meta_path}
            POST /api/investigations/{id}/run   (unchanged) + {job_id} in response
            POST /api/runs/{id}/rerun   → new run, same inputs (reads prior manifest)
            POST /api/jobs/{id}/cancel  → cooperative cancel between stages

RUNS        GET /api/runs               + ?q=&status=&incident=&region=&from=&to=&sort=&offset=
                                        → {total, items[+top_suspect_mmsi,+slick_area_km2]}
AOIS        GET/POST/PATCH/DELETE /api/aois        (authoring; watcher consumes same table)
ALERTS      GET /api/alerts ?status=…  · POST /api/alerts/{id}/ack|assign|dismiss{reason}
EVENTS      GET /api/events/runs/{run_id}   (SSE: stage transitions from status.json)
            GET /api/events/alerts          (SSE)
VESSELS     GET /api/vessels ?q=&source=    · GET /api/vessels/{mmsi}
            GET /api/vessels/{mmsi}/tracks  → per-run GeoJSON refs
WEIGHTS     GET /api/attribution/weights    → values + source file + validated:true
FUNNEL      GET /api/runs/{id}/funnel       → {found, indexed, ranked, filtered, reasons_histogram}
REPORTS     POST /api/runs/{id}/reports          → draft (composed server-side)
            GET  /api/reports/{id} · POST /api/reports/{id}/submit|approve (reviewer) 
            GET  /api/reports/{id}/export.csv|.json     (annex always embedded)
TILES       GET /api/tiles/{run}/{z}/{x}/{y}.png
SYSTEM      GET /api/system/health      → {host:{cpu,mem,disk}, models:[{name,loaded,provider}], db, providers_summary}
AUDIT       GET /api/audit ?actor=&action=&from=&to=&offset=  · GET /api/audit/export
METRICS     GET /api/metrics            🔧 deployed checkpoint identity+sha256+its holdout numbers;
                                        drift ML block labelled EXPERIMENTAL
```

Error model: keep existing honest 4xx/422 pydantic pattern; every mutating route emits an audit event; every list route returns `{total, items}` with `offset/limit`.

---

## 6 · EVENT / JOB ARCHITECTURE

- **JobManager** (`services/jobs.py`): registry of live jobs; pipeline thread checks `cancel_event.is_set()` at each stage boundary (the 5 `flush_status` points); cancelled runs seal with `status: cancelled`, never half-written artefacts (provenance `assert_writable` already guards).
- **SSE**: `events.py` tails `status.json` mtime per run (2 Hz) and pushes stage deltas; alerts stream pushes `alerts` inserts. Frontend keeps polling as fallback.
- **Scheduler**: after the `sys.path` fix, `SCHEDULER_ENABLED=true` in `.env`; each poll writes `alerts(kind=new_scene)` rows; `aoi_watch.polls` increments (currently 0).
- **Audit events (10 types)**: `auth.login/logout`, `user.change`, `incident.create/status`, `investigation.create`, `run.start/complete/cancel`, `report.submit/approve/export`, `decision.*` (exists), `key.set` (exists), `model.change`, `data.export`. One helper `services/audit.py.record()` used everywhere; hash-chained.

---

## 7 · DATA / PROVIDER INTEGRATION PLAN

1. **Flagship real run (highest credibility per audit rec §3g/§5.3):** pick a Gulf-of-Mexico Sentinel-1 IW GRDH scene overlapping 2023-01-01, acquire via `fetch_scene`, calibrate (`satellite.cli` — also closes D-02), run `run_pipeline` with `data/ais/real/mc_gulf_2023_01_01.parquet` reachable so `ensure_vessels` uses it (extend `ensure_vessels`: **real-parquet-first** — if a real store covers the origin window, use it; synth only as labelled fallback). Target manifest: attribution `data_source: "sensor"|"real"`, `suspects.source: "real"`. This converts P9/P10 from demo to evidence.
2. **Scene search/download over HTTP** wrapping the proven CLI chain (CDSE search returned 2 live products at 14:08Z).
3. **Provider honesty:** status word `REACHABLE` (not WORKING) for landing-page probes; add authenticated functional probes where cheap (CMEMS STAC HEAD, MarineCadastre file HEAD); tri-state credentials (`configured|n/a|missing`); registry rows for Sentinel-2 and Live-AIS fixed to `NOT DEPLOYED`; add per-provider coverage metadata (dataset id, bbox, temporal extent, resolution).
4. **EO posture:** do not wire S2 into the pipeline pre-SIH (no labelled optical data ⇒ no honest accuracy claim). Ship the honest story: adapter exists+tested, one real acquisition (hash on record), `NOT DEPLOYED` badge, roadmap note. Revisit post-SIH.
5. **Validation executions (F-list):** one each of `.SAFE` calibration, threshold_fallback run, HYCOM pull, live CMEMS pull; store under `docs/qa/evidence/` with hashes.
6. **Kill latent hazards:** stop copying mock `provider_status.json` into run dirs (write the run's real provider snapshot instead); remove AISStream from Keys page.

---

## 8 · SECURITY / RBAC MODEL

- **Identity:** `users` + argon2id hashes (or OIDC later; schema holds `password_hash NULL`). Login issues JWT (HS256, 8 h) in **HttpOnly SameSite=Lax cookie** — removes the `localStorage` admin-token hazard (AD-06). `X-Admin-Token` retired after migration; keys routes move under `require_role(admin)`.
- **Enforcement:** FastAPI dependency at **router include** level (`main.py:118-122`), not per-route decoration — the audit showed per-route guards get missed. Public: `/health*`, `/`, `/api/auth/login` only.
- **Role matrix (route × role):**

| Capability | investigator | analyst | reviewer | auditor | admin |
|---|---|---|---|---|---|
| read runs/layers/incidents | ✅ | ✅ | ✅ | ✅ | ✅ |
| create investigation / run pipeline | ✅ | ✅ | — | — | ✅ |
| decisions / draft reports | ✅ | ✅ | — | — | ✅ |
| approve reports, incident→attributed/closed | — | — | ✅ | — | ✅ |
| edit weights profile / rerun benchmark | — | ✅ | — | — | ✅ |
| users, keys, config | — | — | — | — | ✅ |
| audit read/export | — | — | ✅ | ✅ | ✅ |

- **Audit binding:** `record()` takes actor from the session, never the request body; `Decision.actor` becomes derived. IP captured from request. Rows hash-chained; `GET /api/audit/verify` recomputes the chain (mirrors artefact `/verify` pattern the repo already loves).

---

## 9 · PROVENANCE MODEL (manifest v2 + labels)

Manifest additions (backward-compatible keys): `code_git_sha`, `models:[{kind,name,version,sha256}]`, `forcing:{currents:{provider,dataset,valid,fallback},wind:{…}}`, `ais:{source: real|synthetic, dataset?, planted_culprit?:bool}`, structured `funnel`. Normalisation fixes (single file, `normalise.py`): ellipse axes from particle covariance per step; `origin_uncertainty_km`+coverage+method into metadata; full forcing block; `age_method`; `age_confidence_label:"low"` alongside numeric 0.25.

**UI label vocabulary (binding, from Truth Rules + audit contract):** `REAL` · `SYNTHETIC` (permanent badge on all AIS-derived surfaces of synthetic runs, incl. every suspect row and report) · `CACHED` · `FALLBACK` · `REACHABLE` · `NOT DEPLOYED` (EO, live AIS) · `EXPERIMENTAL · disabled` (drift ML) · `PHYSICS` (hindcast engine badge) · `LOW` (age confidence) · `Rank #N · Score X.XX` (never guilt language — protect S-12).

---

## 10 · UI INTEGRATION ARCHITECTURE

- **Shell:** keep the 8-route SPA; add router groups per UX spec — auth wrapper → app shell (top bar with mode switcher/ZULU/provenance chips/palette) → persistent map canvas → routed rails/drawers. P5–P12 = one `InvestigationWorkspace` with sub-view state (`/runs/:id/(overview|detection|characterization|drift|ais|suspects|report)`), map mounted once.
- **Modes:** `2D` (existing MapLibre/deck.gl, default) · `3D` (deck.gl `_GlobeView` sharing the same layer definitions — chosen over globe.gl to reuse the existing deck.gl layer code; 2D remains fallback per brief) · `MAP` (2D + mapbox-gl-draw-compatible draw control for AOI/measure) · `SAT` (tile layer from `/api/tiles`). Mode = `?m=` param; camera/`t`/selection preserved (UX spec §2).
- **Data layer:** extend `lib/api.js` `useApi` with cookie auth, SSE hook (`useRunEvents`), and typed fetchers per new route. Timeline `t` remains client state, encoded in deep links (`?t=`).
- **Truth chrome:** provenance chips read manifest v2; SYNTHETIC badge component consumes `suspects.source`; hindcast panel shows `PHYSICS` + `origin_uncertainty_km`; Analytics reads fixed `/api/metrics` and renders checkpoint identity; ellipses render only when axes > 0.
- **Bundle:** code-split maplibre/deck (audit: 1.66 MB + 802 kB) via route-level lazy imports.

---

## 11 · IMPLEMENTATION DEPENDENCY GRAPH

```
ENTITY/PLATFORM SPINE                        DATA/PIPELINE SPINE
Truth fixes (Stage 0)                        Truth fixes (normalise/metrics/mock-scene)
      ↓                                            ↓
Auth + Users + RBAC ──────────┐              Scene search API ← fetch_scene (✅)
      ↓                       │                    ↓
Audit v2 (actors, chain)      │              Scene download job (JobManager)
      ↓                       │                    ↓
Incidents ← assignees         │              Investigation v2 (AOI+window+scene)
      ↓                       │                    ↓
Alerts ← scheduler fix        │              Pipeline run (✅) + SSE + cancel
      ↓                       │                    ↓
Runs query/reconcile          │              ensure_vessels real-first → FLAGSHIP REAL RUN
      ↓                       │                    ↓
Vessel index ← run completion hook           Funnel/weights/ellipse/forcing surfaces
      ↓                       │                    ↓
Reports v2 (review workflow ←─┘ roles)       Tile server · P8/P9/P10 UI depth
      ↓                                            ↓
Admin / Health / Model registry              Workspace UI integration → E2E validation
```

---

## 12 · PRIORITIZED BUILD ROADMAP

| Stage | Content | Unblocks | Effort* |
|---|---|---|---|
| **0 Truth corrections** | M-05 metrics · N-14 demo scene · scheduler sys.path · H-06 ellipses · normalise carries (forcing/age) · PC-11 mock copy · X-05 lite fix · manifest git SHA+model hash · weights validation | P3/5/6/7/8/15 honesty | 2–3 d |
| **1 Identity** | users/auth/JWT/RBAC guards · audit v2 (actors, 10 events, chain, /api/audit) | P0, P16, P18, review flow | 4–6 d |
| **2 Entities** | incidents CRUD+lifecycle · runs query/pagination/reconcile/denorm · vessel index+identity fields | P4, P13, P11 | 5–7 d |
| **3 Acquisition & tasking** | scenes search/download routes+jobs · investigation v2 (AOI/window) · AOI CRUD+draw+upload · scheduler on + alerts entity + SSE | P3, P2, P1 | 6–8 d |
| **4 Data honesty & depth** | **flagship real-AIS Gulf run** · real-first ensure_vessels · funnel/weights routes · F-list validations · provider honesty (REACHABLE/tri-state/coverage/NOT DEPLOYED rows) | P9/P10 REAL, P14 | 4–6 d |
| **5 Reporting & governance** | report compose/versioning/review/CSV/annex · model registry · decisions render | P12, P15 | 4–6 d |
| **6 Platform & polish** | tile server · SSE everywhere · host/model health · 3D globe mode · MAP draw/measure · palette+search · bundle split | P6 zoom, P1/17, UX | 6–9 d |
| **7 Validation** | full E2E acceptance run (§14) + UAT spec re-execution + demo dry-run | SIH | 2–3 d |

*Effort assumes one experienced dev + Claude Code; stages 1–3 partly parallelizable across backend/frontend.

---

## 14 · END-TO-END ACCEPTANCE TEST PLAN

Execute after Stage 6; every test captures evidence under `acceptance_evidence/<ID>/`. Reuse the existing UAT spec (OT-*) plus these deltas:

| ID | Test | Pass condition |
|---|---|---|
| E2E-01 | Anonymous access | every `/api/*` (except auth/health) → 401; route×role matrix captured |
| E2E-02 | Investigator journey | login → create incident → draw AOI + window → scene search returns live CDSE products → pick → run → SSE shows 5 stages → workspace renders all layers → manifest v2 has git SHA + model hashes |
| E2E-03 | UI never mock-runs | UI-launched run manifest: `stages_real==stages_total`, `stages_mock==0` |
| E2E-04 | **Flagship real run** | Gulf S1 + MarineCadastre parquet through `run_pipeline`: attribution `data_source` real; `suspects.source=="real"`; sealed+verified; UI shows REAL badge |
| E2E-05 | Synthetic honesty | Bali synthetic run: SYNTHETIC badge visible on P1 card, P9 map, every P10 row, P12 report + annex |
| E2E-06 | Hindcast uncertainty | published ellipses non-zero, consistent with particle covariance; `origin_uncertainty_km≈0.99` rendered; PHYSICS badge; no ML claim anywhere |
| E2E-07 | Metrics truth | Analytics shows `unet-r34-fullcorpus-e48` identity+sha256, oil-tile IoU 0.5723; drift ML card EXPERIMENTAL·disabled |
| E2E-08 | Attribution integrity | weights sum validated; Σwᵢsᵢ == totals on fresh run; comparison view matches suspects.json; zero-candidate scenario renders honest empty |
| E2E-09 | Vessel dossier | culprit MMSI from ≥2 runs → `GET /api/vessels/{mmsi}` shows both appearances; cross-run tracks render |
| E2E-10 | Report lifecycle | draft→submit→approve(reviewer)→published immutable; annex lists 8 artefact hashes == `/verify`; export stamped; version 2 created on edit |
| E2E-11 | Runs reconcile | DB==replay==disk counts (or UI states which); filters/sort/pagination correct against brute-force |
| E2E-12 | Alerts | AOI poll (dry-run) `errors:[]`; new-scene alert → ack → assign → investigation link; SSE delivery |
| E2E-13 | Audit chain | 10 event types present with authenticated actors+IP; `/api/audit/verify` ok; manual row tamper → verify fails |
| E2E-14 | Cancel | mid-run cancel → job `cancelled`, no sealed partial artefacts, UI honest state |
| E2E-15 | Tiles & perf | native-res zoom via tiles ≤256 KB/tile; `origin_cloud?lite` < full-file time; pipeline ≤ 60 s warm; regression: 654+ tests still pass |

---

## 15 · FINAL SIH DEMO READINESS CHECKLIST

**Narrative (12 min):**
☐ Login (RBAC visible) → Global Monitor with live provider pulse
☐ Alert fires for AOI → create investigation by **drawing AOI + window** → live CDSE scene search
☐ Run pipeline live (~25 s, SSE stage cascade) → workspace walkthrough: SAR tiles → detection (YOLO11n+U-Net named correctly) → characterization (age ≈ Xh · **Confidence LOW**) → drift (PHYSICS badge, real ellipses, origin window) → AIS funnel (SYNTHETIC badge) → attribution (weights, evidence, **Rank #1 · Score**) → report with provenance annex → reviewer approves → export
☐ **The flagship card:** open the sealed Gulf run — "real Sentinel-1, real MarineCadastre AIS, real forcing, end-to-end" — plus benchmark card (86 %/100 %, re-executed)
☐ Honesty tour (judges love this): EO NOT DEPLOYED, drift-ML EXPERIMENTAL·evaluated-negative, synthetic vs real badges, `/verify` live

**Gates before demo day:**
☐ Stage-0 fixes merged & re-audited ☐ E2E-01…15 green with evidence ☐ 654+ tests pass ☐ demo scene cached offline (no network dependency for the core run) ☐ `.env` credentials tested (`test-all`) ☐ two rehearsals incl. offline-mode fallback ☐ UAT spec (OT-*) re-run: no PASS regressions; N-01/N-02/N-03 verdicts quoted honestly in the deck ☐ no guilt language anywhere (grep gate in CI)

**Talking points that convert audit findings into strengths:** byte-identical reproducibility across machines · sha256-sealed immutable runs with live `/verify` · exclusion ledger with measured reasons · a trained ML hindcast we **chose not to ship** because held-out evaluation said no — engineering integrity as a feature.

**End of master plan.**
