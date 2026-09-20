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

Result: _to be filled at spike end (§2)._
