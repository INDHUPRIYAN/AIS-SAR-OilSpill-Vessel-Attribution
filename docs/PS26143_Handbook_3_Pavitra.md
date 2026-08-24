# OceanTrace — Personal Developer Handbook

## Developer 3 of 5 — PAVITRA

**Role:** API Developer — Satellite Scene Service (Sentinel-1 acquisition)

| | |
|---|---|
| Event | SIH 2026 · Problem Statement 26143 (NTRO) |
| Title | Leveraging satellite imagery to determine oil spills at sea with AIS correlation to identify the responsible vessel |
| Category / Theme | Software / Space Technology |
| Project codename | OceanTrace |
| Companion docs | `PS26143_System_Specification` · `PS26143_Team_Split_Handbook` |
| Datasets locked | DARTIS + Trujillo (Zenodo Sentinel-1) |
| Tooling | Claude Code as primary development assistant |

> **How to use this handbook.** This is your single working document. You own the very start of the pipeline: search, download, cache and describe Sentinel-1 SAR scenes, with provider fallback and health reporting. Everything you need is here — the request/response contracts with examples, account setup, a phased build plan, the must-pass tests (including the airplane-mode test), and the handover rules. Your component talks only to external public APIs — you never wait for a teammate.

**Contents:** 1 Project in one page · 2 Your mission · 3 Ground rules · 4 Contracts · 5 Day-0 setup · 6 Build plan · 7 Frozen interfaces · 8 Testing · 9 Pitfalls · 10 Integration & handover

---

## 1. The Project in One Page

### 1.1 What the finished system does

The system takes a Sentinel-1 SAR satellite scene, detects and characterises any oil slick in it, uses ocean-current and wind data to trace the slick backwards to its probable origin point and time (hindcast) and forwards to its future spread (forecast), reconstructs historic AIS vessel traffic around that origin window, filters irrelevant vessels, scores the remaining candidates, and presents a ranked, explainable suspect list on a GIS investigation interface. API Monitoring and Key Management pages make external dependencies observable; every stage has a fallback and the last fallback in every chain is dependency-free.

### 1.2 Full pipeline and ownership

```
Sentinel-1 scene acquisition ............ PAVITRA  ◄◄ you
        ↓  scene_meta.json + calibrated GeoTIFF
Oil-spill detection (2-stage ML) ........ Indhu
        ↓  raw mask
Characterisation (Engine A) ............. Nandha
        ↓  slick.geojson       ◄── currents.nc + wind.nc .. Keerthana
Drift hindcast + forecast (Engine B) .... Nandha
        ↓  origin_cloud.geojson ◄── vessels.parquet ....... Krishnan
Filter + Score + Rank (Engine C) ........ Nandha
        ↓  suspects.json
GIS UI + Monitoring + Integration ....... Indhu
```

### 1.3 Your module highlighted

```
EXTERNAL: Copernicus Data Space (primary) / ASF Vertex (fallback)
        ↓
╔══════════════════════════════════════════╗
║ 1. SATELLITE SCENE SERVICE               ║ ◄◄◄ YOUR MODULE
║    search → download → cache → describe  ║
╚══════════════════════════════════════════╝
        ↓ scene_meta.json + calibrated GeoTIFF
Detection ML (Indhu) → rest of the pipeline → GIS UI (Indhu)
```

## 2. Your Mission

### 2.1 Component owned

The satellite-imagery data source: search, download, cache and describe Sentinel-1 SAR scenes.

- **Primary provider:** Copernicus Data Space Ecosystem (CDSE) — account, OAuth client, token-refresh wrapper (the access token lives ~10 minutes — long downloads fail without refresh), OData search + download.
- **Fallback provider:** ASF Vertex via the `asf_search` Python package (Earthdata login — simple username/password, no OAuth complexity).
- **Guaranteed path:** pre-downloaded scenes in `data/scenes/` — the demo never depends on live download.
- Plus: local scene cache (never re-download), download progress + retry, provider health reporting, and the two demo scenes downloaded and verified.

### 2.2 Where it fits

The very start of the pipeline. Your GeoTIFF is what the detection model reads; your `scene_meta.json` carries the acquisition time and bbox that every later stage (drift window, AIS window) is anchored to. Your `provider_status.json` entries feed Indhu's API Monitoring page.

### 2.3 What you own

Everything from external providers to a cached, described scene on disk: accounts, OAuth + token refresh, OData search, downloads with retry/progress, ASF fallback, the cache, provider status, the two demo scenes, tests and README.

### 2.4 What you do NOT own

SAR calibration science — the dB constants come from `config/normalisation.yaml` (Indhu). Detection (Indhu). Drift/attribution (Nandha). Met-ocean (Keerthana). AIS (Krishnan). UI (Indhu). If raw-GRD calibration proves too heavy, the team pivots to pre-calibrated scenes — flag it early, don't fight it alone.

### 2.5 Why you are never blocked

Your component talks only to external public APIs. Any Sentinel-1 scene over any ocean region works for development.

## 3. Ground Rules (identical for all five developers)

