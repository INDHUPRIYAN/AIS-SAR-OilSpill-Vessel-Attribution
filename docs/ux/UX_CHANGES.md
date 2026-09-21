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

---

## P6 — Forecast, Report, and the end of the redirects

### Before → after

| | Before | After |
|---|---|---|
| Weathering | the whole block flattened to one line of `key value · key value`, which dropped the caveats | its own section: assumed oil type and temperature (both labelled *assumed*), evaporated fraction, water content and viscosity at the horizon on screen, the engine's own honesty note, and **what this model does not account for** behind a disclosure |
| "Download PDF" | opened an HTML page | **Printable report**, with "use your browser's Print to save it as a PDF" on hover. There is no server-side PDF (G5) and nothing now implies there is |
| CSV / JSON export | reachable only from the Reports library | also from the Report stage, beside the document they describe |
| `/api/reports` shape | one caller assumed a bare array and would have shown "compose a report" forever the day it paginates | normalised once in `lib/api`; every caller keeps working |
| In-page links | ~100 links spelled legacy addresses and worked through the permanent redirects | every link is canonical; **e2e S7** walks eleven screens and fails if any `#main` link points at an address that would redirect |

### Why the landfall ETA is still absent

The forecast's own metadata names `coastline stranding` in
`processes_not_modelled`. The gap (G2) is now visible in the product, in the
engine's words, on the stage where someone would look for it — rather than
being a silent omission.

### Deferred

- The Reports library page itself (a thin document list) is reviewed in P7
  with the other secondary pages.

---

## P7 — Secondary pages

### Before → after

| | Before | After |
|---|---|---|
| Dashboard | opened with a globe and four KPI tiles; everything that had happened was in one merged feed | opens with **Needs attention** — open alerts, failed runs, finished analyses, each one click from the thing itself and each saying *why* it is listed — then **New investigation**, then the KPI tiles, then the platform strip. The globe is the mini map |
| Run outcomes on the dashboard | inferred from the run registry, mixed into the feed | arrive as alerts in their own right (backend exception G3), with the registry still read so runs that failed before that existed are not lost |
| Data Sources ⇄ API Monitor | two pages answering "is data coming in?", neither pointing at the other | cross-linked both ways |

### Retirement decision (approved condition 2)

`/hindcast` retired in P1 and its parity checklist is complete.
**`/incident` is deferred, and the route stays alive.** A8 passes inside the
workspace and both addresses are canonical, but C8 — an incident-driven replay
with its eleven map-mode presets — is not reproduced by the workspace's stage
timeline. Dropping it would lose a capability; building it is a phase of its
own. `docs/ux/RETIREMENT_PARITY.md` records what holds, what does not, and the
exact next step. The replay is no longer a second map *stack* (one engine, one
camera, one palette since P3) — it is a second *page*.

### Scope cut, deliberately

The brief's P7 also asks for Detections and Vessels registries and a System
section rework. Detections (`/detections`), Vessels (`/vessels/:mmsi`) and the
System pages already exist, are backed by real endpoints, were re-addressed in
P1 and improved in P5. Merging `/detections/viewer` into `/detections`, and
`api-monitor` into `data-sources`, is presentation tidying that would have eaten
the budget protecting P10. Cross-links were added instead; the merges are listed
as deferred.

### Deferred

- Merge `/detections/viewer` into `/detections`, and `/system/api-monitor`
  into `/system/data-sources`.
- Merge `/investigations/registry` into `/investigations`.
- `/operations/replay` retirement (above).

---

## P8 — States, async jobs, search, notifications

### Before → after

