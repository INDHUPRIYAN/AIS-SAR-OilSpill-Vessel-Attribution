# OceanTrace — UX Audit (Phase 0)

Date: 2026-09-21 · Branch: `feat/indhu-detection-pipeline` · HEAD `3838f88`
Scope: `main_system/frontend` (React 18, Vite 5, react-router 6, plain JS/JSX, ~25k LOC, 27 pages).
Read-only audit. No product code was changed. Companion files:
[FRONTEND_FUNCTIONALITY_MATRIX.md](FRONTEND_FUNCTIONALITY_MATRIX.md) ·
[BASELINE.md](BASELINE.md) · [BACKEND_GAPS.md](BACKEND_GAPS.md)

`new-UI-folder/` holds 16 PNG mockups only. No code, imported by nothing.
The repo-root `*_inventory.md` files date from 2026-09-08 and are stale (they
predate zones, users, ops, hindcast, SAR database); this audit supersedes them.

---

## 1. Verdict in one page

The frontend is **honest but fragmented**. There is no fake progress, no mock
vessels, no `Math.random` data, and every one of the 105 API client methods
resolves to a real backend route. What is wrong is structural:

| Problem | Evidence |
|---|---|
| The product reads as 26 flat pages, not one investigation workstation | 26 routes, no path params, 5 nav sections; the workspace is item 12 of 26 |
| Nine navigation surfaces compete | rail, "More" flyout, header nav, context strip, palette, status bar links, two tile grids, user menu |
| Five map surfaces, none reusable | `GlobeScene`, `globe/Globe`, `ReplayGlobe`, `CommandMap`, `WorkspaceMap` — each with its own layers, camera, palette |
| The globe is not Earth | flat-shaded sphere + a 372 KB generated land mask. No countries, borders, labels, ocean names, or imagery |
| Four independent clocks | Incident transport, workspace time rail, cinematic beat clock, incident rAF engine |
| Four colour tables disagree | "hindcast" is magenta, sky, or cyan depending on the screen; none reads the `--c-*` tokens that exist for this |
| Two unconnected hindcast systems | pipeline Engine B (workspace) and BAYES-TRACK (`/hindcast`) share forcing and nothing else; the posterior never reaches the Origin stage |
| State is not in the URL | the workspace holds 49 `useState` and persists 5 params; every tab and filter in the app is lost on refresh |
| A running job cannot be followed | no completion notification; Cancel vanishes after navigating away |

---

## 2. Routes

Single `<BrowserRouter>` (`src/main.jsx:10`), routes in `src/App.jsx:51-79`,
mirrored by hand in `ROUTES` (`src/lib/shell.jsx:34-83`). The two lists agree
today; no test enforces it. **No path params anywhere** — all identity is in
query strings. Unknown paths `replace` to `/` silently (no 404).

| Path | Page | Target level (brief IA) | Fate proposed |
|---|---|---|---|
| `/` | Operations (overview console) | L1 Dashboard | becomes Dashboard |
| `/dashboard` | Run registry | — | merge into Investigations |
| `/investigations` | Register | L1 | keep |
| `/investigation` | Workspace | L1 | → `/investigations/:id/:stage` |
| `/report` | Printable report | L1 (Report stage) | → `/investigations/:id/report/print` |
| `/reports` | Report library | L2 | keep |
| `/globe` | Global view + zone editor | L2 Live Map | → `/map`; zone editing moves to System → Zones |
| `/sar-database`, `/satellite` | Scene DB, scene viewer | L2 Detections | merge → `/detections`, `/detections/:id` |
| `/vessels` | Vessels + dossier | L2 | → `/vessels/:mmsi` |
| `/incidents`, `/incident`, `/alerts`, `/my-desk` | Incident register, replay, alert queue, officer desk | L4 add-on | incidents/alerts surface as Dashboard "Needs attention" + notifications; replay folds into the workspace (it is a second workspace) |
| `/environment` | Forcing viewer | inside Hindcast stage | fold into stage panel |
| `/hindcast` | BAYES-TRACK 7-tile grid | L3 Engine Monitoring | → `/system/engines`; results surface in Origin stage |
| `/monitoring`, `/catalog` | Provider health ×2 | L3 Data Sources | merge → `/system/data-sources` |
| `/system`, `/models`, `/analytics` | Ops, models, metrics | L3 | → `/system/health`, model diagnostics behind it |
| `/zones`, `/officers`, `/keys`, `/audit` | Admin | L3 | → `/system/zones`, `/system/users`, `/system/settings`, `/system/audit` |
| `/about` | Static page with named individuals | — | → Help, content trimmed |

