# OceanTrace — Personal Developer Handbook

## Developer 4 of 5 — KEERTHANA

**Role:** API Developer — Met-Ocean Data Service (currents · wind · weather)

| | |
|---|---|
| Event | SIH 2026 · Problem Statement 26143 (NTRO) |
| Title | Leveraging satellite imagery to determine oil spills at sea with AIS correlation to identify the responsible vessel |
| Category / Theme | Software / Space Technology |
| Project codename | OceanTrace |
| Companion docs | `PS26143_System_Specification` · `PS26143_Team_Split_Handbook` |
| Datasets locked | DARTIS + Trujillo (Zenodo Sentinel-1) |
| Tooling | Claude Code as primary development assistant |

> **How to use this handbook.** This is your single working document. You own the environmental data source: fetch, subset, normalise and cache ocean-current and wind data with provider fallback and health reporting. Everything you need is here — the contracts with examples, account setup (including the two traps that break everyone: the GLORYS-vs-forecast product routing and the ERA5 licence acceptance), a phased build plan, the must-pass tests, and the handover rules. Your component talks only to external APIs — you never wait for a teammate.

**Contents:** 1 Project in one page · 2 Your mission · 3 Ground rules · 4 Contracts · 5 Day-0 setup · 6 Build plan · 7 Frozen interfaces · 8 Testing · 9 Pitfalls · 10 Integration & handover

---

## 1. The Project in One Page

### 1.1 What the finished system does

The system takes a Sentinel-1 SAR satellite scene, detects and characterises any oil slick in it, uses ocean-current and wind data to trace the slick backwards to its probable origin point and time (hindcast) and forwards to its future spread (forecast), reconstructs historic AIS vessel traffic around that origin window, filters irrelevant vessels, scores the remaining candidates, and presents a ranked, explainable suspect list on a GIS investigation interface. API Monitoring and Key Management pages make external dependencies observable; every stage has a fallback and the last fallback in every chain is dependency-free.

### 1.2 Full pipeline and ownership

```
Sentinel-1 scene acquisition ............ Pavitra
        ↓  scene_meta.json + calibrated GeoTIFF
Oil-spill detection (2-stage ML) ........ Indhu
        ↓  raw mask
Characterisation (Engine A) ............. Nandha
        ↓ slick.geojson  ◄── currents.nc + wind.nc . KEERTHANA ◄◄ you
Drift hindcast + forecast (Engine B) .... Nandha
        ↓  origin_cloud.geojson ◄── vessels.parquet ....... Krishnan
Filter + Score + Rank (Engine C) ........ Nandha
        ↓  suspects.json
GIS UI + Monitoring + Integration ....... Indhu
```

### 1.3 Your module highlighted

```
EXTERNAL: Copernicus Marine / ERA5 — HYCOM / Open-Meteo (fallbacks)
        ↓
╔══════════════════════════════════════════╗
║ 4. MET-OCEAN DATA SERVICE                ║ ◄◄◄ YOUR MODULE
║    fetch → subset → normalise → cache    ║
╚══════════════════════════════════════════╝
        ↓ currents.nc + wind.nc
Drift engine (Nandha) → origin + forecast → pipeline → GIS UI (Indhu)
```

## 2. Your Mission

### 2.1 Component owned

The environmental data source, per data type:

| Data | Primary | Fallback 1 | Guaranteed path |
|---|---|---|---|
| Ocean currents | Copernicus Marine toolbox — **GLORYS multiyear** (`GLOBAL_MULTIYEAR_PHY_001_030`) for historical scene dates; `GLOBAL_ANALYSISFORECAST_PHY_001_024` only for recent dates | HYCOM GOFS via OPeNDAP (no key) | Cached NetCDF → zero-current, wind-only drift |
| Wind | ERA5 via the NEW CDS (`cdsapi`, personal access token, dataset licence accepted) | Open-Meteo (no key, instant) | Cached NetCDF → constant wind from scene metadata |
| Waves/weather | CMEMS wave products / Open-Meteo Marine | NOAA GFS/NOMADS | Omitted, documented |

