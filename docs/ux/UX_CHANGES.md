# UX_CHANGES — what changed, phase by phase

Companion to [UX_AUDIT.md](UX_AUDIT.md). Each phase appends a section.
Gate for every phase: lint · typecheck (maps only, from P2) · unit · build ·
e2e · BASELINE flows.

---

## P1 — Shell, information architecture, URL contract

### Before → after

| | Before | After |
|---|---|---|
| Navigation surfaces | 9 (rail, "More" flyout, header nav, context strip, palette, status-bar links, 2 tile grids, user menu) | 1 sidebar + palette. Header nav, context strip and flyout removed |
| Sidebar | 94 px icon rail, 11 items + 14 in a flyout, 5 sections | Dashboard · Investigations · Live Map · Detections · Vessels · Reports — rule — System ▸ · Help. Collapsible (icons + tooltips, groups become flyouts); drawer ≤ 1100 px |
| Role-aware nav | hides 3 admin pages | same, plus: **zone officer** gets an Operations group (My Desk, Incidents, Alerts, Replay) in the main sidebar; every other role finds it under System ▸ Incident routing |
| Where am I | non-clickable section/title strip | real breadcrumbs from the URL: `Investigations › inv-… › Drift hindcast & origin` |
| Routes | 26 flat, no path params, declared twice by hand | generated from `ROUTES`; identity in the path |
| Unknown address | silent redirect to `/`, URL erased | 404 page that shows the address and keeps it |
| Tabs | component state, lost on refresh | `?tab=` on System Health, Data Sources, ML Models |
| Lint | none | ESLint 9 flat config, `npm run lint` |

### Sitemap

```
/                                   Dashboard
/investigations                     register
/investigations/new                 workspace, new case
/investigations/latest[/:stage]     workspace, most recent
/investigations/:inv[/:stage]       workspace           (?run= pins a run)
/investigations/run/:run[/:stage]   workspace, unfiled run
/investigations/registry            run registry        (merges into register in P7)
/map                                Live Map
/detections                         scene registry      (?scene=)
/detections/viewer                  scene viewer        (merges in P7)
/vessels[/:mmsi]                    vessels + dossier
/reports                            library
/reports/print/:run                 printable report
/operations/{desk,incidents,alerts,replay}
/system/{engines,data-sources,api-monitor,zones,health,models,analytics,
         environment,audit,users,credentials}
/help
```

All 22 legacy addresses redirect permanently, query preserved, history
replaced (Back does not bounce). Includes the two shapes the backend search
emits, which were broken at baseline: `/incidents?incident=` → `?focus=`,
`/investigation?scene=` → `/detections?scene=`.

### Components

- **Created:** `lib/urls.js` (builders, `canonical()`, `usePathParams`,
  `useUrlTab`), `components/shell/Breadcrumbs.jsx`, `pages/NotFound.jsx`.
- **Refactored:** `lib/shell.jsx` (`ROUTES` with ids, groups, patterns;
  `routeFor` by pattern; `crumbsFor`), `LeftNav.jsx` (sidebar, groups, drawer),
  `TopHeader.jsx`, `AppShell.jsx`, `App.jsx` (router generated from `ROUTES`).
- **Reused unchanged:** all 26 pages. `Investigation.jsx` and `Vessels.jsx`
  keep their `useSearchParams`-shaped code through a path-backed adapter, so
  the workspace's 1100 lines were not touched to move its identity into the path.

### Fixed from the functionality matrix

Rows 1–7: four broken deep links, `super_admin` locked out of Credentials,
`?run=undefined` link, blank `/report`.

Also found by the gate: after the sidebar widened, the workspace stage row
overflowed and its prev/next buttons slid under the right panel (e2e 5 caught
it). The chip row now shrinks and scrolls; the buttons do not move.

### Tests

Unit 183 → 199 (`routes.test.jsx`: route/page parity, legacy addresses all
resolve to live routes, nav placement per role, breadcrumbs). E2E 15 → 21
(`shell.spec.js`: every screen renders without script error, legacy redirects,
404, back/forward, URL tabs, tablet drawer).

