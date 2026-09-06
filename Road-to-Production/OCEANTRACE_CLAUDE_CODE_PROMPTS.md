# OCEANTRACE — CLAUDE CODE EXECUTION PROMPT PACK

Companion to `OCEANTRACE_IMPLEMENTATION_MASTER_PLAN.md`. Paste **one prompt at a time** into Claude Code from the repo root. Do not skip ahead: each prompt assumes its predecessors are merged and green.

## STANDING RULES (prepend to every prompt — copy the block below verbatim at the top of each session)

```
STANDING RULES — OCEANTRACE (apply to everything in this session)
1. Preserve existing working functionality. The pipeline (run.py), engines A/B/C, contracts,
   provenance sealing, provider health, and the 654-passing test suite are load-bearing.
   Before creating anything, check whether an equivalent exists and extend it.
2. Truth rules are non-negotiable: never fake a backend to satisfy UI; never label synthetic
   AIS as real/live; EO is NOT DEPLOYED; the hindcast is PHYSICS (the ML residual is
   EXPERIMENTAL, evaluated negative, disabled — never production); deployed models are
   YOLO11n and U-Net+ResNet-34 ("unet-r34-fullcorpus-e48"), never "YOLOv8"; attribution is
   an explainable weighted ranking — use "Rank #1 · Score 0.82", never guilt language;
   age renders with its LOW confidence; uncertainty is drawn only from real backend values.
3. Additive schema changes only; write an idempotent migration; never drop or rewrite
   existing tables/artefacts; artefact names and layer routes are frozen contracts.
4. Every task ends with: run the FULL test suite (must stay ≥ 654 passed, 3 skipped),
   plus the new tests for this task; capture proof (command output, HTTP transcript,
   artefact diff) under dev_evidence/<PROMPT_ID>/.
5. If a step reveals the plan conflicts with repository reality, STOP, report the conflict
   and the smallest honest alternative — do not improvise architecture.
6. Small commits, one concern each, message prefixed [PROMPT-NN].
```

---

## PROMPT 00 — Reconnaissance refresh & baseline

Goal: re-establish ground truth before touching code (the audit is 1 day old; the tree was dirty).
Do: record HEAD, branch, `git status`; run the full test suite; start the backend exactly as uvicorn does and hit `/readyz`; list the 34 routes; snapshot DB table counts; confirm the six audit defects still reproduce (metrics epoch-29 payload, Dashboard.jsx mock path, `POST /api/aois/poll?dry_run=true` ModuleNotFoundError, zero-radius ellipses in a recent `origin_cloud.geojson`, `?lite=true` timing, mock provider_status.json copy).
Acceptance: `dev_evidence/P00/baseline.md` with all of the above; no code changes.

## PROMPT 01 — Truth fix: `/api/metrics` serves the deployed model (audit M-05)

Exists: `main_system/backend/api/analytics.py:202` reads `data/runs/training/metrics.json` (epoch-29). Deployed identity: `unet-r34-fullcorpus-e48`, eval at `docs/eval/unet-r34-fullcorpus-e48_holdout.json`; screen metrics fine.
Change: metrics loader resolves the DEPLOYED checkpoint — read identity from `weights/model_card.md`-adjacent metadata or a new `weights/deployed.json`; response gains `checkpoint: {name, epoch, file, sha256}` per model; segmentation numbers must equal the e48 holdout file exactly; keep the honest `notes[]`/`pixel_accuracy_note`; drift block labelled `status: "experimental", applied: false, evaluated: "negative"`.
Tests: unit test asserting `/api/metrics` oil_tile_iou == 0.5723 and checkpoint name e48; regression that stale file is no longer read.
Acceptance: live curl transcript in `dev_evidence/P01/`; frontend Analytics still renders (adjust field mapping if needed).

## PROMPT 02 — Truth fix: UI launches real runs (audit N-14) + local scene catalog