| Rule | Meaning |
|---|---|
| Contract is law | If your output matches the schema in `contracts/`, integration works without touching your code. Changes to a frozen contract require team sign-off. |
| WGS84 everywhere | All vector data in EPSG:4326 (lon/lat). Convert only at ingest boundaries; one assert per boundary. |
| UTC everywhere | All timestamps UTC (`Z` suffix). Beware IST = UTC+05:30 — never let local time leak into data. Scene acquisition time anchors every downstream window. |
| Error taxonomy | Standard classes: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`, plus yours in §4.4. Structured errors, never crashes. |
| Mocks first | `contracts/mocks/` exists from day 1 (Indhu creates). Everyone develops against mocks. |
| No blocking | Every developer builds, runs and tests alone. |
| Freeze rule | Once a component is integrated and green, nobody edits it without Indhu's sign-off. |
| Honesty | Cached data is badged CACHED in the UI; fallbacks are surfaced, never hidden in logs. |

## 4. Contracts — Your Inputs and Outputs

### 4.1 Input — request from the main system (mocked as fixed JSON files during development)

```json
{ "bbox": [80.10, 12.90, 80.55, 13.35],
  "start": "2017-02-01T00:00:00Z",
  "end":   "2017-02-03T00:00:00Z",
  "scene_id": null }
```

`bbox` is `[W, S, E, N]` in WGS84. If `scene_id` is given, fetch that exact scene.

### 4.2 Output — `scene_meta.json` + calibrated GeoTIFF on disk

```json
{ "scene_id": "S1A_IW_GRDH_20170202T0039_DEMO-A",
  "acquired_utc": "2017-02-02T00:39:42Z",
  "bbox": [80.10, 12.90, 80.55, 13.35],
  "crs": "EPSG:4326",
  "db_range": [-35.0, 0.0],
  "file_path":
    "data/scenes/S1A_IW_GRDH_20170202T0039_DEMO-A/scene_sigma0_db.tif",
  "provider_used": "CDSE",
  "source": "real" }
```

The GeoTIFF lives in `data/scenes/<scene_id>/`, calibrated to Sigma0 in dB. Pre-processed (pre-calibrated) scenes are acceptable for the POC; raw-GRD download + calibration is the stretch goal.

### 4.3 Output — `provider_status.json` entries for YOUR providers (CDSE, ASF)

```json
{ "provider": "CDSE", "purpose": "Sentinel-1 scene search + download",
  "status": "WORKING", "last_code": 200, "last_latency_ms": 340,
  "last_success_utc": "2026-08-24T14:02:11Z",
  "last_failure_utc": null, "last_error_class": null,
  "chain": ["CDSE", "ASF", "LocalCache"],
  "active_provider": "CDSE" }
```

One entry per provider, updated on every call (and by the health scheduler). This is exactly what Indhu's Monitoring page renders.

### 4.4 Your error classes

| Class | When | Main-system reaction |
|---|---|---|
| `AUTH_FAILED` | Bad/expired OAuth or Earthdata credentials | Badge; admin fixes key via Key Management |
| `NOT_FOUND` | No scene in the requested bbox/window | UI message; user widens the window |
| `TIMEOUT` | Provider unresponsive | Retry, then fall through the chain |
| `UNAVAILABLE` | Both providers down | Serve cached demo scene, badge "CACHED" |

## 5. Day-0 Setup

### 5.1 Accounts and credentials (do these first — approvals are instant but fiddly)

1. **CDSE:** register at dataspace.copernicus.eu → create an OAuth client (client id + secret). Note: access tokens expire in ~10 minutes; refresh tokens last longer.
2. **NASA Earthdata:** register at urs.earthdata.nasa.gov (for ASF).
3. Store both in `.env` (never in code): `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`, `EARTHDATA_USER`, `EARTHDATA_PASS`. Later these move into Indhu's Key Management page.

### 5.2 Environment

```
pip install requests asf_search rasterio shapely pydantic pytest \
            tenacity tqdm