### Deferred

- ~100 in-page links still spell legacy addresses and rely on the redirect.
  They move to `url.*` builders in P9.
- 88 lint warnings (75 unused imports in legacy pages, 13 hook-dependency).
  `--max-warnings 88` ratchets it; P9 drives it to zero.
- Stage ids in the path are the existing twelve; P3 maps them onto the six-step stepper.
- Notifications panel (bell still links to Alerts) — P8.
- Map camera and timeline position in the URL — P2.

---

## P2 — Unified MaritimeGlobe engine, modes 1–2, TimeContext, controls

### Before → after

| | Before | After |
|---|---|---|
| Globe | deck `_GlobeView`: flat-shaded sphere, 0.2° land mask, no countries, labels or imagery; could never host tiles | **MapLibre GL 5 globe + deck overlay**: real countries, land borders, coastline, country and sea names; satellite imagery on the sphere; SAR raster capable |
| Basemap data | generated land raster | Natural Earth 1:10m, India point-of-view, bundled (works offline) |
| Basemap choices | Canvas / Contrast / None — three tints of one mask | Geopolitical · Satellite · Dark Maritime, one radio; an unconfigured provider is disabled and says so |
| Providers | hardcoded URLs, attribution suppressed, direct OSM tile use | `VITE_MAP_*`, attribution shown, OSM direct use removed — MAP_DATA_SOURCES.md |
| World → scene | two engines and a cross-fade | one surface; the globe morphs to a flat map as you zoom |
| Camera | per-page state, per-frame React updates, parallax | uncontrolled map, reports on move end; `?c=lon,lat,zoom` on the Live Map |
| Colours | four tables that disagreed | one palette; legend tokens held equal by test |
| Time | four clocks | `TimeContext` + `TimeController` built and tested (mounted in P3) |
| Typecheck | none | `npm run typecheck` — `tsc --checkJs`, `components/maps/**` only |

### Spike

Criteria were committed before the spike code (`GLOBE_ARCHITECTURE.md` §1).
Result: T1–T4 PASS, 0 FAIL → adopt. Registration 0.0 px on globe and mercator;
60 fps animating, 55 fps re-uploading all 7,500 particles per frame.
It also found two backend limits (tile server without overviews; full-resolution
mask PNG) that shaped the SAR layer — BACKEND_GAPS G15, G16.

### Components

- **Created:** `components/maps/*` (see GLOBE_ARCHITECTURE §3),
  `scripts/build_basemap_natural_earth.py`, `public/geo/ne/*`, `public/fonts/*`,
  `.env.example`, `.env.production`, `.env.development`, `tsconfig.maps.json`.
- **Refactored:** `components/globe/GlobeScene.jsx` is now an adapter over
  MaritimeGlobe, so Dashboard, Live Map, Zones, the workspace 3D stage and the
  replay globe changed engines without being rewritten. `globe/Globe.jsx`
  colours come from the palette. `lib/geovalidate` tests slicks against the
  1:10m coastline instead of the 0.2° mask.
- **Removed:** `deck.gl` umbrella dependency (and with it `@arcgis/core`,
  amcharts, d3 — `node_modules` 755 → 497 MB), `public/geo/land.json`,
  `scripts/build_globe_land.py`, cursor parallax.
- **Upgraded:** `maplibre-gl` 4.7 → 5.24, `react-map-gl` 7 → 8,
  `+ @deck.gl/mapbox` 9.3.

### Tests

Unit 199 → 218 (`maps.test.jsx`). E2E 21 → 28 (`globe.spec.js` G1–G7: real
Earth, basemap switch keeps camera and data layers, satellite provider +
attribution, continuous globe → map, camera in URL, water click → zone lookup,
works with every external host blocked). Lint ratchet 88 → 83.

### Deferred

- `WorkspaceMap` → MaritimeGlobe (P3); `CommandMap` retires with `/incident` (P7).
- `LayerControl`, `TimeController`, `MapLegend`, `SourceChip` are built and
  unit-tested but first mounted in P3.