Exists: `frontend/src/pages/Dashboard.jsx:29-33` hardcodes `contracts/mocks/scene_meta.json`; real demo scene exists (`data/runs/inv-final-audit/scene_meta.json` → Bali Sigma0 GeoTIFF); `routes.py` validates scene_meta paths.
Change: new `GET /api/scenes/local` listing curated demo scenes `{id, label, scene_meta_path, acquired_utc, source: "REAL (cached Sentinel-1)"}` — seed with the Bali scene; Dashboard replaces the hardcode with a scene picker (default = first real scene); keep the mock scene available but explicitly labelled `MOCK (pipeline smoke test)` and never default.
Acceptance: create+run from the UI path (curl the same API calls) → manifest `stages_real == 5, stages_mock == 0`; transcript in `dev_evidence/P02/`.

## PROMPT 03 — Truth fix: scheduler import path (audit P2/C7)

Exists: `services/scheduler/watcher.py:139` imports `satellite.chain`; `main.py:17-20` adds only repo root + main_system to `sys.path`; `pytest.ini` masks it.
Change: make the backend runtime resolve `scene_service` (and sibling service dirs) the same way tests do — extend the path setup in `main.py` (or package installs via pyproject if already supported); add a smoke test that imports `backend.services.scheduler.watcher` in a subprocess WITHOUT pytest.ini's pythonpath (simulate uvicorn env).
Acceptance: `POST /api/aois/poll?dry_run=true` returns `errors: []` for enabled AOIs (network-permitting; otherwise a provider error, never ModuleNotFoundError); evidence transcript.

## PROMPT 04 — Truth fix: hindcast uncertainty reaches the contract (audit H-06, H-11, C-05, C-07)

Exists: engine-native `origin_window` carries `origin_uncertainty_km 0.9857, coverage 0.908, method "calibrated physics heuristic (not ML)"`; native `confidence_ellipse` features lack axes; `normalise.py:168-169` zero-fills; `:200` truncates forcing to filenames; age `low`→0.25, `age_method` dropped.
Change (two layers):
(a) Engine B: compute per-step ellipse axes from the particle covariance (2σ) and emit `semi_major_m/semi_minor_m/orientation_deg` on each `confidence_ellipse` feature.
(b) `normalise.py`: carry axes verbatim (fail loudly if absent rather than zero-fill); copy `origin_uncertainty_km/_coverage/_method` into `origin_cloud.geojson.metadata`; carry the FULL forcing block `{currents:{provider,dataset,fallback}, wind:{…}, windage, engine, ml_residual}`; add `age_method` and `age_confidence_label` alongside the numeric score in `slick.geojson`.
Contract: extend `contracts/schemas/geo.py` additively (new optional fields); bump contract minor version constant if one exists.
Tests: run a pipeline run in-test (Bali scene) and assert non-zero axes on all steps, uncertainty in metadata, provider names in forcing, `age_method == "damping+fay"`, label `"low"`.
Acceptance: fresh run's published artefacts show all fields; before/after artefact diff in `dev_evidence/P04/`.

## PROMPT 05 — Truth sweep: mock provider snapshot, lite-origin perf, weights validation, manifest v2 seeds

Four small fixes, one prompt:
(a) `run.py:998-1000`: stop copying `contracts/mocks/provider_status.json`; instead snapshot the run's real provider view (from `providers/health` state) into `provider_status.json` with `owner: "measured"`.
(b) `routes.py:262-286 _lite_origin`: replace the O(n²) `f not in parts` scan with a set / single-pass grouping; assert `?lite=true` faster than full-file serialization in a perf test (audit: 3.407 s vs 0.070 s).
(c) Weights loader (`attribution` config load): validate sum==1.0±1e-6 else raise with the offending values (do NOT silently renormalise — explicit is safer for an evidence system); add `GET /api/attribution/weights`.
(d) Manifest v2 seeds: `provenance.py` adds `code_git_sha` (from `git rev-parse`, or "unknown+dirty" honestly) and `models: [{kind,name,version,file,sha256}]` for the two ONNX files (+ engine ids). Keys additive; `/verify` untouched.
Acceptance: fresh manifest shows git SHA + both model sha256s (screen `9a6ff8df…`, segment `a4aac81f…`); perf numbers captured; weights route live; tests green.