Every old URL gets a redirect so existing links, bookmarks and the backend's
search results keep working (see §8 of the plan).

### Broken deep links (4)

| Emitter | URL | Reader | Effect |
|---|---|---|---|
| `components/shell/StatusBar.jsx:78` | `/system?tab=workers` | `SystemOps.jsx:44` reads no params; `workers` is not a tab | always Runtime tab |
| `components/workspace/IntelPanels.jsx:895` | `/incidents?incident=` | `Incidents.jsx:75` reads `focus` | **opens the wrong incident** |
| `backend/api/search.py:103` | `/incidents?incident=` | same | every palette incident hit opens `items[0]` |
| `backend/api/search.py:150` | `/investigation?scene=` | workspace never reads `scene` | every palette scene hit opens the default investigation |

The last two are emitted by the backend. Hard rule 1 forbids changing it, so
the frontend will accept those shapes and redirect to the canonical URL.

Also: `/report` with no `?run` renders a blank document; `GlobeView.jsx:499`
can emit `?run=undefined`.

---

## 3. Navigation (9 surfaces)

1. Left icon rail — 11 items (`LeftNav.jsx:56-73`)
2. "More" flyout — 14 items (`LeftNav.jsx:76-114`)
3. Header primary nav — 6 items from `PRIMARY_NAV`, **the one list not derived from `ROUTES`** (`shell.jsx:94-102`); overlaps the rail 4/6; `/globe` is header-only
4. Header context strip — not clickable; **no breadcrumbs exist anywhere**
5. Command palette ⌘K — real, server-backed (`/api/search`), best-built feature in the app
6. Status bar deep links — 4 (one broken)
7. Operations tile grid — hardcoded copy of top-level destinations
8. My-Desk tile grid — another copy
9. User menu

Plus **four tab-component implementations** (`ui.jsx:198`, `workspace/intel.jsx:38`,
`RightPanel.jsx:44`, `Catalog.jsx:73`), none bound to the URL.

Below 1260 px the header nav disappears; below 1000 px the workspace drops
both sidebars. There is no drawer or bottom-sheet behaviour.

---

## 4. Maps and globe

### Installed

| Package | Version | Role today |
|---|---|---|
| `@deck.gl/*` (core, layers, geo-layers, mesh-layers, aggregation-layers, extensions, react) | 9.3.10 | **renders 100% of data on all five surfaces** (~120 layer instances), incl. `_GlobeView` |
| `maplibre-gl` + `react-map-gl` | 4.7.1 / 7.1.9 | passive basemap under `CommandMap` and `WorkspaceMap` only; zero data layers |
| `deck.gl` umbrella | 9.3.10 | **declared, never imported**; drags `@arcgis/core` (116 MB) + amcharts (33 MB) into `node_modules` |
| Cesium, three, leaflet, mapbox-gl, d3-geo (direct) | — | not installed |

### Surfaces

| Component | Lines | Projection | Mounted by |
|---|---|---|---|
| `globe/GlobeScene.jsx` | 373 | deck `_GlobeView`, sphere mesh + `land.json` + JS graticule | base of the next two |
| `globe/Globe.jsx` | 812 | globe | `/`, `/globe`, `/zones`, workspace `GlobeStage` |
| `ReplayGlobe.jsx` | 377 | globe | `/incident` |
| `CommandMap.jsx` | 605 | mercator + MapLibre | `/incident` |
| `workspace/WorkspaceMap.jsx` | 1127 | mercator + MapLibre | `/investigation` |

Capability is split by surface: zones, incidents and live AIS are globe-only;
SAR raster, mask, look-alikes and tile grid are 2D-only; the hindcast density
heatmap exists only in `CommandMap`. Wind/current is drawn as quiver arrows in
one map and advected particles in the other. `GlobeScene.jsx:38-52` documents
why: deck's `_GlobeView` cannot host a MapLibre style and bitmap reprojection
fails on the sphere — so the 3D globe can never show tiles, labels or SAR.

### Providers (all hardcoded, no `VITE_*` anywhere)

| Provider | URL | Note |
|---|---|---|
| ESRI World Imagery | `server.arcgisonline.com/.../World_Imagery/...` | keyless; attribution suppressed |
| OpenStreetMap | `a.tile.openstreetmap.org` | **direct use violates OSM tile policy for a deployed app** |
| OpenFreeMap positron / dark | `tiles.openfreemap.org/styles/*` | keyless vector styles |
| Own SAR tiles | `/api/tiles/{run}/{z}/{x}/{y}.png` | real, z0–16 |
| `public/geo/land.json` | 372 KB, 658 features | generated from a GSHHG-derived land raster at 0.2°; land/sea only, no countries |

