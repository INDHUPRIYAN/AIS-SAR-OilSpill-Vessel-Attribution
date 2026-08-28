# OceanTrace — Satellite Scene Service (Sentinel-1 SAR Acquisition)
**Module:** `scene_service/`  
**Role:** Developer 3 — Pavitra (API Developer — Satellite Scene Service)  
**Status:** Production-Ready / Frozen / 100% Tested (118/118 Tests Passing)

---

## 1. Module Overview

The **Satellite Scene Service** provides automated acquisition, catalog discovery, multi-provider fallback retrieval, integrity verification, and local caching of **Sentinel-1 Synthetic Aperture Radar (SAR)** imagery for the OceanTrace marine oil spill detection and vessel attribution pipeline.

Key capabilities:
- **Primary Acquisition:** Copernicus Data Space Ecosystem (CDSE) OData catalog search & Keycloak OAuth2 token management.
- **Secondary Fallback:** NASA Alaska Satellite Facility (ASF) DAAC Vertex REST API search & NASA Earthdata authentication.
- **Local Scene Cache:** Offline-first caching with atomic file operations, schema validation, and SHA-256 integrity checksumming.
- **Strict Fallback Priority:** Cache $\rightarrow$ CDSE $\rightarrow$ ASF $\rightarrow$ Structured Failure Response.
- **Health Telemetry:** Live HTTP latency and availability monitoring (`UP`, `DOWN`, `DEGRADED`, `UNCONFIGURED`).
- **CLI Interface:** Full command-line interface with machine-readable JSON output and standard exit codes.
- **Deterministic Offline/Mock Mode:** 100% offline development, test execution, and CI/CD demonstration with synthetic fixtures without network calls or external credentials.

---

## 2. Architecture

```text
                             [CLI: satellite.cli]
                                      │
                                      ▼
                          [SceneRetrievalChain]
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
         1. Local Cache Check                      Provider Search
         [LocalSceneCache]                     (CDSE / ASF fallback)
                 │                                         │
        ┌────────┴────────┐                                │
     [Hit]              [Miss]                             │
        │                 │                                │
        │                 ▼                                │
        │        2. CDSE Primary                           │
        │        [CDSEAdapter]                             │
        │                 │                                │
        │        ┌────────┴────────┐                       │
        │    [Success]          [Fail]                     │
        │        │                 │                       │
        │        │                 ▼                       │
        │        │        3. ASF Fallback                  │
        │        │        [ASFAdapter]                     │
        │        │                 │                       │
        │        │        ┌────────┴────────┐              │
        │        │    [Success]          [Fail]            │
        │        │        │                 │              │
        │        ▼        ▼                 ▼              │
        │    Save to Cache          4. Structured Failure  │
        │    [LocalSceneCache]      (RetrievalResponse)    │
        │        │                          │              │
        └────────┼──────────────────────────┘              │
                 ▼                                         ▼
        [RetrievalResponse]                       [SceneSearchResult]
```

### Component Roles
1. **`satellite.models`**: Pydantic v2 data models enforcing strict schema validation and ISO-8601 UTC timestamps matching project contracts.
2. **`satellite.cache`**: Filesystem cache managing persistent storage, directory structures, and SHA-256 integrity verification.
3. **`satellite.cdse_adapter`**: Primary acquisition adapter interacting with the European Copernicus Data Space Ecosystem.
4. **`satellite.asf_adapter`**: Secondary fallback adapter querying NASA's Alaska Satellite Facility DAAC.
5. **`satellite.chain`**: Orchestrator executing the priority chain (`CACHE` $\rightarrow$ `CDSE` $\rightarrow$ `ASF` $\rightarrow$ `Failure`).
6. **`satellite.status`**: Operational health probes measuring API latency and connection state for upstream providers.
7. **`satellite.cli`**: Command-line entrypoint for search, retrieval, status inspection, and mock-mode workflows.
8. **`fixtures`**: Deterministic baseline TIFF and metadata fixtures enabling zero-network offline execution.

---

## 3. File Structure