Plus: subsetting to region, unit normalisation, caching in `data/metocean/`, and provider health reporting.

### 2.2 Where it fits

You feed Nandha's drift engine. Without your NetCDFs the hindcast/forecast cannot run on real physics — it degrades to wind-only or constant-wind mode with a UI badge. Your `provider_status.json` entries feed Indhu's API Monitoring page.

### 2.3 What you own

Everything from providers to cached, drift-ready NetCDFs: accounts and tokens, product routing by date, fetch + subset + normalise, fallbacks, cache, offline serving, provider status, tests and README.

### 2.4 What you do NOT own

Drift physics (Nandha). Scene selection (Pavitra/team). Detection (Indhu). AIS (Krishnan). UI (Indhu).

### 2.5 Why you are never blocked

External APIs only. Any ocean bbox works for development; the demo bbox/dates arrive later from the chosen scenes.

## 3. Ground Rules (identical for all five developers)

| Rule | Meaning |
|---|---|
| Contract is law | If your output matches the schema in `contracts/`, integration works without touching your code. Changes to a frozen contract require team sign-off. |
| WGS84 everywhere | All grids on lon/lat (EPSG:4326). Convert only at ingest boundaries; one assert per boundary. |
| UTC everywhere | All timestamps UTC. Beware IST = UTC+05:30 — a local-time request window fetches the wrong hours of data. |
| Error taxonomy | Standard classes: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`, plus yours in §4.4. Structured errors, never crashes. |
| Mocks first | `contracts/mocks/` exists from day 1 (Indhu creates). Everyone develops against mocks. |
| No blocking | Every developer builds, runs and tests alone. |
| Freeze rule | Once a component is integrated and green, nobody edits it without Indhu's sign-off. |
| Honesty | Fallbacks are surfaced in the UI ("Wind: served by Open-Meteo — ERA5 unavailable"), never hidden in logs. |

## 4. Contracts — Your Inputs and Outputs

### 4.1 Input — request from the main system (mocked as fixed JSON files during development)

```json
{ "bbox": [79.90, 12.70, 80.75, 13.55],
  "start": "2017-01-31T00:00:00Z",
  "end":   "2017-02-03T00:00:00Z" }
```

Convention: scene bbox ± margin, time window = scene time ± 48 h (the margin covers the backward drift run).

### 4.2 Outputs — `currents.nc` and `wind.nc`

- `currents.nc`: eastward/northward surface current (agreed names, e.g. `uo`, `vo`) on dimensions `(time, lat, lon)`; units m/s; time as UTC; CF-style attributes.
- `wind.nc`: 10 m wind components (`u10`, `v10`), same grid convention, units m/s.
- Variable names, dimension names and units are documented in your README and agreed ONCE, in writing, with Nandha — his drift engine opens these files with xarray and must not guess.
- Cached copies for BOTH demo scenes live in `data/metocean/<scene_id>/`.

Quick self-check that a produced file is drift-ready:

```python
import xarray as xr
ds = xr.open_dataset("data/metocean/DEMO-A/currents.nc")
assert {"uo", "vo"} <= set(ds.data_vars)
assert {"time", "lat", "lon"} <= set(ds.dims)
```

### 4.3 Output — `provider_status.json` entries for YOUR providers (CMEMS, ERA5, Open-Meteo, HYCOM)

```json
{ "provider": "ERA5", "purpose": "historical 10 m wind for drift",
  "status": "FAILED", "last_code": 403, "last_latency_ms": 512,
  "last_success_utc": "2026-08-24T13:10:02Z",
  "last_failure_utc": "2026-08-24T13:55:41Z",
  "last_error_class": "LICENCE_NOT_ACCEPTED",
  "chain": ["ERA5", "OpenMeteo", "StaticCache"],
  "active_provider": "OpenMeteo" }