## PROMPT 06 — Auth foundation: users, login, sessions (Stage 1 begins)

Build: `users` table + migration `backend/migrations/m001_*.py` (idempotent, runs at startup); argon2id hashing in `core/security.py` (keep Fernet vault untouched); `api/auth.py`: `POST /api/auth/login` (sets HttpOnly SameSite=Lax JWT cookie, 8 h), `POST /api/auth/logout`, `GET /api/auth/me`; `core/authz.py`: `get_current_user`, `require_role(*roles)`; bootstrap admin from env `OT_ADMIN_EMAIL/PASSWORD` on first start.
Do NOT yet guard existing routers (next prompt) — ship auth alongside, verify in isolation.
Tests: login/logout/me lifecycle, wrong password 401, expired token 401, cookie flags.
Acceptance: transcript of the lifecycle; suite green.

## PROMPT 07 — RBAC enforcement + admin token retirement

Change: apply auth dependencies at router-include level in `main.py:118-122` per the role matrix (master plan §8); public routes: `/health*`, `/`, `/api/auth/login` only; migrate the 4 `/api/keys*` routes from `X-Admin-Token` to `require_role("admin")` (keep the token as a deprecated fallback behind `OT_ALLOW_LEGACY_ADMIN_TOKEN=true` for one release); frontend: login page, session context, protected routes, logout; move admin-token usage out of `localStorage`.
Tests: route×role matrix test (parametrized over every mounted route × 5 roles + anonymous) asserting 401/403/200 per matrix — this is the acceptance artefact.
Acceptance: matrix output in `dev_evidence/P07/route_role_matrix.txt`; UI login flow screenshots/DOM dump; suite green.

## PROMPT 08 — Audit v2: authenticated actors, 10 event types, hash chain, /api/audit

Exists: `AuditLog` model + 2 emit sites (`routes.py:453`, `:592`); nested route under `/keys`.
Change: extend table (`actor_user_id`, `ip`, `resource`, `prev_hash`, `row_hash`); `services/audit.py.record(request, action, resource, detail)` pulling actor from session and chaining `row_hash = sha256(prev_hash ‖ canonical(row))`; instrument the 10 event types (auth.login/logout, user.change, incident.* [stub until P10], investigation.create, run.start/complete/cancel, decision.*, report.* [stub], key.set, model.change, data.export incl. `/export` zip); new `GET /api/audit` (search/filter/offset) + `GET /api/audit/export` + `GET /api/audit/verify` (chain re-check); deprecate `/api/keys/audit` (alias).
Tests: every event type produces a row with a real actor; tamper test — mutate a row directly in SQLite, `verify` fails; filters correct.
Acceptance: chain-verify transcript incl. tamper detection.

## PROMPT 09 — Incident entity + registry (audit I-01..04)

Build: `incidents` table per master plan §4; `incident_id` FK added (nullable) to `investigations` and `runs` (stamped at run creation from its investigation); `api/incidents.py` CRUD + `GET /api/incidents` with `status/region/assignee/q/from/to/sort/offset/limit` → `{total, items}`; lifecycle transitions permissioned (attributed/closed → reviewer/admin) and audited (`incident.status`); a lightweight promote action `POST /api/incidents/from_run/{run_id}` seeding geometry from the run's slick centroid.
Frontend: Incidents Registry page (table + filters + status chips + detail drawer listing linked runs).
Tests: CRUD, lifecycle permission denials, filter/pagination correctness vs brute force, audit rows emitted.
Acceptance: create→link run→filter→status-change→audit row, all in one transcript.

