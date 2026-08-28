# Met-Ocean Data Service (Module 4)

**Developer:** Keerthana (Developer 4 of 5)  
**SIH 2026 Problem Statement:** 26143 (NTRO)  
**Project:** OceanTrace — Met-Ocean Environmental Forces Pipeline  

---

## 1. Overview

The **Met-Ocean Data Service** is responsible for automated spatio-temporal data retrieval, coordinate normalization, unit conversion, disk caching, dynamic circuit breaking, and multi-provider fallback orchestration for environmental forces driving oil spill drift simulations:

- **Ocean Currents:** Surface eastward ($u_o$) and northward ($v_o$) current velocities ($\text{m/s}$) $\to$ `currents.nc`
- **Atmospheric Wind:** 10-meter eastward ($u_{10}$) and northward ($v_{10}$) wind velocities ($\text{m/s}$) $\to$ `wind.nc`

Downstream Consumer: **Nandha's Drift Engine** (`analysis_engines/`) for Euler integration and OpenDrift trajectory simulations.

---

## 2. Dynamic Fallback Architecture

Independent fallback chains per data type:

### Ocean Currents Chain
```text
Copernicus Marine Service (CMEMS)
├── Historical (< 2021-01-01) -> GLOBAL_MULTIYEAR_PHY_001_030 (GLORYS12V1)
└── Recent (>= 2021-01-01)   -> GLOBAL_ANALYSIS_FORECAST_PHY_001_024
       ↓ (on failure / timeout)
HYCOM GOFS 3.1 (OPeNDAP / THREDDS)
       ↓ (on failure / timeout)
Static Disk Cache (data/metocean/)
       ↓ (if unavailable)
Structured Degraded Mode
```

### Atmospheric Wind Chain
```text
ECMWF ERA5 Reanalysis (Climate Data Store API)
└── Dataset: reanalysis-era5-single-levels (10m_u_component, 10m_v_component)
       ↓ (on failure / timeout)
Open-Meteo REST API (Speed & Direction -> Vector Decomposition)
       ↓ (on failure / timeout)
Static Disk Cache (data/metocean/)
       ↓ (if unavailable)
Structured Degraded Mode
```

---

## 3. Output Data Contracts

### `currents.nc`
- **Variables:**
  - `uo`: Eastward surface current velocity ($\text{m/s}$)
  - `vo`: Northward surface current velocity ($\text{m/s}$)
- **Dimensions:** `(time, lat, lon)`
- **Coordinate Reference System:** WGS84 / EPSG:4326 (lon $\in [-180, +180]$, lat $\in [-90, +90]$)
- **Time Standard:** ISO-8601 UTC

### `wind.nc`
- **Variables:**
  - `u10`: 10-metre eastward wind component ($\text{m/s}$)
  - `v10`: 10-metre northward wind component ($\text{m/s}$)
- **Dimensions:** `(time, lat, lon)`
- **Coordinate Reference System:** WGS84 / EPSG:4326
- **Time Standard:** ISO-8601 UTC

---

## 4. CLI Usage

```bash
# Fetch both currents and winds for target bounding box and time window
python metocean_service/metocean/cli.py \
  --bbox 79.90 12.70 80.75 13.55 \
  --start 2017-01-29T00:00:00Z \
  --end 2017-02-02T00:00:00Z \
  --what both \
  --provider auto \
  --output-dir data/metocean

# Check provider health telemetry
python metocean_service/metocean/cli.py --health-check
```

---

## 5. Python API Usage

```python
from metocean import fetch_metocean, MetoceanRequest

request = MetoceanRequest(
    bbox=[79.90, 12.70, 80.75, 13.55],
    start="2017-01-29T00:00:00Z",
    end="2017-02-02T00:00:00Z",
    what="both",
    provider="auto",
    output_dir="data/metocean",
)

response = fetch_metocean(request)
print("Currents NetCDF:", response.currents_path)
print("Wind NetCDF:", response.wind_path)
print("Providers Used:", response.providers_used)
```

---

## 6. Running Tests

```bash
python -m unittest discover -s metocean_service/tests -p "test_*.py"
```
All 62 automated unit, contract, fallback, circuit breaker, cache, and demo scene tests pass.