```

### 4.4 Your error classes

| Class | When | Main-system reaction |
|---|---|---|
| `AUTH_FAILED` | Bad CMEMS login / CDS token | Badge; admin fixes key |
| `LICENCE_NOT_ACCEPTED` | ERA5 dataset licence not accepted on its download page | Badge with the exact fix ("accept licence on dataset page") |
| `NO_DATA_FOR_PERIOD` | Requested window outside product coverage (usually wrong product for a historical date) | Route to GLORYS / fall back |
| `TIMEOUT` / `UNAVAILABLE` | Provider slow/down | Fall through the chain; serve cache |

If no cache exists either, the drift engine runs wind-only or constant-wind mode with a UI badge — your job is to make that state visible, not to hide it.

## 5. Day-0 Setup

### 5.1 Accounts and credentials (do these first — the licence step trips everyone)

1. **Copernicus Marine (CMEMS):** register at marine.copernicus.eu; `pip install copernicusmarine`; run `copernicusmarine login` once (stores credentials).
2. **CDS / ERA5 (the NEW CDS):** register at cds.climate.copernicus.eu; create `~/.cdsapirc` with `url: https://cds.climate.copernicus.eu/api` and your personal access token; then OPEN the ERA5 dataset page and ACCEPT ITS LICENCE — without this, `cdsapi` requests fail with a misleading error.
3. **Open-Meteo / HYCOM OPeNDAP:** no keys.
4. Store references in `.env`; keys later move into Indhu's Key Management page.

### 5.2 Environment

```
pip install copernicusmarine cdsapi xarray netcdf4 dask requests \
            pydantic pytest tenacity
```

### 5.3 Your folders

```
backend/app/services/metocean/
├── cmems_adapter.py       # GLORYS / analysis-forecast routing + subset
├── era5_adapter.py        # cdsapi retrieval → NetCDF
├── hycom_adapter.py       # OPeNDAP fallback (xarray remote open)
├── openmeteo_adapter.py   # keyless wind fallback → NetCDF
├── chain.py               # primary → fallback → StaticCache
├── cache.py               # data/metocean/ cache
├── status.py              # provider_status.json writer
└── cli.py                 # fetch-metocean
data/metocean/<scene_id>/  # cached NetCDFs (committed for demo scenes)
tests/requests/            # fixed request JSONs
```

### 5.4 Mock/test inputs

Fixed request JSONs; a tiny hand-built NetCDF as the format reference — it doubles as Nandha's mock (coordinate with him once on variable names, then both of you work independently).

## 6. Build Plan — Phase by Phase

| Phase | Deliverable |
|---|---|
| 0 | Accounts + `~/.cdsapirc` + ERA5 licence accepted; CMEMS login works |
| 1 | Tiny hand-built reference NetCDF; variable names agreed in writing with Nandha |
| 2 | CMEMS currents fetch + date-based product routing + regional subset |
| 3 | ERA5 wind fetch → normalised `wind.nc` |
| 4 | Fallbacks: HYCOM (currents), Open-Meteo (wind) |
| 5 | Fallback chain + circuit breaker + `provider_status.json` |
| 6 | Cache + offline serving |
| 7 | One-time cached pulls for BOTH demo scene windows |
| 8 | Tests, README, handover |

### Phase 2 — Currents with date routing (the #1 trap)

☐ Route by request date: historical (e.g. 2017) → `GLOBAL_MULTIYEAR_PHY_001_030` (GLORYS); recent → `GLOBAL_ANALYSISFORECAST_PHY_001_024`. The analysis/forecast product does NOT cover old dates — a 2017 request to it returns "no data" and looks like an outage.
☐ `copernicusmarine subset` with bbox, depth = surface, time window → NetCDF.
☐ Normalise variable/dimension names + units to the agreed convention.

### Phase 3 — ERA5 wind

☐ `cdsapi` retrieval: `reanalysis-era5-single-levels`, `10m_u_component_of_wind` + `10m_v_component_of_wind`, hourly, area subset, NetCDF.
☐ ERA5 queues can be slow — request tight windows, cache aggressively, run pulls early, never live in the demo.
☐ Normalise to `u10`/`v10` on `(time, lat, lon)`.