`attributionControl={false}` on both MapLibre instances.

### Camera, selection, transitions

- Workspace: map and globe have separate cameras; hand-off is a scripted
  fly-to plus a 0.6 s opacity crossfade with a hardcoded ±3°/±2° box. Both
  WebGL contexts stay mounted. Globe clicks are discarded
  (`GlobeStage.jsx:79 onSelect={() => {}}`). HTML callouts vanish in 3D.
- Incident: surfaces swap by unmounting; user panning is never synced.
- No camera in any URL. No shared camera store.

### Timelines (4, none shared)

Incident transport (`ReplayControls` + `Incident.jsx` state), workspace time
rail (`StageTimeline` → `timeMs`), cinematic beat clock (`useCinematic`, which
overrides the rail while active), and the raw incident rAF engine.

### Performance facts

- `setState` per animation frame: `Incident.jsx:275`, `StageTimeline.jsx:111`,
  `onViewChange` during flights, globe parallax (`GlobeScene.jsx:175-190`),
  vessel pulse at 20 fps rebuilding every globe layer.
- `CommandMap.jsx:101` and `WorkspaceMap.jsx:225` build ~40 layers in the
  render body with no memoisation.
- No JS viewport culling anywhere. 361 look-alike boxes → ~4,300 hatch paths.
- Bundle: one 2.99 MB JS chunk, no route splitting.
- `forcing_field` must not be fetched concurrently (HDF5 not thread-safe;
  documented at `Investigation.jsx:375`). Any time-scrub design must respect this.

---

## 5. Design tokens and responsive

Canonical set: `src/styles/tokens.css` (125 variables, hand-designed dark +
light). Competing: `workspace/palette.js`, `CommandMap.jsx SEMANTIC`,
`globe/Globe.jsx GLOBE_COLORS`, `ReplayGlobe.jsx REPLAY_COLORS`, and
`report.css` (print; legitimately separate).

| Semantic | tokens.css | workspace | replay map |
|---|---|---|---|
| slick | red | orange | amber |
| forecast | amber | amber | violet |
| hindcast | magenta | magenta | sky |
| origin | amber | white | teal |
| candidate | orange | white | rose |

The brief's language (amber oil, blue forecast, orange→red backward drift,
grey filtered, dashed red gaps) matches none of them exactly; one map palette
derived from tokens fixes all five at once. `useThemeColors()` (`ui.jsx:448`)
already bridges CSS variables to JS and is used by charts but not maps.

14 distinct breakpoint values, no scale. Fonts (Inter, JetBrains Mono) load
from Google Fonts CDN, so the offline e2e test runs on fallbacks.

---

## 6. Hardcoded or fabricated values

No fake vessels, progress, or timestamps. What exists, all to be fixed in P3–P9:

| Value | Where | Fix |
|---|---|---|
| `polarisation \|\| "VV"`, `mode \|\| "IW"`, `product: "GRD"`, `platform: "Sentinel-1"` | `Satellite.jsx:135,213,214`, `Operations.jsx:255`, `IntelPanel.jsx:120`, `IntelPanels.jsx:109-110`, `Investigation.jsx:1022,1025`, **`IncidentReport.jsx:254-256` (printed)** | show "Not recorded" |
| `windage ?? "0.03 (default)"` | `Environment.jsx:183` | show "Not recorded" |
| "CMEMS chain" / "ERA5 chain" badges regardless of provider | `Environment.jsx:153,164` | read `f.provider` |
| `Age confidence: LOW` literal | `Investigation.jsx:1003` | read `age_confidence_label` |
| `SCANNING SAR SCENE · NN%` from the animation clock | `Investigation.jsx:974` | indeterminate; stage count from real status |
| "Download PDF" opens an HTML page | `IntelPanels.jsx:971` | label "Open printable report" |
| 19-box place-name gazetteer asserting names no API returned | `lib/replay.js:290-310` | label as approximate region, or drop |
| `parent_id: "zone-bob"`, `"zone-bob-"` prefix strip | `GlobeView.jsx:171`, `OfficerDashboard.jsx:110` | derive from zones API |
| Default name "Chennai / Ennore investigation" | `Dashboard.jsx:23` | derive from scene |
| DWT always "—", "No vessel image" placeholder | `IntelPanels.jsx:784,791` | remove rows |
| Synthetic radar rings/sweep/blips, scanline, lock-on arcs, origin pulses | `CommandMap.jsx:127-204,285-295,475-488`, `ReplayGlobe.jsx` | remove (decorative animation, rule 2) |
| Silent truncation: `vessels_geojson` default 200 vessels; `origin_cloud?lite` 7500→1800 particles | metadata present, UI shows neither | show "Showing N of M" |