## PROMPT 10 — Runs history hardening (audit RH-01..05, RH-08)

Change: `GET /api/runs` gains `q/status/incident/region/from/to/sort/offset/limit` + `{total, items}`; SQLite indexes on `(started_utc)`, `(status)`, `(incident_id)`; seal-time denormalisation writes `top_suspect_mmsi, top_score, slick_area_km2` onto the Run row (backfill script for existing 90+ runs from their artefacts); `archived` flag + route; run reconciliation: execute `backend/backfill_runs.py` over the six orphan dirs (`gulf-real-ais`, `inv-final-audit`, …) and patch the pipeline CLI entry (`run.py:1035-1071`) to upsert a Run row; UI Runs page: filters, sort, pagination, count source label.
Tests: filter/sort/pagination vs brute force; CLI run creates a DB row; counts converge (assert DB==replay for manifest-bearing dirs).
Acceptance: before/after counts (89/93/96 → converged) captured.

## PROMPT 11 — Cross-run vessel index + dossier (audit V-01..09, A-04)

Build: `vessels` + `vessel_appearances` per §4; `services/vessel_index.py` invoked at run seal (and a backfill command over all historical runs) reading `suspects.json` + `vessels.parquet`; extend the AIS contract (`ais_service/ais/contract.py`) with optional `name/imo/flag/call_sign` (MarineCadastre ingest maps VesselName/IMO/CallSign where present; synthetic generator leaves null — never invent); routes `GET /api/vessels`, `/api/vessels/{mmsi}`, `/api/vessels/{mmsi}/tracks`; every payload carries `source: real|synthetic`.
Frontend: Vessel Dossier page (identity card with SYNTHETIC banner when applicable, appearances table, per-run track overlay).
Tests: indexer idempotent; a culprit MMSI appearing in ≥2 backfilled runs returns both appearances; identity fields populated from the real Gulf parquet, null (not fabricated) for synthetic.
Acceptance: `GET /api/vessels/{mmsi}` transcript showing 2+ runs.

## PROMPT 12 — Scene search & acquisition over HTTP (audit N-08/09, D-01)

Exists (CLI, proven live): `backend/fetch_scene.py`, `scene_service/satellite/chain.py` (CDSE→ASF→cache).
Build: `api/scenes.py` — `GET /api/scenes/search?bbox&start&end&source=S1` calling the chain's search (normalize product fields, include per-provider attempt provenance); `POST /api/scenes/download {product_id}` → JobManager job (next prompt provides JobManager if ordering demands; else include a minimal job row here) streaming progress; downloaded product lands in the LocalCache the chain already reads; source=S2 → `501 {"status":"NOT_DEPLOYED"}` honestly.
Tests: search route mocked-chain unit tests + one recorded live search (network permitting, mark skippable); download job lifecycle with a tiny fixture.
Acceptance: side-by-side CLI vs HTTP search results transcript.

## PROMPT 13 — Investigation v2 + AOI CRUD + jobs/SSE/cancel (audit N-03..07/15/16, G-18)