- Camera in the URL for the workspace (P3).
- Route-level code splitting: bundle is one 4.07 MB chunk (P9).

---

## P3 — Investigation workspace: one surface, the case in view, honest progress

### Before → after

| | Before | After |
|---|---|---|
| Map surfaces in the workspace | two: a deck `_GlobeView` stage and a deck+MapLibre mercator map, opacity-crossfaded, two WebGL contexts, two cameras | **one** `MaritimeGlobe`. "Globe" and "Map" are the same camera at different zooms |
| The globe beat | warm a second canvas, fly it, cross-fade, hand the camera over with a hardcoded ±3°/±2° box | two flights of one camera: out to orbit, then down to the scene |
| SAR raster | deck `TileLayer`, `minZoom: 0` — asking the tile server for z0–z9 tiles that take 30–120 s each | the engine's two-source layer: run quicklook below z10, tiles above, one shared URL with the analysis panels |
| Case summary | title + scene id | sticky fact strip — spill area, age ± confidence, origin ± km, top candidate — each present only if the run produced it, plus a **Case brief** button |
| `CaseBrief.jsx` | 245 lines, fully unit-tested, mounted nowhere | the **Brief** context, offered on every stage of the right panel |
| Stepper | `SCENE DETECTION GEOMETRY DRIFT VESSELS ATTRIBUTION` | numbered: Scene · Detection · Characterise · Hindcast → Origin · AIS · Attribution → Report |
| Run progress | `SCANNING SAR SCENE · 73%` — the presentation's animation clock, read as detector progress | `stage 3 of 5`, from `/api/jobs/{id}`; the sweep says only `SCANNING SAR SCENE` |
| Stage updates | poll every 2 s (a 4 s stage looked instantaneous) | SSE via `lib/useRunEvents` — written, tested and unused since it was added — with the poll as its stated fallback |

### Fixed from the functionality matrix

Rows 8, 9, 12, 13, 14, 16, 21, 22: the five AIS acquisition controls that wrote
to a state nothing read, the read-only "Search" box, the fake percentage, the
discarded globe clicks, the dead `sarStretch` prop, orphaned `CaseBrief`,
orphaned `useRunEvents`.

### Autonomous decisions