---

## 7. Dead ends, missing screens, unused backend

- **Orphaned code:** `workspace/CaseBrief.jsx` (245 lines, tested, mounted
  nowhere), `lib/useRunEvents.js` (SSE client, tested, used nowhere — the
  workspace still polls at 2 s), `AlertBell`, dead transport imports in
  `GlobeChrome.jsx`, `sarStretch` prop, `stretch` state in `Satellite.jsx`.
- **Dead controls:** five AIS acquisition controls write `aisOn`, which nothing
  reads (`ControlPanel.jsx:112-127`); a read-only fake search box
  (`ControlPanel.jsx:265`); decorative "≡" menu glyphs.
- **RBAC regression:** `Keys.jsx:24` locks out `super_admin`.
- **Backend with no UI** (full list in the matrix): SSE run/job/alert events,
  `/api/jobs/{id}` (real `stages_done/total`), hindcast posterior particles,
  vessel tracks, alert assign, run archive, zone delete, AOI CRUD, AIS stream
  start/stop/flush, provider call history, attribution weights.
- **Missing screens vs the brief:** canonical detection page, vessel dossier
  as one component (the workspace version is weaker than `/vessels`),
  404 page, notifications panel (the bell is only a link), settings/profile.
- **Error handling is bimodal:** newer pages use `DataState`; Dashboard,
  Analytics, Catalog, Monitoring render empty on failure; the workspace
  swallows 14 queries with `.catch(() => null)` and its status poll silently.

---

## 8. Tooling gap against the phase acceptance gate

| Gate | Today |
|---|---|
| lint | **absent** (no ESLint; code already carries `eslint-disable` comments) |
| typecheck | **absent** (no TypeScript, no `checkJs`) |
| unit | Vitest, ~178 cases in 13 files; 23 of 27 pages untested |
| e2e | Playwright, 15 tests, needs a live backend on :8000; no npm script |
| build | `vite build` works; single 2.99 MB chunk |

Proposal: add ESLint (react, react-hooks) in P1 as the lint gate. Do not
convert to TypeScript mid-refactor; treat "typecheck" as not applicable unless
you want `tsc --checkJs` on new `components/maps/**` only.

---

## 9. Globe engine recommendation

**Standardise on MapLibre GL JS as the single map engine, upgraded 4.7 → 5.x
for its native globe projection, with deck.gl layers drawn into it through
`MapboxOverlay` (`@deck.gl/mapbox`).** This is an upgrade of what is installed,
not a second engine; Cesium is not added.

Why this and not the current deck `_GlobeView`:

| Requirement | deck `_GlobeView` (today) | MapLibre 5 globe + deck overlay |
|---|---|---|
| Real countries, borders, labels, ocean names | impossible (no vector tiles on the sphere) | native vector style |
| Satellite imagery on the globe | documented as failing | native raster source |
| World → region → SAR scene as one continuous surface | two engines, crossfade, second WebGL context | one map; globe morphs to mercator as you zoom, same camera |
| SAR tiles on the globe | no | raster source from `/api/tiles/...` |
| Existing ~120 deck layers | — | reused; they move into the overlay |
| Camera in URL, shared selection, one TimeContext | per-surface | one instance, one store |

Costs and risks, stated plainly:

- `react-map-gl` must go 7 → 8 for MapLibre 5. `deck.gl` umbrella is replaced
  by scoped packages plus `@deck.gl/mapbox` (removes ~149 MB of unused deps).
- **Unverified:** I have not run deck 9.3 layers on a MapLibre 5 globe in this
  repo. Deck documents globe support for the overlay, but `HeatmapLayer` and
  `TripsLayer` behaviour on the globe needs a spike. P2 therefore starts with a
  one-day spike rendering slick, tracks, particles and SAR tiles on the globe.
  Fallback if a layer misbehaves: that layer renders only once the projection
  has morphed to mercator (zoom ≳ 5), which is where those layers are used anyway.