Build: extend `InvestigationCreate` with `incident_id?, aoi (GeoJSON, validated: polygon, at-sea sanity), window_start/end_utc, scene_product_id | scene_meta_path` (server resolves product→cached scene path); `aois` table + CRUD (`api/aois.py`), watcher reads the table (YAML entries migrated in); `services/jobs.py` JobManager wrapping `_execute_run` — job row, progress from `status.json`, cooperative cancel checked at each stage flush; `POST /api/jobs/{id}/cancel`; `POST /api/runs/{id}/rerun` (same inputs from prior manifest); `api/events.py` SSE `GET /api/events/runs/{run_id}` tailing status.json.
Frontend: New Investigation wizard (AOI draw [MAP-mode draw control] / GeoJSON upload / existing AOI, time window, scene search step feeding P12's route, review+launch), SSE-driven stage cascade with poll fallback; cancel button.
Tests: schema validation (land AOI rejected, bad window rejected), cancel mid-run seals `cancelled` with no partial sealed artefacts, SSE delivers 5 stage events, rerun produces a new id with identical inputs recorded.
Acceptance: full wizard-equivalent API transcript → 5/5-real run with SSE log.

## PROMPT 14 — Flagship real-AIS run + real-first ensure_vessels (audit A-05/06 — Stage 4 centerpiece)

Exists: real MarineCadastre parquet `data/ais/real/mc_gulf_2023_01_01.parquet` (185,132 rows, 807 vessels); `run.py:558-628 ensure_vessels` synthesises when coverage fails.
Change: `ensure_vessels` becomes real-first — query the AIS store/known real parquets for origin-window coverage; use real data when covered (rows flagged `source: real`, NO planted culprit); synthetic only as explicit fallback, labelled exactly as today; manifest `ais` block records which path ran.
Execute: acquire a Gulf-of-Mexico S1 IW GRDH scene overlapping 2023-01-01 via PROMPT-12's route (closing D-02 by calibrating from `.SAFE` if that's the acquisition form); run `run_pipeline` end-to-end; seal; verify.
Tests: real-first selection unit tests (covered→real, uncovered→synthetic-labelled); the flagship run's manifest asserts attribution `data_source != "synthetic"` and `suspects.source == "real"`.
Acceptance: the sealed flagship run id + manifest + `/verify` transcript — this is the single most valuable artefact for SIH. Also capture the honest outcome if attribution finds no strong candidate (that is a valid, defensible result — do not tune to force a culprit).

## PROMPT 15 — Alerts & tasking (audit P2)

Build: `alerts` table per §4; scheduler (now importable, PROMPT-03) enabled via config; poll results create `new_scene` alerts; `api/alerts.py` feed + ack/assign/dismiss(reason, audited); SSE `GET /api/events/alerts`; frontend Alerts page (queue table, severity, SLA age, actions) + top-bar bell fed by SSE.
Tests: poll→alert row; dismiss requires reason; assign requires user; ack audited; SSE delivery.
Acceptance: dry-run poll → alert → ack/assign transcript.

## PROMPT 16 — Report v2: compose, annex, review workflow, versioning, CSV (audit RP-04..13, C-11)