### Phase 4–5 — Fallbacks and chain

☐ HYCOM via OPeNDAP: open remote dataset in xarray, subset, save.
☐ Open-Meteo: keyless HTTP → build a gridded (or per-point) wind NetCDF good enough for drift.
☐ Chain per data type with circuit breaker; every call logged; active provider recorded; fallback surfaced.

### Phase 6–7 — Cache and demo pulls

☐ Cache key = (product, bbox, window). Serve from cache without network when present.
☐ Once scenes are locked: pull currents + wind for both demo windows, verify with the §4.2 self-check AND by handing one file to Nandha to open, commit to `data/metocean/`.

## 7. Frozen Interfaces You Implement

```
fetch-metocean --bbox 79.90 12.70 80.75 13.55 \
               --start 2017-01-31T00:00:00Z --end 2017-02-03T00:00:00Z \
               [--what currents|wind|both] \
               [--provider auto|cmems|hycom|era5|openmeteo|cache]
```

Returns paths to `currents.nc` / `wind.nc`. Same behaviour exposed as a Python function: request dict in → `{"currents": path, "wind": path, "providers_used": {...}}` out, or a structured error of §4.4.

## 8. Testing — the must-pass list

☐ Real 3-day retrieve for a test bbox from CMEMS and from ERA5 completes.
☐ Both grids open in xarray with the expected variables and units.
☐ Failed request handled; invalid request rejected cleanly.
☐ Wrong credentials → `AUTH_FAILED` and automatic fallback, recorded in `provider_status.json`.
☐ Missing period → `NO_DATA_FOR_PERIOD` (not a crash, not a silent empty file).
☐ Date-range test: a 2017 request routes to GLORYS, not the forecast product.
☐ Cached files served offline (network disabled).
☐ Output files match the agreed variable-name convention (schema check).
☐ Cached NetCDFs exist for both demo scene windows.

## 9. Pitfalls and Traps

1. THE trap: the CMEMS analysis/forecast product has no historical coverage. Every historical scene (2017 headline scene!) must route to GLORYS multiyear. Encode this in code, not in memory.
2. The ERA5 licence must be accepted on the dataset's own download page; until then `cdsapi` fails with an error that looks like a server problem. Also: the NEW CDS URL (`cds.climate.copernicus.eu/api`) — old tutorials point at the retired endpoint.
3. Agree NetCDF variable names with Nandha once, in writing, at Phase 1 — renaming later breaks his engine silently.
4. Request windows in UTC; the drift run needs scene time − 24 h at minimum, so always fetch ± 48 h.
5. ERA5 retrieval queues can take a long time — do the demo pulls days early; the demo reads cache only.
6. Longitude conventions differ (0–360 vs −180–180) between products — normalise to −180–180 and assert.
7. Subset before download (bbox + surface depth + window) — full-globe files are gigabytes for no reason.

## 10. Integration and Handover

### 10.1 How the main system calls you

```
Main System ── request JSON ──► [MET-OCEAN DATA SERVICE]
            ◄── currents.nc + wind.nc paths ──
Drift engine (Nandha) opens them with xarray next.
```

On your error classes, the main system falls back to cache; if no cache exists, the drift engine runs wind-only / constant-wind mode with a UI badge.

### 10.2 Coordination points

Nandha — the single variable-name agreement (Phase 1, in writing; your tiny NetCDF is the shared reference). Everything else flows through files.

### 10.3 Handover checklist (before handing to Indhu)

One-command fetch works for both demo scene windows · cached NetCDFs committed to `data/` · offline test passes · README with account setup (CMEMS login; CDS token + licence acceptance) · variable-name walkthrough with Nandha + handover walkthrough with Indhu. Then the common 10-point checklist applies: one-command run; input schema; output schema; example inputs; example outputs; test results; error behaviour per class; README; run instructions; integration instructions.

### 10.4 Definition of Done

Works independently · mock input works · real input works · output follows the contract · tests exist incl. failure cases · documentation exists · another developer can consume the output using only the contract · you can demonstrate the component without the main system.