```

### 5.3 Your folders

```
backend/app/services/satellite/
├── cdse_adapter.py        # OAuth + token refresh + OData
├── asf_adapter.py         # asf_search fallback
├── cache.py               # data/scenes/ cache, checksums
├── chain.py               # CDSE → ASF → LocalCache fallback chain
├── status.py              # provider_status.json writer
└── cli.py                 # fetch-scene
data/scenes/<scene_id>/    # cached scenes (gitignored)
tests/requests/            # fixed request JSONs
```

### 5.4 Mock/test inputs

Fixed request JSONs in `tests/requests/`; one known scene ID with a known file size/checksum as the golden test; a saved OData response JSON for offline unit tests of the search parser.

## 6. Build Plan — Phase by Phase

| Phase | Deliverable |
|---|---|
| 0 | Accounts created; `.env` configured; both APIs pinged successfully |
| 1 | Request/response Pydantic models; mock request files |
| 2 | CDSE OData search working for a known bbox/date |
| 3 | CDSE download with token-refresh wrapper + retry + progress |
| 4 | Local cache (checksum, never re-download, serve offline) |
| 5 | ASF fallback + automatic chain (CDSE → ASF → cache) |
| 6 | `provider_status.json` reporting + health ping |
| 7 | Two demo scenes downloaded, verified, described |
| 8 | Tests, README, handover |

### Phase 2 — Search

☐ OData query: intersects bbox, sensing time in [start, end], `SENTINEL-1`, GRD IW, VV/VH.
☐ Parse product list → pick best scene (coverage of bbox, closest to window centre).
☐ Unit-test the parser against the saved OData response JSON (offline).

### Phase 3 — Download with token refresh

☐ OAuth token endpoint wrapper: fetch access token, auto-refresh when < 60 s remaining or on 401.
☐ Streamed download with progress bar, resume/retry (tenacity), checksum verification.
☐ Simulate token expiry mid-download in a test (short-lived token or forced 401) → download recovers.

### Phase 4 — Cache

☐ Cache key = scene_id. If present with valid checksum: serve without network.
☐ Airplane-mode test: disable network, request cached scene → success, `provider_used: "LocalCache"`.

### Phase 5 — Fallback chain

☐ CDSE failure classes → automatically try ASF (`asf_search` with Earthdata login).
☐ Circuit breaker: after N consecutive failures, skip a provider for a cooldown.
☐ Every attempt recorded in `provider_status.json`; fallback surfaced, never silent.

### Phase 7 — Demo scenes

☐ Scene A (headline): Indian waters, Chennai/Ennore 2017 area. Scene B (proof): Gulf of Mexico or Danish waters (must have real AIS coverage — coordinate scene choice with the team).
☐ Download both, verify they open in rasterio, dB range sane, write `scene_meta.json` for each, commit metadata (scenes themselves stay in gitignored `data/`).
☐ Hand tiles from these scenes to Indhu so he can verify the model performs on them BEFORE the team locks the scenes.

## 7. Frozen Interfaces You Implement

```
fetch-scene --bbox 80.10 12.90 80.55 13.35 \
            --start 2017-02-01T00:00:00Z --end 2017-02-03T00:00:00Z \
            [--scene-id <id>] [--provider cdse|asf|cache]
```

Returns the path to `scene_meta.json` (file + GeoTIFF on disk). The same behaviour is exposed as a Python function the main system calls: request dict in → `scene_meta.json` dict out, or a structured error of §4.4.

## 8. Testing — the must-pass list

☐ Successful search for a known bbox/date returns results.
☐ Successful download; checksum matches the golden value.
☐ Failed request handled; invalid request rejected cleanly.
☐ Invalid credentials → `AUTH_FAILED` (not a stack trace).
☐ Token expiry mid-download recovers automatically.
☐ CDSE down → automatic ASF fallback, recorded in `provider_status.json`.
☐ Missing/invalid response data handled (`BAD_RESPONSE`).
☐ Airplane-mode test: cached scene served with zero network.
☐ Output validates against the `scene_meta.json` schema (UTC times, WGS84 bbox).
☐ Both demo scenes present with metadata.

## 9. Pitfalls and Traps

1. The CDSE access token lives ~10 minutes. A 4 GB scene takes longer than that — without the refresh wrapper, every long download dies at minute 10 with a misleading 401.
2. Never re-download: check the cache first, always. Bandwidth and rate limits are real.
3. Do not fight raw-GRD calibration alone. Pre-calibrated scenes are acceptable for the POC (SNAP is deliberately avoided project-wide); if calibration is heavy, flag it early and pivot.
4. The acquisition time in `scene_meta.json` anchors the drift window and the AIS window — a wrong or non-UTC time silently corrupts the whole investigation. Verify UTC.
5. bbox order is `[W, S, E, N]` lon-lat — easy to flip; assert it at the boundary.
6. Keep credentials out of code, logs and git; they belong in `.env` and later in Key Management.
7. Scene B must be somewhere with real AIS (Gulf of Mexico / Danish waters) — the scene choice is a team decision, not just an imagery decision.

## 10. Integration and Handover

### 10.1 How the main system calls you

```
Main System ── request JSON ──► [SATELLITE SCENE SERVICE]
            ◄── scene_meta.json + GeoTIFF on disk ──
Detection ML (Indhu) reads the GeoTIFF next.
```

On your error classes, the main system falls back to the cached demo scenes and badges the layer "CACHED".

### 10.2 Coordination points

None required for development. At integration: hand demo-scene tiles to Indhu for model verification; confirm the two demo scenes with the team.

### 10.3 Handover checklist (before handing to Indhu)

One-command fetch works · both demo scenes present with metadata · airplane-mode test passes · README with account-setup steps · walkthrough with Indhu. Then the common 10-point checklist applies: one-command run; input schema; output schema; example inputs; example outputs; test results; error behaviour per class; README; run instructions; integration instructions.

### 10.4 Definition of Done

Works independently · mock input works · real input works · output follows the contract · tests exist incl. failure cases · documentation exists · another developer can consume the output using only the contract · you can demonstrate the component without the main system.