```text
scene_service/
├── README.md                           # Comprehensive module documentation and handover guide
├── requirements.txt                    # Minimal module dependencies (pydantic)
├── fixtures/                           # Deterministic offline demo datasets
│   ├── __init__.py                     # Deterministic TIFF generator & fixture initializers
│   └── demo_scene/
│       ├── scene_meta.json             # Demo Sentinel-1 metadata matching contracts
│       └── scene_sigma0_db.tif         # Deterministic baseline TIFF raster fixture
├── satellite/                          # Core Satellite Scene Service package
│   ├── __init__.py                     # Public API exports
│   ├── asf_adapter.py                  # NASA ASF DAAC Vertex search & download adapter
│   ├── cache.py                        # Local filesystem cache with atomic write & SHA-256 validation
│   ├── cdse_adapter.py                 # Copernicus Data Space Ecosystem (CDSE) adapter
│   ├── chain.py                        # SceneRetrievalChain orchestrator with fallback flow
│   ├── cli.py                          # CLI interface supporting --scene-id, --bbox, --check-status, --mock
│   ├── models.py                       # Pydantic v2 data models (GeoBoundingBox, SceneMetadata, etc.)
│   └── status.py                       # Provider health and latency monitoring probes
└── tests/                              # Comprehensive unit and integration test suite
    ├── __init__.py                     # Test package initializer
    ├── test_asf_adapter.py             # Phase 4 ASF adapter tests (15 tests)
    ├── test_cache.py                   # Phase 2 local cache tests (14 tests)
    ├── test_cdse_adapter.py            # Phase 3 CDSE adapter tests (14 tests)
    ├── test_chain.py                   # Phase 5 fallback chain tests (12 tests)
    ├── test_cli.py                     # Phase 7 CLI tests (14 tests)
    ├── test_demo_fixture.py            # Phase 8 demo fixture tests (14 tests)
    ├── test_end_to_end.py              # Phase 9A end-to-end integration tests (9 tests)
    ├── test_models.py                  # Phase 1 data model tests (12 tests)
    └── test_status.py                  # Phase 6 provider health tests (14 tests)
```

---

## 4. Data Models

