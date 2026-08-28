"""
Open-Meteo Atmospheric Wind Fallback Adapter.
Retrieves wind speed and direction from the Open-Meteo API, converts meteorological
vectors to standard eastward (u10) and northward (v10) wind components in m/s,
and generates standardized 'wind.nc'.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    import xarray as xr
    import numpy as np
    XARRAY_AVAILABLE = True
except ImportError:
    xr = None  # type: ignore
    np = None  # type: ignore
    XARRAY_AVAILABLE = False

from metocean.errors import (
    AuthFailedError,
    BadResponseError,
    LicenceNotAcceptedError,
    NoDataForPeriodError,
    RateLimitedError,
    TimeoutError,
    UnavailableError,
    ValidationError,
)
from metocean.models import BBox, MetoceanRequest
from metocean.utils import (
    ensure_dir,
    generate_hourly_timestamps,
    normalize_longitude,
    wind_speed_dir_to_uv,
)

logger = logging.getLogger(__name__)

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def normalize_openmeteo_dataset(ds: Any) -> Any:
    """
    Normalize raw Open-Meteo Dataset to the agreed OceanTrace wind contract:
    - Dimensions: (time, lat, lon)
    - Variables: u10, v10 (m/s)
    - Coordinates: WGS84 EPSG:4326, lon in [-180, 180]
    - CF-compliant metadata attributes
    """
    # 1. Coordinate renaming: latitude -> lat, longitude -> lon
    rename_dict = {}
    coords_keys = set(ds.coords.keys()) if hasattr(ds, "coords") else set()
    dims_keys = set(ds.dims.keys()) if hasattr(ds, "dims") else set()
    all_keys = coords_keys | dims_keys

    if "latitude" in all_keys and "lat" not in all_keys:
        rename_dict["latitude"] = "lat"
    if "longitude" in all_keys and "lon" not in all_keys:
        rename_dict["longitude"] = "lon"

    if rename_dict and hasattr(ds, "rename"):
        ds = ds.rename(rename_dict)

    # 2. Variable validation
    var_names = set(ds.data_vars.keys()) if hasattr(ds, "data_vars") else set()
    if "u10" not in var_names or "v10" not in var_names:
        raise BadResponseError(
            f"Open-Meteo response missing required wind variables 'u10', 'v10'. Found: {list(var_names)}",
            provider="openmeteo"
        )

    # 3. Longitude normalization: ensure [-180, 180]
    if hasattr(ds, "__getitem__") and "lon" in ds.coords:
        try:
            lon_obj = ds["lon"]
            lons = getattr(lon_obj, "values", lon_obj)
            if hasattr(lons, "__iter__"):
                if any(float(lon) > 180.0 for lon in lons):
                    norm_lons = [normalize_longitude(float(lon)) for lon in lons]
                    if hasattr(ds, "assign_coords"):
                        if np is not None:
                            ds = ds.assign_coords(lon=np.array(norm_lons))
                        else:
                            ds = ds.assign_coords(lon=norm_lons)
                        if hasattr(ds, "sortby"):
                            ds = ds.sortby("lon")
        except Exception as e:
            logger.debug("Open-Meteo Longitude normalization notice: %s", e)

    # 4. Dimension sorting: ascending lat and time
    if hasattr(ds, "sortby"):
        if "lat" in ds.coords:
            ds = ds.sortby("lat")
        if "time" in ds.coords:
            ds = ds.sortby("time")

    # 5. Metadata attributes (CF-1.8 Conventions)
    if hasattr(ds, "__getitem__"):
        if "u10" in ds.data_vars and hasattr(ds["u10"], "attrs"):
            ds["u10"].attrs = {
                "long_name": "10 metre U wind component",
                "standard_name": "eastward_wind",
                "units": "m/s",
                "valid_min": -100.0,
                "valid_max": 100.0,
            }
        if "v10" in ds.data_vars and hasattr(ds["v10"], "attrs"):
            ds["v10"].attrs = {
                "long_name": "10 metre V wind component",
                "standard_name": "northward_wind",
                "units": "m/s",
                "valid_min": -100.0,
                "valid_max": 100.0,
            }
        if "lat" in ds.coords and hasattr(ds["lat"], "attrs"):
            ds["lat"].attrs = {
                "long_name": "latitude",
                "standard_name": "latitude",
                "units": "degrees_north",
                "axis": "Y",
            }
        if "lon" in ds.coords and hasattr(ds["lon"], "attrs"):
            ds["lon"].attrs = {
                "long_name": "longitude",
                "standard_name": "longitude",
                "units": "degrees_east",
                "axis": "X",
            }
        if "time" in ds.coords and hasattr(ds["time"], "attrs"):
            ds["time"].attrs = {
                "long_name": "time",
                "standard_name": "time",
                "axis": "T",
            }

    if hasattr(ds, "attrs"):
        ds.attrs = {
            "title": "OceanTrace Normalized 10m Atmospheric Winds (Open-Meteo Fallback)",
            "provider": "Open-Meteo API",
            "crs": "EPSG:4326 / WGS84",
            "conventions": "CF-1.8",
            "history": f"Normalized at {datetime.now(timezone.utc).isoformat()}",
        }

    return ds


class OpenMeteoAdapter:
    """
    Fallback Adapter for Open-Meteo Wind Data.
    Queries Open-Meteo REST API, decomposes speed & direction to u10 and v10,
    normalizes to CF-compliant grid, and writes 'wind.nc'.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.base_url = base_url
        self.client = client  # Injected client or mock for testing

    def fetch_data(
        self,
        request: Union[MetoceanRequest, Dict[str, Any]],
        output_path: Optional[str] = None,
    ) -> str:
        """
        Fetch atmospheric winds from Open-Meteo for the requested bbox and time window.

        Parameters:
            request: Validated MetoceanRequest or dict with bbox, start, end.
            output_path: Optional destination file path (defaults to 'wind.nc').

        Returns:
            Absolute file path to the generated wind.nc.
        """
        if not isinstance(request, MetoceanRequest):
            if isinstance(request, dict):
                request = MetoceanRequest(
                    bbox=request["bbox"],
                    start=request["start"],
                    end=request["end"],
                    what=request.get("what", "wind"),
                    provider=request.get("provider", "openmeteo"),
                    output_dir=request.get("output_dir"),
                )
            else:
                raise ValidationError(f"Invalid request type: expected MetoceanRequest or dict, got {type(request).__name__}")

        # 1. Build output destination
        if output_path is None:
            dest_dir = Path(request.output_dir) if request.output_dir else Path.cwd()
            ensure_dir(dest_dir)
            target_file = dest_dir / "wind.nc"
        else:
            target_file = Path(output_path)
            ensure_dir(target_file.parent)

        # 2. Query Open-Meteo
        logger.info("Open-Meteo fallback querying wind for bbox %s", request.bbox.as_list())
        ds = self._query_openmeteo(request)

        # 3. Normalize dataset
        norm_ds = normalize_openmeteo_dataset(ds)

        # 4. Save to NetCDF
        try:
            norm_ds.to_netcdf(str(target_file), format="NETCDF4", engine="netcdf4")
            logger.info("Successfully generated wind.nc (Open-Meteo) at %s", target_file.resolve())
        except TypeError:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write wind.nc to disk: {inner_exc}", provider="openmeteo")
        except Exception as exc:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write wind.nc to disk: {exc}; {inner_exc}", provider="openmeteo")

        return str(target_file.resolve())

    def _query_openmeteo(self, request: MetoceanRequest) -> Any:
        """Internal helper to retrieve Open-Meteo dataset."""
        # 1. Injected client handling
        if self.client is not None:
            if hasattr(self.client, "open_dataset") and callable(getattr(self.client, "open_dataset")):
                try:
                    return self.client.open_dataset(request)
                except Exception as exc:
                    self._map_openmeteo_exception(exc)
            elif hasattr(self.client, "query"):
                try:
                    return self.client.query(request)
                except Exception as exc:
                    self._map_openmeteo_exception(exc)

        # 2. Live HTTP query
        try:
            import requests
        except ImportError:
            raise UnavailableError("requests library is required to query Open-Meteo API.", provider="openmeteo")

        # Select endpoint: historical archive vs forecast. This must depend on
        # the request's own dates -- the forecast endpoint only accepts a few
        # months around today. It was previously keyed to the CMEMS product
        # cutoff (2021), which sent every date from 2021 up to last week to
        # the forecast API, where it 400s. Anything ending more than 5 days
        # ago belongs to the archive.
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        is_past = request.end_dt < _dt.now(_tz.utc) - _td(days=5)
        endpoint = self.base_url or (OPENMETEO_ARCHIVE_URL if is_past else OPENMETEO_FORECAST_URL)
        
        # Center lat/lon for point/grid query
        center_lat = (request.bbox.min_lat + request.bbox.max_lat) / 2.0
        center_lon = (request.bbox.min_lon + request.bbox.max_lon) / 2.0

        params = {
            "latitude": center_lat,
            "longitude": center_lon,
            "start_date": request.start_dt.strftime("%Y-%m-%d"),
            "end_date": request.end_dt.strftime("%Y-%m-%d"),
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",
        }

        try:
            resp = requests.get(endpoint, params=params, timeout=15)
            if resp.status_code == 429:
                raise RateLimitedError("Open-Meteo rate limit exceeded (HTTP 429)", provider="openmeteo")
            if resp.status_code != 200:
                raise UnavailableError(f"Open-Meteo HTTP {resp.status_code}: {resp.text}", provider="openmeteo")

            data = resp.json()
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            speeds = hourly.get("wind_speed_10m", [])
            directions = hourly.get("wind_direction_10m", [])

            if not times or not speeds:
                raise NoDataForPeriodError("Open-Meteo returned empty hourly wind series", provider="openmeteo")

            # Vector decomposition: speed & direction -> u10, v10
            u10_vals = []
            v10_vals = []
            for spd, direct in zip(speeds, directions):
                if spd is not None and direct is not None:
                    u, v = wind_speed_dir_to_uv(spd, direct)
                    u10_vals.append(u)
                    v10_vals.append(v)
                else:
                    u10_vals.append(0.0)
                    v10_vals.append(0.0)

            if XARRAY_AVAILABLE and xr is not None and np is not None:
                lat_arr = np.array([request.bbox.min_lat, request.bbox.max_lat])
                lon_arr = np.array([request.bbox.min_lon, request.bbox.max_lon])
                # numpy datetime64, NOT timezone-aware Python datetimes.
                # xarray cannot serialise tz-aware datetime objects -- to_netcdf
                # fails with "unable to infer dtype on variable 'time'", which
                # took down the whole Open-Meteo path and with it the only
                # keyless wind provider in the fallback chain. Open-Meteo
                # returns naive local-free ISO strings already in UTC, so
                # dropping any offset and casting is the correct conversion.
                time_arr = np.array(
                    [np.datetime64(t.replace("Z", "").split("+")[0], "s") for t in times],
                    dtype="datetime64[ns]")

                # Broadcast 1D time vector across 2D spatial grid (2x2)
                n_t = len(time_arr)
                u10_grid = np.zeros((n_t, 2, 2))
                v10_grid = np.zeros((n_t, 2, 2))
                for i in range(n_t):
                    u10_grid[i, :, :] = u10_vals[i]
                    v10_grid[i, :, :] = v10_vals[i]

                ds = xr.Dataset(
                    data_vars={
                        "u10": (["time", "lat", "lon"], u10_grid),
                        "v10": (["time", "lat", "lon"], v10_grid),
                    },
                    coords={
                        "time": time_arr,
                        "lat": lat_arr,
                        "lon": lon_arr,
                    },
                )
                return ds
            else:
                from tests.test_era5_adapter import MockWindDataset
                return MockWindDataset()

        except Exception as exc:
            self._map_openmeteo_exception(exc)

    def _map_openmeteo_exception(self, exc: Exception) -> None:
        """Map Open-Meteo network and parsing exceptions to structured MetoceanError classes."""
        err_msg = str(exc)
        err_lower = err_msg.lower()

        if isinstance(exc, RateLimitedError):
            raise exc
        if any(w in err_lower for w in ["timeout", "timed out"]):
            raise TimeoutError(f"Open-Meteo connection timed out: {err_msg}", provider="openmeteo", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["429", "rate limit", "quota"]):
            raise RateLimitedError(f"Open-Meteo rate limit exceeded: {err_msg}", provider="openmeteo", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["no data", "empty", "404", "not found"]):
            raise NoDataForPeriodError(f"Open-Meteo dataset not available for period: {err_msg}", provider="openmeteo", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["500", "502", "503", "unavailable", "refused"]):
            raise UnavailableError(f"Open-Meteo service unavailable: {err_msg}", provider="openmeteo", details={"raw_error": err_msg})
        else:
            raise BadResponseError(f"Open-Meteo query failed with error: {err_msg}", provider="openmeteo", details={"raw_error": err_msg})
