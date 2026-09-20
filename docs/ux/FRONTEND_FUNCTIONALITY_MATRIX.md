# Frontend Functionality Matrix (Phase 0)

Every control → handler → API → backend route → verdict. Verified against
`main_system/backend/api/*.py` on 2026-09-21. Paths are relative to
`main_system/frontend/src/`.

Classes: **WORKING** · **PARTIAL** (works, but does less than it implies) ·
**FRONTEND-ONLY** (implies an effect it does not have) · **BROKEN** ·
**ORPHANED** (no handler, or never mounted). Pure view toggles that honestly
only change the view are WORKING.

Summary: every API path the frontend calls exists. 0 calls to missing routes.
The defects are the rows below marked other than WORKING — 24 in total.

---

## 1. Defects (act on these in P1–P9)

| # | Control | Location | Class | Action | Phase |
|---|---|---|---|---|---|
| 1 | "Open incident" link uses `?incident=`; reader wants `?focus=` | `components/workspace/IntelPanels.jsx:895` | BROKEN | canonical URL | P1 |
| 2 | Palette incident results `?incident=` (backend-emitted) | `backend/api/search.py:103` | BROKEN | frontend accepts + redirects | P1 |
| 3 | Palette scene results `/investigation?scene=` (backend-emitted) | `backend/api/search.py:150` | BROKEN | redirect to detection page | P1 |
| 4 | Status bar `/system?tab=workers` | `components/shell/StatusBar.jsx:78` | BROKEN | URL-bound tabs | P1 |
| 5 | Credentials page gate `role !== "admin"` locks out super_admin | `pages/Keys.jsx:24` | BROKEN | `hasRole(user)` | P1 |
| 6 | `?run=undefined` link when no run in context | `pages/GlobeView.jsx:499` | BROKEN | hide link | P1 |
| 7 | `/report` without `?run` renders blank | `pages/Report.jsx:23` | BROKEN | empty state | P1 |
| 8 | AIS Trajectories toggle, window select, "Filter vessels", 3 check rows → `aisOn`, read by nothing | `components/workspace/ControlPanel.jsx:112-127`, `pages/Investigation.jsx:208` | FRONTEND-ONLY | wire to layer state or remove | P5 |
| 9 | Read-only "Search" box, value hardcoded "Sentinel-1" | `ControlPanel.jsx:265` | ORPHANED | remove | P3 |
| 10 | Uploaded AOI polygon stored, only its bbox is sent | `Investigation.jsx:716,751` | FRONTEND-ONLY | say "bounding box of your polygon is searched" (backend takes bbox) | P3 |
| 11 | "Download PDF" opens the HTML report | `IntelPanels.jsx:971` | FRONTEND-ONLY | relabel; PDF is browser print | P6 |
| 12 | `SCANNING SAR SCENE · NN%` is the animation clock | `Investigation.jsx:965,974` | FRONTEND-ONLY | indeterminate + real stage count | P3 |
| 13 | Workspace globe clicks discarded | `components/workspace/GlobeStage.jsx:79` | ORPHANED | resolved by single engine | P2 |
| 14 | "≡" menu glyphs with no handler | `IntelPanels.jsx:54`, `ControlPanel.jsx:38` | ORPHANED | remove | P3 |
| 15 | Copy describing a time transport that does not exist; dead imports + `SPEEDS` | `pages/Operations.jsx:10-17,368`, `components/globe/GlobeChrome.jsx:16,100` | ORPHANED | remove | P2 |
| 16 | `sarStretch` prop never passed; `stretch` state never read | `WorkspaceMap.jsx:143,232`, `pages/Satellite.jsx:34` | ORPHANED | remove (or wire a stretch control — tiles API supports `db_min/db_max`) | P4 |
| 17 | Cancel button disappears after leaving the page (needs local `job.id`) | `ControlPanel.jsx:327` | PARTIAL | derive `job-{run_id}` | P8 |
| 18 | Refresh reloads one of several queries | `Satellite.jsx:81`, `Environment.jsx:118`, `Vessels.jsx:62`, `Investigations.jsx:200`, `Incidents.jsx:119`, `Zones.jsx:106` | PARTIAL | reload all page queries | P8 |
| 19 | New-zone default `parent_id: "zone-bob"` | `GlobeView.jsx:171` | FRONTEND-ONLY | derive from zones | P7 |
| 20 | Synthetic radar rings, sweep, blips, scanline, lock-on arcs, origin pulses | `components/CommandMap.jsx:127-204,285-295,475-488`, `ReplayGlobe.jsx:78-117,301-315` | FRONTEND-ONLY (decorative) | remove with replay retirement | P7 |
| 21 | `CaseBrief.jsx` — 245 lines, unit-tested, mounted nowhere | `components/workspace/CaseBrief.jsx` | ORPHANED | reuse as case-summary header source, or delete | P3 |
| 22 | `useRunEvents.js` — SSE client, unit-tested, used nowhere | `lib/useRunEvents.js` | ORPHANED | mount in workspace | P3 |
| 23 | `AlertBell` export | `pages/Alerts.jsx:270` | ORPHANED | becomes notifications panel or delete | P8 |
| 24 | `ReportReview` assumes `/api/reports` is a bare array; other callers tolerate `{items}` | `components/ReportReview.jsx:41-44` | latent | normalise in `api.js` | P6 |

---

## 2. Working controls, by surface

### Investigation workspace (`pages/Investigation.jsx`, `components/workspace/*`)