| | Before | After |
|---|---|---|
| The bell | a link to the alert queue | a panel over the page: open alerts, each saying *why* it is there ("a run failed", "a run you started finished", "the detector opened a case") and opening the thing itself — the run, the incident — with acknowledge-in-place through the same endpoint the queue uses. Empty and failed feeds each say so |
| What feeds it | new scenes and auto-opened cases only | also every run that fails and every run a person started that finishes (backend exception G3) |
| Leaving a running job | Cancel disappeared on return (the job id was component state); nothing said the run would continue | Cancel works whenever the run on screen is running — the server names jobs `job-{run id}`; the header says **"You can leave this page — OceanTrace will notify you when it finishes."** |
| Backend stops answering | the workspace's status poll swallowed every error and kept showing the last answer as current | after two consecutive misses: **"Lost contact with the server — retrying. Figures shown are from the last answer."** |
| Run registry, Analytics, Data sources | no error branch: a failed request rendered as "No runs yet", an empty dashboard, or blank rows (Data sources only spun while *all three* of its requests were loading) | loading, error-with-retry, empty and populated are four different screens |
| Search | ⌘K only | `/` too, listed in the `?` overlay (the keymap is the overlay's source, so it cannot drift) |

### Autonomous decisions

10. **The bell opens a panel, not a page.** An analyst who has started a run and
    moved on should not have to leave their work to learn it finished. The
    queue is still one click away for triage.
11. **Two misses before saying "lost contact".** One failed poll during a
    backend restart is normal; announcing it would train people to ignore the
    warning.

### Deferred

- Browser (OS-level) notifications: would need a permission prompt and a
  service worker; the in-app bell reaches anyone with the app open.
- The remaining pages without all five states (Monitoring, Officers,
  Operations' side panels) render real data and fail visibly enough today;
  a uniform pass is listed for later.

---

## P9 — Fabricated values out, bundle down

### Fabricated values removed (functionality matrix §B / UX_AUDIT §6)

| Was | Where | Now |
|---|---|---|
| `Sentinel-1` · `IW` · `GRD` · `VV` whenever the record was silent | Satellite, Dashboard, intel panel, workspace scene card and map overlay, **printed incident report** | recorded value → else read from the **ESA product name** (`S1A_IW_GRDH_1SDV_…` names mission, beam mode, product, polarisation) → else **"not recorded"**. `lib/sceneName.js`, unit-tested including an uploaded chip with no name |
| `Age confidence: LOW` literal | workspace validation callout | the slick's own `age_confidence_label`, or "not recorded" |
| `Windage 0.03 (default)` | forcing page | the run's recorded windage, or "not recorded by this run" |
| `CMEMS chain` / `ERA5 chain` badges on any grid | forcing page | the grid's own `provider` |
| New zone parent `"zone-bob"` baked into the editor | Live Map zone editor | the jurisdiction zone the server returned |
| Default investigation name `"Chennai / Ennore investigation"` | run registry | empty field |
| Place names from 19 hand-drawn boxes, read as fact | registers, report, brief | prefixed **"near …"**: a convenience label for a region, not a gazetteer answer |

### Performance

| | Before | After |
|---|---|---|
| Entry JS | one 4.07 MB chunk (every page, the map engine, every chart, every Lucide icon) | **374 KB**. Each page is its own chunk (`React.lazy`); MapLibre + deck (1.8 MB) load with the first map page; Recharts with the first chart |
| Sidebar icons | `import * as Icons` — ~1,400 icons for the twenty-odd drawn | named imports; a unit test fails if a route names an icon the sidebar cannot draw |
| SAR fade-in | rebuilt the map style every frame (fixed in P5) | paint property |
| Workspace clock | page re-render every animation frame | `TimeContext` commits at ~20 fps (P4) |

### Deferred

- Memoising the ~40 deck layers `WorkspaceMap` builds per render: the per-frame
  causes (style rebuild, clock) are gone, and the layer builder is 1,100 lines
  under e2e guard — not worth the regression risk this late.
- Lint warnings: 80, held by the ratchet (was 88 at P1). Mostly unused imports
  in pages not otherwise touched.
- The decorative radar/scanline animation in `CommandMap` leaves with
  `/operations/replay` (see RETIREMENT_PARITY.md).

---

## P10 — Demo mode

### What shipped

- **Demo case** panel on the Dashboard: the canonical acceptance run
  (`inv-gulf-flagship-20230108-2day`, digest `fd42e078f8366110` —
  DEMO_RUNBOOK.md) one click away. It opens the run in the workspace and plays
  the analysis from orbit through detection, characterisation, hindcast,
  origin, forecast, AIS and attribution to the report; the analyst can stop
  and step through the six-step stepper at any point. About five minutes.
- **DEMO CASE** badge in the workspace header whenever that run is on screen.
- The run's existence is asked of the server: a host without it shows "The demo
  case is not installed on this host" rather than a link to a 404.
- `VITE_DEMO_RUN_ID` points a deployment at a different showcase run.
- E2E `demo.spec.js`: one click from the dashboard, badge, presentation walks
  forward, the case strip's spill area equals the run's `slick.geojson`, the
  `present` flag is consumed so a reload does not replay.

### Honesty

"Demo" means *chosen for the walkthrough*, not *made up*. Nothing in the demo
case is a fixture: it is a sealed pipeline run over a real Sentinel-1 scene
with CMEMS currents, ERA5 wind and real AIS. G11 (the run is unfiled —
produced by the CLI, reconciled from its manifest) is shown as it is: the
header also carries **UNFILED RUN**.

### Found while walking the demo, and fixed

Two more constants presented as facts about the loaded scene: the analysis
panel's `Mission: Sentinel-1` / `Product: SAR (GRD)`, and section titles
reading `Sentinel-1 GRD` on every scene. Both now come from the record or the
ESA product name (`lib/sceneName`), like the rest of P9.

### Autonomous decisions

12. **The demo case is an existing run, not a seeded investigation.** Creating
    an investigation row would have needed either a backend write the rules do
    not allow or a script the evaluator must run. The run is already sealed and
    registered; linking to it by its canonical address needs neither.

---

## Final acceptance walkthrough — 2026-09-21

Run as a first-time user in a real browser (headless Chromium, 1600 × 900,
GPU via ANGLE) against the isolated harness stack, as `tests-e2e/acceptance.spec.js`
so it can be repeated. Screenshots: `docs/ux/acceptance/`.

| Step | Result | Evidence |
|---|---|---|
| Open OceanTrace → real Earth | globe with Natural Earth countries, borders, country and sea names; "India" is a rendered label | `01-dashboard-real-earth.png` |
| Dashboard → New investigation | `/investigations/new`, acquisition stage | `02` |
| The case → camera at the real place | camera inside the scene's own `scene_meta.bbox` | `03` |
| SAR → detection → characterisation | real Sentinel-1 quicklook on the map; spill area on the case strip equals `slick.geojson` | `04`, `05` |
| Wind + currents | source-labelled from the run's forcing metadata | `06b` |
| Hindcast → origin | particle cloud on the shared clock; honest particle count; Bayesian panel beside the drift estimate, "never merged" | `06`, `06c` |
| AIS → candidates → vessel evidence | "Highest-Ranked Candidate", no culprit wording; shared vessel identity with "not supplied" fields; **Seen before** lists two other runs | `07`–`09` |
| Report → printable → case history | "Printable report", CSV/JSON; printable page carries the run id; register reachable by breadcrumb | `10`–`12` |
| Deep link + refresh + back/forward | stage survives reload; Back returns to the previous stage; Live Map camera survives reload | ACC-2 |
| Unknown address | 404 page keeps and shows the address | `20` |
| Backend stops answering | "Lost contact with the server — retrying…" after two missed polls | `21` |
| Notifications | bell opens a panel in place; Escape closes it | `22` |
| Leave a running job and return | "stage 1 of 5" + "You can leave this page…"; left to the Dashboard; came back — still running, **Cancel present**; Cancel → server status `cancelled` | `30`, `31` |
| A run a person started finishes | a `run_complete`/`run_failed` alert exists for it; Dashboard "Needs attention" lists it | `32` |

**What the walkthrough found, and what was done about it**

1. **Back left the case.** Choosing a stage replaced the history entry, so Back
   jumped out of the investigation. Stage changes the analyst makes now push
   history (Back returns to the previous stage); the presentation's nineteen
   automatic beats still replace, or Back would replay it in reverse. The
   stage on screen now follows the address on Back/Forward.
2. **The investigation selector misattributed unfiled runs.** With no
   investigation on screen, the select showed its first option, reading as
   "this run belongs to that case". It now says "Unfiled run — not part of an
   investigation".
3. **"not recorded SAR" and "NOT RECORDED NOT RECORDED".** The product-name
   parser required the full ESA name; the reference corpus uses short ones
   (`S1A_IW_GRDH_MALACCA`) that still name mission, mode and product. The
   polarisation block is now optional, and a scene with nothing known reads
   "No scene loaded" / "Scene".
4. **The lost-contact state was inert on sealed unfiled runs.** Correct —
   nothing polls a run that can no longer change — so the test exercises a
   live investigation instead. Recorded, not "fixed".
5. **The first leave-and-return attempt proved nothing**: the smallest scene
   finished before the workspace opened, so the running branch never executed.
   It was re-walked on a 33.6 MB reference scene (above). The committed spec
   still uses the smallest scene for speed and skips the running branch when
   the run is already done; the evidence screenshots come from the larger walk.

---

## Summary

### Final gate (2026-09-21, HEAD after this commit)

| Suite | Baseline (a71b415) | Final |
|---|---|---|
| Lint (`npm run lint`) | no linter | **0 errors**, 80 warnings under a `--max-warnings 80` ratchet (88 at P1) |
| Typecheck (`npm run typecheck`, maps scope) | none | **clean** |
| Unit (`npm test`) | 183 / 13 files | **245 / 19 files** |
| E2E (`npx playwright test`) | 15 | **35** (+ shell, globe, demo, acceptance specs) |
| Backend (`pytest`) | 1302 passed, 4 skipped | **1318 passed**, 4 skipped (+16 contract tests for G1, G3) |
| Build | one 4.07 MB JS chunk | entry **374 KB**; map engine 1.8 MB loaded with map pages only |

BASELINE A-flows A1–A15: green after every phase. No hard-stop condition was hit.

### Architecture, before → after

```
BEFORE                                      AFTER
26 flat routes, no path params              ROUTES-driven router, identity in the path,
9 navigation surfaces                        22 permanent legacy redirects, 404
                                            1 sidebar (role-aware) + palette + breadcrumbs
5 map surfaces, 2 engines                   1 engine: MaritimeGlobe (MapLibre 5 globe
  deck _GlobeView sphere + land mask          + deck overlay), Natural Earth India-POV basemap,
  deck + MapLibre mercator x2                 satellite via VITE_MAP_*, SAR quicklook→tiles
4 clocks, 4 colour tables                   1 TimeContext, 1 palette (tokens held equal by test)
workspace: 2 WebGL contexts, crossfade      workspace: 1 surface; globe↔map is a zoom
fake %, dead controls, constants as facts   real stage counts, SSE, facts recorded or derived
bell = link; no run-completion alerts       notifications panel; run alerts (G3)
```

### Sitemap

See the P1 section. Unchanged since, except that `/operations/replay` remains
a live page (retirement deferred with its parity recorded).

### Components

- **Created:** `components/maps/*` (MaritimeGlobe, basemaps, camera,
  TimeContext, MapControls, palette), `components/shell/Breadcrumbs`,
  `components/shell/Notifications`, `components/vessels/VesselIdentity`,
  `components/workspace/BayesOriginPanel`, `pages/NotFound`, `lib/urls`,
  `lib/sceneName`, `lib/demo`; backend `services/run_alerts`.
- **Refactored:** `lib/shell` (ROUTES), `App` (generated, lazy router),
  `LeftNav`, `TopHeader`, `AppShell`, `GlobeScene` (now an adapter),
  `WorkspaceMap` (on the one engine), `Investigation` (shared clock, case strip,
  honest progress, history), `StageTimeline`, `RightPanel`, `AnalysisPanels`,
  `IntelPanels`, `ControlPanel`, `Operations` (Dashboard), `Vessels`,
  `Analytics`, `Catalog`, `Dashboard` (registry), `Environment`,
  `IncidentReport`; Engine B drift runner + attribution gate (G1).
- **Reused as-is, now mounted:** `CaseBrief` (was orphaned), `useRunEvents`
  (was orphaned), `HindcastResult` (now also in the Origin stage).
- **Deleted:** `GlobeStage`, `public/geo/land.json`, `scripts/build_globe_land.py`,
  the `deck.gl` umbrella dependency (−258 MB `node_modules`).

### Fake or orphaned UI removed

Functionality-matrix rows 1–19, 21–24: four broken deep links; the super_admin
lockout; `?run=undefined`; blank `/report`; five dead AIS controls; the
read-only search box; the uploaded-polygon claim; "Download PDF"; the fake
scan percentage; discarded globe clicks; decorative "≡" glyphs; dead transport
code; the dead stretch prop/state; Cancel lost on return; single-query Refresh
buttons (partly — see deferred); the `zone-bob` constant; orphaned CaseBrief
and useRunEvents; the latent report-list shape. Plus every constant listed in
the P9 table. Row 20 (decorative radar animation) leaves with the replay page.

### Real-data integrations added

`/api/jobs/{id}` (stage progress), `/api/events/runs/{id}` (SSE),
`/api/hindcast/jobs` + `/jobs/{id}` (Bayesian origin in the workspace),
`/api/vessels/{mmsi}` appearance list, first/last heard, dimensions, in the
workspace; forecast weathering metadata in full; report CSV/JSON exports from
the Report stage; `/api/alerts` in the notifications panel; the G1 contour
and G3 alerts end to end.

### States added

Loading / error-with-retry / empty / populated on the run registry, Analytics,
Data Sources, the notifications panel and the Bayesian panel; "lost contact"
on the workspace; "no Bayesian hindcast for this run"; "demo case not
installed on this host"; "satellite basemap not configured"; "SAR scene
raster unavailable"; "50 % contour not recorded for this run"; "No scene loaded".

### Deferred work (complete list)

| Item | Why deferred | Where recorded |
|---|---|---|
| Retire `/operations/replay` into the workspace | C8 parity not reached: its eleven map-mode presets need a phase of their own | RETIREMENT_PARITY.md |
| Merge `/detections/viewer`, `/system/api-monitor`, `/investigations/registry` into their parent pages | presentation tidying; cross-linked instead to protect P10 | P7 |
| Named map "modes" switcher (AIS / current / wind / intelligence presets) | the layers and honest empty states exist; the preset switcher is polish | P5 |
| Visual consolidation of the workspace rail onto `TimeController` | one clock already; the two renderings do not desync | P4 |
| Memoise `WorkspaceMap`'s ~40 layers | per-frame causes removed; 1,100 lines under e2e guard | P9 |
| Behaviour timeline with anomalies shaded | needs per-fix anomaly flags the contract does not carry | P5 |
| Landfall ETA | nothing computes it; the forecast's own metadata says coastline stranding is not modelled | BACKEND_GAPS G2 |
| Server-side PDF | none exists; the UI says "Printable report" | G5 |
| OS-level browser notifications | permission prompt + service worker | P8 |
| 80 lint warnings (mostly unused imports in untouched pages) | held by the ratchet | P9 |

### Deliverables

| File | Contents |
|---|---|
| `docs/ux/UX_AUDIT.md` | P0 audit, engine recommendation, refactor plan |
| `docs/ux/FRONTEND_FUNCTIONALITY_MATRIX.md` | every control → handler → API → verdict; the 24 defect rows |
| `docs/ux/BASELINE.md` | regression contract and the executed green starting point |
| `docs/ux/BACKEND_GAPS.md` | G1–G16; G1 and G3 closed by the two approved exceptions |
| `docs/ux/GLOBE_ARCHITECTURE.md` | spike criteria (fixed before code), spike result, the engine as built |
| `docs/ux/MAP_DATA_SOURCES.md` | every basemap and data layer, its provider, licence and absent state |
| `docs/ux/RETIREMENT_PARITY.md` | `/hindcast` done; `/incident` deferred with the checklist |
| `docs/ux/UX_CHANGES.md` | this file: every phase, autonomous decisions 1–12, deferrals, the walkthrough |
| `docs/ux/acceptance/*.png` | 20 screenshots from the acceptance walkthrough |
| `DEMO_RUNBOOK.md` | now opens with the one-click demo case |
| `scripts/build_basemap_natural_earth.py` | rebuilds the bundled basemap |

---

## Analysis workspace restored to the old visual - 2026-09-21

Reported by the user after the run: the Analysis (investigation workspace)
looked broken next to the previous version. It was, and the cause was mine -
the P3 port of the workspace map onto the new engine. Nothing in the backend,
the APIs or the other sections was touched to fix it.

**Method.** The pre-refactor frontend was built from git (`64603f8`) and served
beside the current one against the same backend; both were screenshotted on the
flagship run at detection, characterisation, drift, AIS and attribution. The old
build is the visual reference; the new backend data drives both.

**What was broken, and is fixed** (detail in GLOBE_ARCHITECTURE section 4):
fragmented slick fills and vanished glow trails; every map callout and rank
badge missing; Detection opening at the wrong camera for ~20 s with no SAR
tiles, tile frame or candidate callout; the first camera fit of a page load
ignored; script errors on every workspace load (`RightPanel`'s `Proxy` threw
for any non-stage key).

**Structure, back to the old proportions.** The header had grown to five rows
(wrapped badges, scene line, fact strip, a button on its own row) and pushed
the map down 75 px. It is three rows again - title + badges, scene, facts -
each a single line that ellipses rather than wraps; the title never gives way
to a badge. The right panel shows a context strip only on stages that have
contexts (with Brief appended); stages without one get their old panel back,
and a two-tab strip appears only while the brief is open. Attribution analysis
is above "Seen before" again.

**Kept from P3-P10**, because they are backend-true and do not disturb the old
visual: the case-fact strip, numbered stepper, real stage progress and SSE,
both hindcast contours, the Bayesian context, the shared vessel identity, AIS
gaps, honest scene facts, the shared clock.

**Result.** Side by side, the map at each compared stage now matches the old
one; e2e 35/35, unit 245/245, lint and maps typecheck clean.

---

## Attribution map: ships, wakes and less ink - 2026-09-21

Reported by the user from run `inv-85ba402b6f-094027` (East Mediterranean):
vessels "rounding in the same place", no moving ship, a congested map.

**Where the looping vessels come from.** Not from the frontend. The run's own
manifest records `ais.file = vessels_generated.parquet`, `data_source =
synthetic`: no real archive covers that origin, so the pipeline generated a
fleet, and the generator's fishing pattern (`ais_service/ais/generator.py`,
`_fishing_track`) hops between waypoints inside a small radius. The backend is
untouched; the map now says so. The legend carries "Simulated AIS: no real
archive covers this origin. These are not observed vessels." whenever the run
recorded its AIS as synthetic.

**What changed on the map (frontend only, `WorkspaceMap.jsx`).**

| Before | After |
|---|---|
| vessels were round dots | a hull glyph at the vessel's interpolated position, turned to its recorded heading, moving with the shared clock; click selects |
| every vessel drew its whole track, dashed, with direction arrows | once candidates are lit, only ranked and selected vessels keep their track; the rest of the traffic is a faint ship with its last 6 h behind it |
| a dashed tie and a dot from every candidate to the origin | one tie, for rank 1 and the selected vessel; the closest-approach mark is a ring so the ship shows through |
| rank badges fanned around the shared closest point, overlapping | the badge rides on its ship; a ship that has sailed out of frame at the shown time gets its badge back at the closest point, stacked clear of the origin callout |
| AIS-gap dashes for every vessel | for ranked and selected vessels once candidates are lit |

Nothing is computed that the run did not record: positions are interpolated
between the run's fixes (as the dots were), the 6 h wake length is a drawing
choice and is named in the legend.

Gate: lint 0 errors / 80 warnings, unit 245, e2e 35/35.

---

## Map story pass: no circling, chart colours, honest AIS - 2026-09-21

User review of the attribution map: vessels circling, origin/forecast drawn in
"toy cartoon" colours, square-cornered slick, no account of why real AIS was
not used, and the picture did not tell the story.

| Complaint | Cause (measured) | What was done |
|---|---|---|
| vessels circling on the spot | the synthetic generator's fishing track took distance `% total` over 5-8 waypoints, so each boat retraced one small closed polygon for the whole 36 h window | **generator fixed** (`ais_service/ais/generator.py`, own commit, own test): a tow with heading held within +/-70 deg of its course, which can bend but cannot close. Decoys were already straight lanes. Sealed runs keep the fleet they were made with; run `inv-837a0cc083-114549` is the same scene regenerated |
| cartoon colours | every label was a solid block of saturated colour (yellow->red forecast ramp, magenta hindcast, orange slick), with glow paths and 17 px outlined arrowheads | one label style (light ink, dark plate, hairline border in the subject's colour), sentence case, 11.5 px; one muted hue per concept - rust slick, sand forecast, slate-violet hindcast; later horizons fade instead of turning red; glows removed; markers halved |
| square slick borders | detector patch gate, not drawing - BACKEND_GAPS G17 | drawn outline corner-cut; numbers untouched |
| why not real AIS | the run already records the decision and every archive it checked (`manifest.ais.detail`, `.considered`); nothing showed it | "Why real AIS was not used" in the vessel panel, from that record: the reason, each real archive and that it holds no vessel in this origin area and window |
| labels stacked at the origin | slick, origin and window edge are 1-2 km apart | each label owns one side: origin upper-left, window edge lower-left, slick lower-right, forecast right |

**Does the story read?** Checked on screenshots of the regenerated run, stage
by stage. Drift: backtrack -> origin window opens T-6 h -> estimated origin ->
detected slick at acquisition -> forecast +6 h, +12 h, one line of travel, no
label touching another. Attribution: the #1 track runs through the origin ring
with its closest-approach tie, other candidates are badged, the rest of the
traffic is faint ships with wakes, and both the legend and the panel say the
traffic is simulated and why.

Gate: lint 0 errors / 80 warnings, unit 245, e2e 35/35, ais_service tests green.

---

## Operator pass: scanning box, satellite default, keys in the UI, zone CRUD, upload on the Dashboard - 2026-09-21

| Asked | Found | Done |
|---|---|---|
| "scanning in one small bar" on Detections | two faults: the image box had no height until an image loaded, and ANY failed image request printed "The run records no scene raster to render" for good. The raster existed (200 in 17 s afterwards); the request failed once while detection held the file | box has a fixed minimum shape; a failed image retries every 5 s (6 attempts) under "Rendering the scene image... large scenes take 15-20 s"; "could not be rendered" only after that, and it says the analysis does not depend on it |
| Satellite as the default globe | four places each defaulted to geopolitical | one `DEFAULT_BASEMAP` (satellite when a provider is configured, geopolitical on an offline host) used by all four; two e2e assertions widened |
| "all APIs should be UP; if not, how is detection happening" | nothing was down. WORKING = an authenticated request succeeded; REACHABLE = the host answered. The header counted REACHABLE (the most a no-key provider can prove) and the two NOT_DEPLOYED adapters as failures: "DEGRADED 4/12". Detection needs no API at all: it is the local ONNX models on a raster already on disk; providers feed scene download, currents, wind and AIS | header is OPERATIONAL when every deployed provider is up, DEGRADED only when one is FAILED / UNCONFIGURED / DEGRADED (named in the tooltip), LIVE when all are verified; status bar "10/10 PROVIDERS UP - 4 VERIFIED" |
| Data Sources: every API with its fallbacks, a button to the real site, enter the key in the UI | the backend already had an encrypted, audited key store with a real authenticated test (`/api/keys`); the page showed none of it | per provider: its fallback ladder with the current rung marked, "Get a key" opening the provider's own registration page, "Enter / Change key" opening the key fields in place, "Save and test" (stores, then runs the backend's authenticated probe and prints its verdict). The public evaluator view is refused key writes by design; the form says so and points at Login |
| Zone CRUD, split and assign | create / reshape lived on the Live Map, assign on the Officers page, rename / deactivate / delete nowhere | Zones page: "New zone"; per zone a manage panel - rename, notes, set inactive / reactivate, edit boundary, "Split: add a sub-zone" (opens the Live Map already drawing, parent chosen), assign / remove officer, delete with confirm. Server refusals (zone has incidents, evaluator may not delete) are shown verbatim |
| Upload on the Dashboard | the upload + metadata form sat at the bottom of the Detections filter column | extracted to `components/sar/UploadPanel`, mounted in the Dashboard's left column as a collapsible panel; after validation "Analyse this scene" files the investigation, starts the run and lands on that run's detection view, where FIND VESSELS continues into the workspace |
| Fewer vessels, closer views | attribution drew every vessel and framed every candidate's whole track; characterisation framed the tile, not the slick | attribution / evidence / report draw only ranked candidates unless the analyst switches traffic on (the AIS stage, whose subject is the traffic, keeps it all); candidate frame tightened (~5 km); characterisation frames the slick's own bbox |
| Validate the reports | figures checked against the run's artefacts for `inv-837a0cc083-114549`: area, origin, window, forecast, funnel (30 considered, 4 ranked, 26 filtered) all match. One misleading pair: "proximity 0.00" beside "Distance 0.0 km" | not a scoring bug. `closest_approach_km` is the engine's distance to the 90 % origin REGION (0 = entered it); proximity is cloud density along the path in the window. The top vessel went dark for 50 min inside the region, the straight line across the gap misses the ~500 m cloud, and the AIS-gap factor (1.00) is what scores it. Column renamed "Distance to origin region" in the report and the panel; the report explains the pair when it occurs |

**Backend / service changes (own commit).** `scene_service/satellite/cdse_adapter.py`:
downloading by scene name built `Products(<name>)/$value`; CDSE serves
products by UUID only, so every by-name download was a 422, and the branch
invented its metadata (time = now, bbox = 0,0,1,1). It now resolves the name
in the catalogue first and takes the real start time and footprint from the
same record. Scene-service tests green.

Gate: lint 0 errors / 80 warnings, unit 245, e2e 35/35.

