# GLOBE_ARCHITECTURE

## 1. Engine spike — criteria (written before the spike code)

**Question:** can MapLibre GL JS 5 (globe projection) host this app's deck.gl
data layers through `MapboxOverlay`, as one engine for world → region → scene?

**Setup:** `maplibre-gl` 5.x, `@deck.gl/mapbox` matching the installed deck
9.3, one map with `projection: globe`, real artefacts of the flagship run
`inv-gulf-flagship-20230108-2day` served by the harness backend. Rendered in
headless Chromium with the e2e GPU flags; judged from screenshots and from
numbers read back out of the page.

Each test is scored **PASS**, **DEFER** or **FAIL**.

- PASS — renders correctly on the globe (zoom ≈ 1.5–4) *and* in mercator after
  the projection morph (zoom ≥ 6), geographically registered in both.
- DEFER — wrong or absent on the globe, correct in mercator at zoom ≳ 5, and
  the layer is one the product only needs at regional zoom. Acceptable.
- FAIL — wrong in mercator too, misregistered, crashes, or a layer the product
  needs at world zoom does not render there.

| # | Layer | Deck layer type | Data | Correct means |
|---|---|---|---|---|
| T1 | Slick polygon | `GeoJsonLayer` (fill + stroke) | `/api/layers/{run}/slick` | polygon sits on the scene footprint; centroid screen position within 3 px of `map.project(centroid)` in both projections |
| T2 | Vessel tracks | `PathLayer` | `/api/runs/{run}/vessels_geojson` | lines follow the sphere (no chords through the globe, no far-side bleed); same 3 px registration check on a track vertex |
| T3 | Particle animation | `ScatterplotLayer`, data swapped per timestep from `origin_cloud` `step_index` | `/api/layers/{run}/origin_cloud` | 25 steps animate; ≥ 30 fps measured over 3 s with 7,500 points while playing |
| T4 | SAR raster tiles | MapLibre `raster` source (primary) — deck `TileLayer`+`BitmapLayer` as the alternative | `/api/tiles/{run}/{z}/{x}/{y}.png` | tiles drape on the globe inside the scene bbox; stay registered through the morph to mercator |

Supporting checks (recorded, not scored): pick/hover returns the object on the
globe; `flyTo` from world to the scene crosses the projection morph without a
jump; occlusion — a track on the far side of the globe is hidden.

**Decision rule (fixed in advance):**

- 0–1 of T1–T4 FAIL → **adopt**: one MapLibre-5 globe, deck layers in the
  overlay. DEFER results become a per-layer `minZoom`.
- ≥ 2 FAIL → **fallback architecture**: MapLibre-5 globe for orientation
  (basemap, zones, footprints, markers drawn as *MapLibre* layers), and data
  layers mount only once the same map has morphed to mercator. Still one
  engine, one camera, one component — the deck overlay is simply gated by zoom.

## 2. Spike result — 2026-09-21

Stack: `maplibre-gl` 5.24.0, `react-map-gl` 8.1.3, `@deck.gl/mapbox` 9.3.11 on
deck 9.3.10. Run `inv-gulf-flagship-20230108-2day` (198 tracks, 7,500
particles in 25 steps, 19,884 × 30,034 px scene). Headless Chromium, ANGLE/GPU,
1400 × 850. Both overlay modes (`interleaved` true and false) were run and
gave the same results.

| # | Layer | Globe (z 1.6–3.4) | Mercator (z 7–10.6) | Score |
|---|---|---|---|---|
| T1 | Slick polygon | renders; `map.project` vs deck viewport: **0.0 px** | **0.0 px** | **PASS** |
| T2 | Vessel tracks | follow the sphere, no chords, far side hidden; **0.0 px** | **0.0 px** | **PASS** |
| T3 | Particle animation | — | 25 steps × 300 pts: **60 fps**; all 7,500 re-uploaded every frame: **55 fps** | **PASS** |
| T4 | SAR raster | quicklook drapes on the globe inside the footprint | quicklook z < 10, tiles z ≥ 10, registered against slicks and swath edge | **PASS** (with the split below) |

Supporting: picking returns the slick on the globe; `flyTo` world → scene
crosses the morph without a jump; zero page errors.

**Decision: 0 FAIL → adopt.** One MapLibre-5 map, globe projection, deck
layers in a `MapboxOverlay`. No per-layer `minZoom` was needed for T1–T3.

### What the spike found that the plan did not expect

1. **The SAR tile server has no overviews.** Measured per tile on this scene:
   z7 > 120 s (timed out), z8 ≈ 30 s, z10 ≈ 2 s. Sixteen parallel requests (MapLibre's
   default) exhausted the backend's DB pool (`QueuePool limit … reached`).
   Not ours to change (BACKEND_GAPS G15). Design consequence: SAR is a
   **two-source layer** — `/api/runs/{id}/scene_png` (1024 px quicklook, one
   request, ≈ 20 s cold) as a MapLibre `image` source below z10, the tile
   pyramid at z ≥ 10, and `MAX_PARALLEL_IMAGE_REQUESTS = 4`. The quicklook
   needs a real loading state; it is slow, not broken.
2. **`mask_png` is full resolution** (597 Mpx for this scene — a decompression
   bomb for a browser). Detection overlays therefore use the vector slick
   polygons, never the mask raster, on large scenes (G16).
3. MapLibre's `idle` event does not fire reliably while an overlay repaints;
   nothing in product code may wait on it.