| Control | Handler | API | Route |
|---|---|---|---|
| Analyse scene (live) | `run()` :671 | POST `/api/investigations/{id}/run` | `routes.py:461` |
| Replay analysis | `run()` | POST `/api/investigations/{id}/replay` | `investigation_page.py:189` |
| Cancel | `cancelRun` :693 | POST `/api/jobs/{id}/cancel` | `routes.py:524` |
| Re-run | `rerunLast` :699 | POST `/api/runs/{id}/rerun` | `routes.py:546` |
| Investigation select | `onPickInv` | GET `/api/investigations/{id}` | ✓ |
| Load scene | `loadScene` :708 | POST `/api/investigations` | `routes.py:234` |
| Search scenes | `searchScenes` :728 | GET `/api/scenes/search` | `scenes.py:277` |
| AOI zone/saved-AOI selects | `setSpatial` | GET `/api/zones/{id}`, `/api/aois` | ✓ |
| Area search | `searchArea` :799 | GET `/api/zones/{id}` | ✓ |
| Wind / current toggles | `onShow` | GET `/api/runs/{id}/forcing_field` | `replay.py:229` |
| Layer panel (10) | `onToggle` | — (disabled from `status.layers_present`) | view |
| Stage chips, prev/next | `go` | — (`?stage=`) | view |
| Time rail, play, speed | rAF | — (domain from real artefacts) | view |
| Beat rail play/stop/skip/jump | `useCinematic` | — (holds on real stage status) | view |
| Zoom, reset, fullscreen, basemap, 2D/3D, measure | local | — | view |
| Create incident / Override gate | `onCreateIncident` :776 | POST `/api/incidents/auto/{run}[?force]` | `incidents.py:306` |
| Record decision | `onDecision` :783 | POST `/api/runs/{id}/decisions` | `routes.py:1034` |
| Compose / Submit / Publish | :790-798 | POST `/api/reports`, `/submit`, `/publish` | `reports.py:97,168,189` |
| Export bundle | `<a download>` | GET `/api/runs/{id}/export` | `routes.py:975` |
| Status | poll 2 s / 10 s | GET `/api/investigations/{id}/status` | `investigation_page.py:100` |

### Global view / Operations / Zones (globe surfaces)

| Control | API | Route |
|---|---|---|
| Coordinate search, click on water | GET `/api/zones/lookup` | `zones.py:176` |
| Edit boundary / Save / Create zone | GET/PATCH `/api/zones/{id}`, POST `/api/zones` (+ assignments) | `zones.py:298,365,516` |
| Officer select | GET `/api/users?role=zone_officer` | `users.py:164` |
| Zone filters, revisions | GET `/api/zones`, `/revisions` | `zones.py:84,250` |
| Layer switches, basemap, zoom, tool rail | — | view |
| Feeds (alerts, investigations, runs, providers, workers, my zones, live AIS) | real polls | ✓ |

### Incident replay (`pages/Incident.jsx`)
Transport, step chips, 11 map-mode presets, 8 layer toggles, surface switch,
vessel select — all WORKING view controls over real layer bundles.

### SAR database / Satellite
Filter + search host (GET `/api/sar/scenes`), catalogue search
(GET `/api/scenes/search`), upload + validate metadata (POST `/api/sar/upload`,
`/upload/{id}/metadata`), Analyse (POST investigations + run), Find vessels
(nav with `?present=1`), mask toggle, bundle download — WORKING.

### Alerts / Incidents / Reports
Ack, dismiss-with-reason, view filters; new incident, status transition, count
filters; compose/submit/publish/revise, CSV/JSON export — WORKING.

### System surfaces
Provider test / test-all; logs filter + clear (admin); keys save + test;
audit filters, verify, CSV export; models tabs; catalog tabs; hindcast run /
demo / job select / engine drawer / WS feed with poll fallback; officers role,
activate, assign, unassign, create — WORKING.

### Shell
Rail, flyout, palette (GET `/api/search`), bell → `/alerts`, theme, user menu,
sign out, provenance chips, Zulu clock, shortcut overlay — WORKING.

---

## 3. Backend capabilities with no UI

| Route | Where it belongs |
|---|---|
| GET `/api/events/runs/{id}`, `/events/jobs/{id}` (SSE) | workspace status (P3) |
| GET `/api/events/alerts` (SSE) | notifications (P8) |
| GET `/api/jobs/{id}` — `current_stage`, `stages_done`, `stages_total` | stage progress (P3) |
| GET `/api/hindcast/jobs/{id}/particles?tau=` | Origin stage posterior (P4) |
| GET `/api/vessels/{mmsi}/tracks` | vessel dossier (P5) |
| GET `/api/attribution/weights` | ranking legend (P5) |
| GET `/api/ais/live/geojson` | AIS mode (P5) |
| POST `/api/ais/stream/start|stop|flush`, `/live/prune` | Data Sources → AIS card, admin only (P7) |
| GET `/api/apis/{provider}/calls` | Data Sources card history (P7) |
| POST `/api/alerts/{id}/assign` | alert queue (P7) |
| POST `/api/runs/{id}/archive` | Investigations register (P7) |
| DELETE `/api/zones/{id}` | Zones (P7) |
| AOI CRUD + poll (`/api/aois*`) | Zones / monitoring areas (P7) |
| GET `/api/decisions` | case history (P6) |
| GET `/api/users/{id}`, `/users/{id}/zones`, `/auth/roles` | Users (low priority) |
| POST `/api/incidents/from_run/{id}`, `/backfill-zones` | superseded by auto-gate; leave unwired |
| GET `/api/scenes/local/{id}`, `/sar/scenes/{key}` | detection detail page (P7) |
| POST `/api/hindcast/jobs` | leave unwired (from_run covers it) |

## 4. Frontend calls with no backend route

None.
