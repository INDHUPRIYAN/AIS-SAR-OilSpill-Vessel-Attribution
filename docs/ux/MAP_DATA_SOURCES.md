# MAP_DATA_SOURCES

Every source the map draws from, how it is configured, and what the user sees
when it is absent. Rule: a missing source is reported, never substituted.

## Basemap

| Source | What | Where | Licence / terms | If absent |
|---|---|---|---|---|
| **Natural Earth** 1:10m countries, *India point-of-view* variant (`ne_10m_admin_0_countries_ind`) | country polygons, land borders, coastline, country labels | bundled: `main_system/frontend/public/geo/ne/` (3.0 MB raw, ≈ 0.9 MB gzipped) | public domain | cannot be absent — ships with the app |
| **Natural Earth** 1:50m marine polygons | ocean, sea, bay and gulf names | bundled, same folder | public domain | — |
| **Open Sans Semibold** glyphs (SDF PBF, ranges 0–255, 256–511) | map label text, offline | bundled: `public/fonts/` | Apache-2.0; glyphs from MapLibre demotiles | labels do not render |
| **Satellite imagery** | raster basemap for Satellite mode | `VITE_MAP_SATELLITE_URL`, `VITE_MAP_SATELLITE_ATTRIBUTION`, `VITE_MAP_SATELLITE_MAXZOOM` | the provider's | mode disabled, labelled "not configured"; a request for it shows the vector basemap with the notice *Satellite basemap not configured* |
| Detail raster (optional) | street/coast detail under labels at z ≥ 7 | `VITE_MAP_DETAIL_URL`, `VITE_MAP_DETAIL_ATTRIBUTION` | the provider's | Natural Earth only |

Rebuild the bundle: `python scripts/build_basemap_natural_earth.py`
(downloads the two source files; `--src DIR` to build offline). Simplification
is coverage-preserving (shared borders stay shared), tolerance 0.05°, 3 decimal
places. `SOURCE.json` beside the data records dataset, point of view,
tolerance and build time.

**Why the India point-of-view file.** Natural Earth's default country file
draws the northern boundary of India in a way that is not acceptable for an
Indian government deployment. Natural Earth publishes per-country
point-of-view variants for exactly this; the build script uses `_ind`, and a
unit test asserts India's polygon reaches its full northern extent. Land
borders are derived from the country polygons themselves (country outline
minus coastline) rather than from Natural Earth's separate boundary-lines
file, so no second dataset can contradict the polygons.

### Shipped default provider

`.env.production` / `.env.development` configure **Esri World Imagery**
(`server.arcgisonline.com/.../World_Imagery/...`, keyless), with the
attribution Esri requires, shown in the map's attribution control. This is the
same endpoint the app already used, now configured instead of hardcoded. It is
suitable for evaluation and demonstration; an operational deployment should
point `VITE_MAP_SATELLITE_URL` at imagery the operator is licensed for (or
blank it — the app then says imagery is not configured).

### Removed

- `tile.openstreetmap.org` direct use (against OSM's tile usage policy for a deployed app).
- `public/geo/land.json` and `scripts/build_globe_land.py` (0.2° land raster mask; no countries).
- Attribution is no longer suppressed.

Still present until P3/P7 migrate them: `WorkspaceMap.jsx` and
`CommandMap.jsx` carry their own OpenFreeMap/Esri style URLs.

## Data layers (all from the OceanTrace API — nothing on the map is local or generated)

| Layer | Endpoint | Notes |
|---|---|---|
| SAR raster, z < 10 | `GET /api/runs/{run}/scene_png` | 1024 px quicklook as a georeferenced image; ≈ 20 s cold on the 597 Mpx flagship scene — shown with a loading state |
| SAR raster, z ≥ 10 | `GET /api/tiles/{run}/{z}/{x}/{y}.png` | tile server has no overviews (BACKEND_GAPS G15); 4 parallel requests |
| Slick / detections | `GET /api/layers/{run}/slick`, `/detect` | vector; the mask raster is not used on large scenes (G16) |
| Hindcast cloud, origin | `GET /api/layers/{run}/origin_cloud` | persisted particles per `step_index` |
| Forecast | `GET /api/layers/{run}/forecast` | |
| Run AIS tracks | `GET /api/runs/{run}/vessels_geojson` | clipped to the scene bbox by the server |
| Candidates | `GET /api/layers/{run}/suspects` | |
| Wind / current | `GET /api/runs/{run}/forcing_field` | per run only (G8); never requested concurrently |
| Live AIS | `GET /api/ais/live`, `/api/ais/status` | no coverage over the Bay of Bengal (G9) — reported as such |
| Zones | `GET /api/zones/geojson` | |
| Incidents | `GET /api/incidents` | |
| Scene footprints | `GET /api/scenes/search`, `/api/scenes/local`, `scene_meta.bbox` | |