- The offline e2e test blocks all external hosts. The geopolitical basemap
  therefore needs a bundled fallback: Natural Earth 1:50m countries, coastlines
  and marine labels as static GeoJSON (public domain, ~2–3 MB), replacing
  `land.json`. Online, vector tiles give detail; offline, Earth still looks
  like Earth.
- Providers move to `VITE_MAP_STYLE_URL`, `VITE_MAP_DARK_STYLE_URL`,
  `VITE_MAP_SATELLITE_URL`, `VITE_MAP_SATELLITE_ATTRIBUTION`. Missing satellite
  config → "Satellite basemap not configured", vector fallback. Direct
  `tile.openstreetmap.org` use is dropped. Attribution is turned back on.

---

## 10. Refactor plan (maps the brief's P1–P10 onto this codebase)

**P1 Shell + IA + URLs.** One sidebar generated from `ROUTES` (Dashboard,
Investigations, Live Map, Detections, Vessels, Reports, System ▸). Header
reduced to logo, search, notifications, health dot, user menu; header nav,
"More" flyout and both tile grids removed. Real breadcrumbs. Path-param routes
with redirects from every legacy URL, including the two backend-emitted
shapes. 404 page. Shared URL-bound `Tabs`. Route/nav parity test and a
link-vs-reader test. ESLint. Fix `Keys.jsx` role check.

**P2 MaritimeGlobe.** Spike, then `components/maps/` with one `MaritimeGlobe`,
a camera+selection store synced to the URL, one `TimeContext`, one map palette
from tokens, `LayerControl`, `TimeController`, `MapLegend`. Modes 1–2. Natural
Earth fallback. Live Map and Dashboard mini-map move first (lowest risk).

**P3 Workspace layout + Detection + Characterization.** Split the 1112-line
`Investigation.jsx`; sticky case header; six-step stepper over the existing
`judgeStages` (kept — it is real); right panel on demand; `WorkspaceMap`
layers ported into `maps/*Layer` modules. SSE via the already-written
`useRunEvents`. Real `stages_done/total` from `/api/jobs/{id}`. The cinematic
presentation is kept and re-seated on `TimeContext`; its decorative radar
theatre is not.

**P4 Hindcast + Origin + SAR mode.** Animate the persisted 25-step particle
cloud on the global slider; show "N of M particles"; BAYES-TRACK posterior
(MAP, HDR, P(τ)) surfaces inside Origin via `/api/hindcast/jobs` for the run.
50% hindcast contour is a backend gap (only 0.9 is written).

**P5 AIS + Vessel Dossier + AIS/current/wind modes.** One dossier component
for workspace and `/vessels/:mmsi`, using `appearance_list` and
`/api/vessels/{mmsi}/tracks`. AIS gaps drawn dashed red from
`ais_gap_minutes`. Wire or delete the five dead AIS controls.

**P6 Forecast + Report + library.** Horizons on the slider. Landfall ETA is a
backend gap — not invented. Honest PDF label. CSV/JSON export reachable from
the stage.

**P7 Secondary pages.** Dashboard from Operations + run registry. Detections
from SAR database + Satellite. System pages merged as in §2. `/incident`
replay retires into the workspace once parity is confirmed.

**P8 States, async, search, notifications.** `DataState` everywhere; no more
`.catch(() => null)`. Cancel works after return (job id is `"job-"+run_id`).
Completion notice is client-side for runs this browser is watching; a
server-side notification is a backend gap. `/` focuses search.

**P9 Cleanup + performance + a11y.** Everything FRONTEND-ONLY/ORPHANED in the
matrix is wired or removed. Memoised layers, no per-frame React state, route
code-splitting, DPR cap, culling.

**P10 Demo mode.** One-click "Demo case" from the Dashboard opening the
flagship run `inv-gulf-flagship-20230108-2day` (real sealed artefacts,
labelled). It currently shows as an unfiled run; whether it can be attached to
an investigation without backend change is to be checked in P10.

### Decisions needed before P1

1. Approve the engine choice in §9 (MapLibre 5 globe + deck overlay, spike first).
2. Approve retiring `/incident` replay and `/hindcast` as standalone
   destinations once their content lives in the workspace and System.
3. Lint gate = ESLint; typecheck gate = not applicable. Or say otherwise.
4. Incidents, Alerts, My Desk: the brief's sidebar has no slot for them. Plan
   is Dashboard "Needs attention" + notifications, with their pages kept
   reachable under System ▸ Zones/Incident routing. Confirm, since zone
   officers use these daily.