1. **Stepper labels, not ids.** The brief's six steps are `Detection →
   Characterize → Hindcast → Origin → AIS → Report`. Hindcast and Origin are
   one pipeline stage (the hindcast is *how* the origin is found), as are
   attribution and report. The six chips keep their stage ids (and so their
   test ids) and read `Scene · Detection · Characterise · Hindcast → Origin ·
   AIS · Attribution → Report`.
2. **`GlobeStage.jsx` deleted** rather than kept as a second surface: with one
   engine there is nothing for it to do.
3. **One quicklook URL** (`scene_png?size=1024`) for the map and the analysis
   panels. Three concurrent renders of a 600 Mpx raster made the panel images
   fail; e2e W2 caught it. `RunImage` now retries once, so "not available for
   this run" means absent rather than slow.
4. **`ResizeObserver` → `map.resize()`** in the engine: the workspace collapses
   its panels over a 260 ms grid transition and MapLibre's own resize left a
   band of stale pixels for a quarter of a second (e2e W1 caught it).

### Deferred

- `LayerControl` / `TimeController` still coexist with the workspace's own
  `LayerPanel` and time rail; binding the workspace to `TimeContext` is P4,
  where the hindcast animation needs it.
- `CommandMap` (the `/incident` replay map) is still a second mercator mount —
  it retires with that route in P7.
- Workspace camera in the URL (P4, with the time position).

---

## P4 — Hindcast and Origin: one clock, both contours, both estimates

### Before → after

| | Before | After |
|---|---|---|
| The workspace clock | local `useState` in the page, passed down as props | the shared `TimeContext`: the stage rail renders it, the map layers read it, any panel subscribes with `useTime()` |
| Hindcast contours | the widest ellipse per step; a 50 % contour was never written and never drawn | the uncertainty ellipse **and**, where the run has it, the nested 50 % contour inside it (dashed) |
| Particle count | `Particles 1,800` — the subsampled cloud shown as if it were the run | `1,800 of 7,500`, with what the number means on hover |
| Confidence levels | not stated | `Contours 50 % · 90 %`, or `90 %` with "this run recorded one confidence level" |
| The Bayesian origin | a separate page with no way back to the run; the posterior never reached the investigation | the **Bayesian** context of the Origin stage: MAP origin, credible release window, 90 % HDR, P(τ), multi-modality — the same component the engines page uses |
| No hindcast for this run | nothing said so | "No Bayesian hindcast has been run for this run" + **Run one** (role-gated), or the live status of one that is running |

### Backend exception used

G1 (committed separately, before this phase): Engine B writes the 0.5 contour
beside the 0.9 one, tagged `role: contour`, display only. The attribution gate
skips it and the report still states the 0.9 ellipse — held by contract tests.
Runs sealed before that change carry 0.9 alone, and the panel says so rather
than drawing a contour that was never computed.

### Autonomous decisions

5. **G12 resolved without a backend change.** BAYES-TRACK already tags a job
   started from a pipeline run with `source: "run:{id}"`, so the workspace
   finds a run's hindcast by filtering `/api/hindcast/jobs` client-side. No new
   route, no schema change.
6. **The two origin estimates are shown side by side and never merged.** Engine
   B's ellipse and BAYES-TRACK's posterior are different methods; agreement is
   evidence and disagreement is something the analyst needs to see. The panel
   says so in as many words.
7. **The right-panel context strip scrolls.** A fifth context on the drift
   stage pushed "Forecast" under the panel's collapse button (e2e W2 caught
   it); the strip now scrolls instead of overlapping.

### Deferred

- `TimeController` (the shared time bar component) is built, unit-tested and
  bound to the same context, but the workspace still renders its own rail
  inside `StageTimeline`. They share one clock, so there is no desync — the
  visual consolidation is P9 polish.
- Coastline-impact ETA stays absent (G2): nothing in the stack computes landfall.

---

## P5 — AIS attribution and the vessel dossier

### Before → after

| | Before | After |
|---|---|---|
| Vessel identity | two different blocks: the workspace showed a `DWT` row that was always a dash and a slot for a photograph no provider supplies; the register showed dimensions, draught and cross-run appearances | one `VesselIdentity`, used by both. A field the archive did not supply says **not supplied**; a field nothing in the system can fill has no row |
| "Have we seen this ship before?" | only on the register, never beside a candidate | **Seen before** in the Attribution stage: every other run that considered this MMSI, each linking to that run's attribution |
| AIS gaps | `suspects.json` records one total per candidate — a vessel went dark, but not where | drawn on the track, dashed in the alarm colour, from the intervals between the fixes the archive actually holds, with how long the silence lasted |
| Archive coverage | `first_seen_utc` / `last_seen_utc` fetched and never shown | **First heard** / **Last heard** on the vessel page |

### Autonomous decisions

8. **AIS gaps are derived, not read.** The contract carries `ais_gap_minutes`
   as one number per candidate with no position, so a gap could never be drawn
   from it. `lib/replay.aisGaps` finds the stretches between consecutive fixes
   longer than 30 minutes — a class-A transponder reports every few minutes
   under way — and the map draws only those. The threshold is stated on hover.
9. **A real performance defect the suite exposed.** The presentation fades the
   SAR raster in; the engine treated opacity as part of the map style's
   identity, so every frame of that fade dropped and re-added the image source
   and re-requested a scene render that takes tens of seconds. Opacity is now a
   paint property. The e2e suite went from two different failures per run to
   28/28, and 3.3 → 2.6 minutes.

### Deferred

- A behaviour timeline with anomalies shaded (brief's dossier spec) needs
  per-fix anomaly flags the contract does not carry — not invented. The
  factor bars and the gate table already say *why* a vessel ranked or was
  excluded, which is the same question answered from the data that exists.
- Live AIS / current / wind as named map "modes": the layers and their honest
  empty states exist; a mode switcher that presets them is P7 with the Live Map.