All models are defined in [`satellite/models.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/satellite/models.py) using Pydantic v2:

### `GeoBoundingBox`
Represents a geographic bounding box $[W, S, E, N]$:
- `min_lon`: Westernmost longitude ($-180.0 \le \lambda \le 180.0$).
- `min_lat`: Southernmost latitude ($-90.0 \le \phi \le 90.0$).
- `max_lon`: Easternmost longitude ($-180.0 \le \lambda \le 180.0$, $\ge \text{min\_lon}$).
- `max_lat`: Northernmost latitude ($-90.0 \le \phi \le 90.0$, $\ge \text{min\_lat}$).
- Methods: `to_list()`, `from_list(coords)`, `to_wkt()`.

### `SceneMetadata`
Standardized Sentinel-1 SAR scene metadata conforming to [`contracts/mocks/mock_scene.json`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/contracts/mocks/mock_scene.json):
- `scene_id` (`str`): Unique Sentinel-1 product identifier (e.g., `S1A_IW_GRDH_1SDV_20231012T172530`).
- `platform` (`str`): Satellite platform (e.g., `Sentinel-1A`, `Sentinel-1B`).
- `acquisition_time` (`datetime`): UTC timezone-aware acquisition timestamp.
- `bbox` (`GeoBoundingBox` / `List[float]`): Bounding coordinates $[W, S, E, N]$.
- `product_type` (`str`): SAR product type (`GRD`, `SLC`, `OCN`).
- `polarisation` (`str`): Transmit/receive polarization (`VV+VH`, `VV`, `HH`).
- `orbit_direction` (`str`): `ASCENDING` or `DESCENDING`.
- `file_path` (`str`): Local path to downloaded/cached GeoTIFF raster.
- `checksum` (`str`): SHA-256 checksum of the raster.
- `file_size_bytes` (`int`): Exact raster size in bytes.
- `download_url` (`str`): Direct upstream download URL.

### `SceneSearchResult`
Container for multi-scene catalog queries:
- `query_bbox`, `query_start`, `query_end`, `total_count`, `scenes`, `provider`.

### `ProviderHealth`
Standardized operational health report:
- `provider_name` (`str`), `is_available` (`bool`), `status` (`"UP" | "DOWN" | "DEGRADED" | "UNCONFIGURED"`), `latency_ms` (`float | None`), `details` (`dict`).

### `RetrievalResponse`
Top-level response returned by the acquisition service:
- `success` (`bool`): True if acquisition succeeded.
- `scene_id` (`str`): Target scene ID.
- `source_provider` (`str | None`): Provider that satisfied request (`CACHE`, `CDSE`, `ASF`).
- `metadata` (`SceneMetadata | None`): Scene metadata if successful.
- `geotiff_path` (`str | None`): Filesystem path to raster if successful.
- `error_message` (`str | None`): Diagnostic message if failed.

---

## 5. CDSE Adapter (`CDSEAdapter`)

Connects to the **Copernicus Data Space Ecosystem (CDSE)**:
- **Authentication:** Keycloak OAuth2 OpenID Connect (`/auth/realms/CDSE/protocol/openid-connect/token`). Automatically tracks token expiration and refreshes cached bearer tokens.
- **Catalog Search:** OData API (`/odata/v1/Products`) with WKT spatial intersection polygons, temporal bounds, and product type filtering (`GRD`/`SLC`).
- **Streaming Download:** Chunked HTTP streaming download (`$value` endpoint) with progressive SHA-256 calculation and empty-file protection.
- **Mock Mode:** Deterministic mock execution returning standard Sentinel-1 mock scenes without requiring live credentials or internet access.

---

## 6. ASF Adapter (`ASFAdapter`)

Connects to the **NASA Alaska Satellite Facility (ASF) DAAC**:
- **Search API:** Vertex Search REST API (`/services/search/param`) with spatial bounding box, temporal range, and Sentinel-1 dataset parameters.
- **Authentication:** NASA Earthdata Login via HTTP Basic Authorization header.
- **Streaming Download:** Chunked streaming retrieval from ASF DAAC archives with automatic SHA-256 calculation.
- **Mock Mode:** Deterministic mock search and acquisition for offline testing.

---

## 7. Fallback Chain (`SceneRetrievalChain`)

Orchestrates Sentinel-1 SAR acquisition following the strict priority chain:
1. **`LOCAL CACHE` (Offline-First):** Checks [`LocalSceneCache`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/satellite/cache.py). If valid metadata and raster with matching SHA-256 exist on disk, returns immediately (`source_provider="CACHE"`).
2. **`CDSE PRIMARY`:** If cache miss, queries CDSE OData API. If successful, saves scene to `LocalSceneCache` and returns `source_provider="CDSE"`.
3. **`ASF FALLBACK`:** If CDSE fails (timeout, 5xx, auth error), automatically catches exception and queries NASA ASF DAAC. If successful, saves scene to cache and returns `source_provider="ASF"`.
4. **`STRUCTURED FAILURE`:** If all providers fail, returns `RetrievalResponse(success=False, error_message=...)` without raising unhandled exceptions.

---

## 8. Local Scene Cache (`LocalSceneCache`)

Maintains local disk persistence under `<cache_dir>/scenes/<scene_id>/`:
```text
<cache_dir>/scenes/<scene_id>/
├── scene_meta.json          # Serialized SceneMetadata JSON
└── scene_sigma0_db.tif      # Calibrated Sentinel-1 SAR raster file
```

- **Atomic File Writing:** Writes to temporary files (`.tmp`) before atomic rename to prevent corruption.
- **Checksum Validation:** Computes and verifies SHA-256 hash against metadata on every retrieval.
- **Corrupted Cache Handling:** Detects invalid JSON or hash mismatches, rejects invalid entries, and marks them as cache misses.
- **Offline Operation:** 100% standalone filesystem operations.

---

## 9. Provider Health Probes (`satellite/status.py`)

Monitors operational status of upstream APIs via lightweight probes:
- **`check_cdse_health()`**: Probes CDSE OData catalog endpoint (`/odata/v1/Products?$top=1`).
- **`check_asf_health()`**: Probes ASF Vertex Search endpoint (`/services/search/param?...`).
- **`get_api_status()`**: Returns aggregated health reports for both providers.

### Classification Thresholds
- **`UP`**: HTTP 200 response with round-trip latency $\le 3000\,\text{ms}$.
- **`DEGRADED`**: HTTP 200 response with latency $> 3000\,\text{ms}$ or non-fatal $4\text{xx}$ response.
- **`DOWN`**: Endpoint unreachable, connection timeout, network failure, or $5\text{xx}$ server error.
- **`UNCONFIGURED`**: Missing required credentials when credential validation is requested.

---

## 10. Command Line Interface (CLI)

The CLI (`satellite.cli`) provides a unified command-line tool.

### Supported Arguments
- `--scene-id <id>`: Acquire a specific Sentinel-1 scene.
- `--bbox <min_lon,min_lat,max_lon,max_lat>`: Search scenes within a bounding box.
- `--start-time <iso8601>`: Search start timestamp (UTC).
- `--end-time <iso8601>`: Search end timestamp (UTC).
- `--product-type <type>`: Sentinel-1 product type (defaults to `GRD`).
- `--check-status`: Run health checks on CDSE and ASF.
- `--output-dir <path>`: Custom output directory for downloaded rasters.
- `--cache-dir <path>`: Custom cache directory location.
- `--mock`: Run in offline mock mode without remote requests.
- `--help`: Display CLI help documentation.

### Exit Codes
- `0`: Successful execution.
- `1`: Retrieval or search failure.
- `2`: Invalid CLI arguments or malformed input.

### CLI Usage Examples

**1. Retrieve scene in mock mode:**
```bash
python -m satellite.cli --scene-id S1A_IW_GRDH_1SDV_20231012T172530 --mock
```

**2. Check provider health status:**
```bash
python -m satellite.cli --check-status --mock
```

**3. Search scenes by bounding box and time window:**
```bash
python -m satellite.cli \
    --bbox 2.5,51.5,3.2,52.1 \
    --start-time 2023-10-12T00:00:00Z \
    --end-time 2023-10-13T00:00:00Z \
    --mock
```

---

## 11. Mock & Demo Mode

> [!NOTE]
> **Synthetic Fixture Disclosure:** The demonstration raster file located in `fixtures/demo_scene/scene_sigma0_db.tif` is a **SYNTHETIC BASELINE TIFF** generated for testing and demonstration purposes. It is **NOT** genuine Sentinel-1 satellite imagery.

- Located at: `scene_service/fixtures/demo_scene/`
- Contains:
  - `scene_meta.json` (Structured metadata matching project contract)
  - `scene_sigma0_db.tif` (4,206-byte baseline uncompressed TIFF raster)
- Exact SHA-256: `6420a1e0aad8f037a3846ba1bb8502ff696f47aaf647ef211aac833fa019a579`
- Allows developers and CI/CD pipelines to run full end-to-end demonstrations completely offline with zero credentials or remote network access.

---

## 12. Environment Configuration

The service reads configuration from environment variables or a `.env` file:

| Environment Variable | Description |
|---|---|
| `CDSE_USERNAME` | Copernicus Data Space Ecosystem username |
| `CDSE_PASSWORD` | Copernicus Data Space Ecosystem password |
| `ASF_USERNAME` | NASA Earthdata username for ASF DAAC |
| `ASF_PASSWORD` | NASA Earthdata password for ASF DAAC |
| `CACHE_DIR` | Directory for local scene cache (default: `./data/cache/satellite`) |
| `OUTPUT_DIR` | Default destination directory for scene downloads |

*(No credentials are hardcoded or leaked into error logs).*

---

## 13. Output Specification (`RetrievalResponse`)

All scene retrieval operations return a structured `RetrievalResponse` JSON object:

```json
{
  "success": true,
  "scene_id": "S1A_IW_GRDH_1SDV_20231012T172530",
  "source_provider": "CACHE",
  "metadata": {
    "scene_id": "S1A_IW_GRDH_1SDV_20231012T172530",
    "platform": "Sentinel-1A",
    "acquisition_time": "2023-10-12T17:25:30Z",
    "bbox": [2.5, 51.5, 3.2, 52.1],
    "product_type": "GRD",
    "polarisation": "VV+VH",
    "orbit_direction": "DESCENDING",
    "file_path": "./data/cache/satellite/scenes/S1A_IW_GRDH_1SDV_20231012T172530/scene_sigma0_db.tif",
    "checksum": "6420a1e0aad8f037a3846ba1bb8502ff696f47aaf647ef211aac833fa019a579",
    "file_size_bytes": 4206,
    "download_url": "mock://cdse.dataspace.copernicus.eu/demo/S1A_IW_GRDH_1SDV_20231012T172530.tif"
  },
  "geotiff_path": "./data/cache/satellite/scenes/S1A_IW_GRDH_1SDV_20231012T172530/scene_sigma0_db.tif",
  "error_message": null
}
```

---

## 14. Integration with Downstream SAR Detection

As verified during Phase 9B integration boundary inspection:

1. **Prerequisites Provided by Satellite Scene Service:**
   - Validated local raster file path (`geotiff_path`).
   - Scene metadata: `scene_id`, `acquisition_time` (UTC), `bbox`, `platform`, `product_type`, `polarisation`, `orbit_direction`.
   - Data integrity verification: `checksum`, `file_size_bytes`.

2. **Downstream Consumption by Detection Engine:**
   - The downstream detection pipeline (e.g. `main_system/backend/services/detection/detector.py` or `analysis_engines/`) reads `geotiff_path` to perform SAR segmentation, feature extraction, and oil slick classification.
   - The downstream engine propagates `scene_id` and `acquisition_time` to produce the final `contracts/schemas/sar_detection.json` output (`detection_id`, `scene_id`, `geometry_geojson`, `confidence`, `spill_area_sq_km`, `timestamp`).

---

## 15. Testing & Verification

The test suite covers 100% of the Satellite Scene Service with **118 unit and integration tests** executing completely offline:

| Test Suite File | Phase | Tests | Status |
|---|---|---|---|
| [`test_models.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_models.py) | Phase 1 (Data Models) | 12 | **PASS** |
| [`test_cache.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_cache.py) | Phase 2 (Local Cache) | 14 | **PASS** |
| [`test_cdse_adapter.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_cdse_adapter.py) | Phase 3 (CDSE Adapter) | 14 | **PASS** |
| [`test_asf_adapter.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_asf_adapter.py) | Phase 4 (ASF Adapter) | 15 | **PASS** |
| [`test_chain.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_chain.py) | Phase 5 (Fallback Chain) | 12 | **PASS** |
| [`test_status.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_status.py) | Phase 6 (Provider Status) | 14 | **PASS** |
| [`test_cli.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_cli.py) | Phase 7 (CLI Interface) | 14 | **PASS** |
| [`test_demo_fixture.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_demo_fixture.py) | Phase 8 (Demo Fixtures) | 14 | **PASS** |
| [`test_end_to_end.py`](file:///d:/SIH/AIS-SAR-OilSpill-Vessel-Attribution/scene_service/tests/test_end_to_end.py) | Phase 9A (End-to-End Validation) | 9 | **PASS** |
| **Total Test Suite** | **Phases 1–9A** | **118 / 118** | **100% PASS** |

### Running Tests
```bash
# Run complete test suite
python -m unittest discover -s scene_service/tests -v

# Run end-to-end integration tests
python -m unittest scene_service/tests/test_end_to_end.py -v
```

---

## 16. Validation Status

- **Module Boundary:** Strictly preserved (0 modifications outside `scene_service/`).
- **Offline Execution:** Mock mode verified with zero external network access.
- **Integrity Verification:** SHA-256 atomic hashing and cache validation verified.
- **Provider Fallback:** CDSE $\rightarrow$ ASF fallback flow verified under simulated network failures.
- **CLI Interface:** Verified with exit codes `0`, `1`, `2` and schema-compliant JSON.
- **Regression:** Zero regressions across all 118 tests.

---

## 17. Known Limitations

1. **Synthetic Demo Raster:** The demonstration raster (`fixtures/demo_scene/scene_sigma0_db.tif`) is a synthetic baseline TIFF fixture designed for offline testing and must not be represented as real SAR data.
2. **Downstream Integration Mapping:** Producing `contracts/schemas/sar_detection.json` requires a downstream runner/adapter in the main system to execute segmentation algorithms on `geotiff_path`.
3. **External Network Access:** Live retrieval requires valid CDSE or NASA Earthdata credentials in production mode.

---

## 18. Developer Handover Guide

To consume a retrieved Sentinel-1 scene in Python:

```python
from satellite import LocalSceneCache, CDSEAdapter, ASFAdapter, SceneRetrievalChain

# 1. Initialize retrieval chain (supports mock_mode=True for offline execution)
cache = LocalSceneCache(cache_dir="./data/cache/satellite")
cdse = CDSEAdapter(mock_mode=True)
asf = ASFAdapter(mock_mode=True)
chain = SceneRetrievalChain(cdse_adapter=cdse, asf_adapter=asf, cache=cache)

# 2. Retrieve scene by ID (Strict Cache -> CDSE -> ASF fallback)
response = chain.retrieve_scene("S1A_IW_GRDH_1SDV_20231012T172530")

if response.success:
    raster_path = response.geotiff_path
    scene_id = response.metadata.scene_id
    acquisition_time = response.metadata.acquisition_time
    bbox = response.metadata.bbox_list
    print(f"Acquired scene {scene_id} ({response.source_provider}) at {raster_path}")
else:
    print(f"Retrieval failed: {response.error_message}")
```

Downstream detection code should consume `response.geotiff_path`, `response.metadata.scene_id`, and `response.metadata.acquisition_time` to feed detection models and construct the final `sar_detection.json` contract.