Build: `services/report_compose.py` assembling `body_json` from artefacts only — sections: executive summary (templated from manifest+suspects, neutral language), detection, characterization (age with LOW label + method), environment (forcing providers from P04's block), hindcast+origin (uncertainty km), forecast (horizons p50/p90), AIS (source badge, funnel), attribution (weights, factors, evidence, filtered ledger), decisions, methodology+limitations (sourced from `docs/LIMITATIONS.md` + engine docstrings — only sections with real sources render), **Data Provenance Annex** (per-artefact sha256 table == manifest, per-source REAL/SYNTHETIC/CACHED table, git SHA, model hashes); `reports` table with versioning keyed to `artefact_digest`; review state machine draft→in_review→published (reviewer approval audited; published immutable); `GET /api/reports/{id}/export.csv` (suspects+metrics flattening) and `.json`; frontend Report page renders composed body, DRAFT watermark until published, decisions section, print stylesheet kept for PDF.
Tests: annex hashes equal `/verify`; publish immutability (further edits → version+1 draft); role gates; CSV columns golden-file.
Acceptance: full lifecycle transcript + one published report JSON.

## PROMPT 17 — Provider honesty + data catalog + model registry (audit PC-*, M-01/03/13, SH-04/07)

Change: provider status wording `REACHABLE` (reserve `WORKING` for authenticated functional probes; add cheap functional probes where possible: CMEMS STAC HEAD, MarineCadastre file HEAD); tri-state `credentials: configured|n_a|missing` (fix `health.py:80-81`); registry gains coverage metadata (dataset id, bbox, temporal extent, resolution) and explicit `sentinel2: NOT_DEPLOYED`, `live_ais: NOT_DEPLOYED` rows; remove AISStream field from Keys page.
Build: `models` table seeded from ONNX hashes + model_card + drift-residual metadata (`status: experimental, note: "evaluated negative; disabled"`); `GET /api/models`; manifest already carries hashes (P05) — link run→model records; `GET /api/system/health` (psutil host metrics + model-load status + DB + provider summary; NO storage/queue/broker tiles).
Frontend: Data Catalog page (providers with honest states + coverage), Models page (cards incl. EXPERIMENTAL drift card), Health page.
Tests: keyless provider reads `n_a`; EO/live-AIS render NOT DEPLOYED; `/api/system/health` fields trace to measurements.
Acceptance: catalog + health payload transcripts.

## PROMPT 18 — Tile server + workspace depth (audit G-13/D-13, X-05 leftovers)

Build: `api/tiles.py` using rio-tiler (add dependency) over the run's calibrated GeoTIFF with dB stretch params; internal overview generation at seal time (or on first request, cached); frontend SAT mode consumes tiles (raster source), stretch presets, hold-to-peek; AIS viewport decimation for vessels_geojson (server `?bbox=&zoom=` thinning); route-level code-splitting for maplibre/deck bundles.
Tests: tile bytes ≤ 256 KB, correct georeferencing at two zooms vs scene corner coords; decimation preserves suspects at all zooms.
Acceptance: native-resolution zoom screenshot/DOM proof + bundle size before/after.

## PROMPT 19 — Command-center shell: 3D globe mode, MAP tools, palette, search (audit G-06/07/11/14/17)

Build (frontend-heavy; keep 2D default per brief): mode switcher `2D/3D/MAP/SAT` over the single persistent map — 3D via deck.gl `_GlobeView` reusing existing layer definitions (fallback to 2D on WebGL2 absence); MAP mode draw/measure tools (reuse P13's draw control; geodesic measure km/nm); `⌘K` palette (navigate routes/runs/incidents/vessels, time jump, layer toggles) backed by a lightweight `GET /api/search?q=` across runs/incidents/vessels/scenes; ZULU clock + provenance chips already-present data wired into the top bar per UX spec; reduced-motion + keyboard per UX spec §8.6/§10.7.
Tests: mode switch preserves camera/`t`/selection (state unit tests); search route correctness; palette action smoke tests.
Acceptance: mode-preservation evidence + palette demo transcript.

## PROMPT 20 — End-to-end validation & demo freeze

Execute master plan §14 E2E-01…15 in order, capturing evidence under `acceptance_evidence/`; re-run the OT-* UAT spec's P0 tests; run the full test suite; produce `ACCEPTANCE_REPORT.md` (per-test status + evidence links + the flagship run card + honest-labels screenshot set); tag the release candidate; write `DEMO_RUNBOOK.md` (the §15 narrative with exact clicks, offline fallbacks, and the rollback command).
Acceptance: every E2E green or honestly documented as PARTIAL with reason; zero regressions in the 654-test baseline; runbook rehearsed once.

---

## SEQUENCING NOTES

- Prompts 01–05 are **Stage 0** and independent of each other — safe to run same-day.
- 06→07→08 strictly ordered (identity spine). 09/10/11 need 07 (guards) but are mutually independent. 12→13→14 strictly ordered (acquisition spine). 15 needs 03+09+13(SSE). 16 needs 08+09. 17–19 parallelizable after Stage 0. 20 last.
- If SIH time compresses, the minimum credible cut is: 00–05, 06–08, 09, 10, 13, **14**, 16, 20 — identity, incidents, real investigation creation, the flagship real run, and an honest report. 3D globe (19) is the first thing to drop; the audit says so too.