4. `public/geo/land.json` (0.2° raster-derived) is visibly crude at regional
   zoom — confirms the Natural Earth bundle is needed, not optional.
5. `vessels_geojson` tracks are clipped server-side to the scene bbox; lines
   ending at the footprint edge are the data, not a rendering fault.

## 3. Architecture as built (P2)

```
components/maps/
  MaritimeGlobe.jsx   the one map: MapLibre GL 5 (globe projection) + deck.gl MapboxOverlay (interleaved)
  basemaps.js         style builder (Natural Earth bundle), provider config from VITE_MAP_*, fallback reporting
  camera.js           useMapCamera, ?c=lon,lat,zoom encode/decode, zoomForBbox
  TimeContext.jsx     the one clock: TimeProvider / useTime (rAF on a ref, ~20 fps commits)
  MapControls.jsx     BasemapSwitch, LayerControl, TimeController, MapLegend, SourceChip
  palette.js          the one colour table (tokens.css --c-* are held equal by test)
  maps.css            mg-* / mc-* chrome
  types.d.ts          ambient declarations for the typecheck gate
components/globe/GlobeScene.jsx   ADAPTER: old GlobeScene/useGlobeCamera props → MaritimeGlobe
```

**One engine.** `MaritimeGlobe` is mounted (through the `GlobeScene` adapter)
by the Dashboard, Live Map, Zones, the workspace's 3D stage and the replay
globe. deck's `_GlobeView`, the sphere mesh, the JS graticule and the land
mask are gone. Two surfaces still mount MapLibre + deck in mercator directly —
`WorkspaceMap` (P3 ports it) and `CommandMap` (retires with `/incident`, P7).
They are the same two libraries, not a second engine, but they are not yet the
same *component*; that is the remaining debt against "one globe component".

**Contract.**

- Props in: `basemap`, `theme`, `layers` (deck), `sar` ({runId, bbox}),
  `initialCamera`, `show` (countries / coastlines / labels), `graticule`.
- Events out: `onCameraChange(camera, {end})`, `onClick(info)`,
  `onHover(info)` — deck-style picking info, with `coordinate` set anywhere on
  the planet so "click on water" keeps working.
- Imperative (ref): `flyTo`, `fitBounds`, `zoomBy`, `resetNorth`, `project`,
  `pick`, `getCamera`, `getMap`. A flight requested before the map has loaded
  is held and flown on load (deep links).
- The map is **uncontrolled**: React never owns the camera per frame. Moves are
  reported throttled (120 ms) and on move end. The legacy hook distinguishes
  *commands* from *echoes* so a report from mid-flight cannot cancel the flight.

**Modes.** Geopolitical (default), Satellite, Dark Maritime are styles of the
same map; switching is a style diff, so camera, zoom, selection and data
layers survive (e2e G2). SAR is a raster source pair inside whichever basemap
is active (§2 finding 1). AIS, current, wind and intelligence "modes" are layer
presets over the same surface and arrive with their data in P4–P5.

**Transitions.** `flyTo` uses `curve: 1.1` (low, anchored flights — no
orbit-and-return) and honours `prefers-reduced-motion` by jumping. The globe →
mercator morph is MapLibre's own and is continuous (e2e G4: one canvas
throughout).

**Removed on purpose.** Cursor parallax: a React state update per animation
frame on every page with a globe, to make the Earth drift under a still mouse.

**Performance notes.** DPR capped at 2. Hover picking is coalesced to one per
frame. `MAX_PARALLEL_IMAGE_REQUESTS = 4`. The JS bundle is currently one
4.07 MB chunk (MapLibre is now a static import of the shell's dashboard);
route-level code splitting is scheduled for P9.

## 4. Correction after the Analysis regression (2026-09-21)

Section 3 says the deck overlay is *interleaved*. It is now **overlaid**, and
the map's projection **follows the zoom**. Both came from putting the ported
workspace next to the pre-refactor one, stage by stage, on the same run:

| Symptom in the workspace | Cause | Fix |
|---|---|---|
| slick fills broken into specks, glow trails thinned to nothing | interleaved deck layers share MapLibre's depth buffer; everything at sea level fights the globe surface | `MapboxOverlay({ interleaved: false })` - deck on its own canvas, same camera (0.0 px registration, section 2) |
| every text callout missing (FORECAST POINT, ORIGIN POINT, DETECTED SLICK, rank badges) | while MapLibre reports a globe projection, deck draws through `_GlobeView` at **every** zoom, and that view cannot draw billboard text, dashed paths or trips | `projectionFor(zoom)`: globe below z4.4, mercator from z5.0 (hysteresis), decided only when a move **ends** - switching mid-flight swaps the style and aborts the flight |
| a deep link to Detection sat at the default camera for ~20 s | camera commands were held until MapLibre's `load`, which waits for every source - including the SAR quicklook that takes ~20 s to render | commands are released on `styledata` |
| first flight of each page load kept the default zoom | the page computed the fit zoom itself and needed the viewport size before the map existed | "frame this box" is a command the map executes (`fitBounds` via `cameraForBounds`) |

The spike (section 1) tested fills, paths, scatter and rasters. It did not
test `TextLayer`, and it judged the interleaved fills from a world-zoom
screenshot where the damage is invisible. Both omissions are why this was
found by the user and not by the spike. `tests-e2e/tools/_cmp.mjs` (old build
vs new, same run, per stage) is the check that would have caught it.
