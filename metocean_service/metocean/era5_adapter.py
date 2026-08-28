"""
ECMWF ERA5 Atmospheric Wind Adapter.
Retrieves and subsets ERA5 reanalysis 10m eastward and northward wind components (u10, v10)
from the ECMWF Climate Data Store (CDS) via the CDS API, enforcing spatial/temporal subsetting,
unit normalization to m/s, coordinate normalization, and generating standardized 'wind.nc'.
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
from metocean.utils import ensure_dir, generate_hourly_timestamps, normalize_longitude

logger = logging.getLogger(__name__)

ERA5_DATASET_NAME = "reanalysis-era5-single-levels"
ERA5_WIND_VARIABLES = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
]


def build_cds_request_payload(request: MetoceanRequest) -> Dict[str, Any]:
    """
    Build the JSON payload for ECMWF CDS API 'reanalysis-era5-single-levels'.
    
    Area format for CDS: [North, West, South, East] = [max_lat, min_lon, min_lat, max_lon]
    """
    timestamps = generate_hourly_timestamps(request.start_dt, request.end_dt)
    if not timestamps:
        timestamps = [request.start_dt]

    years = sorted(list(set(t.strftime("%Y") for t in timestamps)))
    months = sorted(list(set(t.strftime("%m") for t in timestamps)))
    days = sorted(list(set(t.strftime("%d") for t in timestamps)))
    times = sorted(list(set(t.strftime("%H:00") for t in timestamps)))

    return {
        "product_type": "reanalysis",
        "format": "netcdf",
        "variable": ERA5_WIND_VARIABLES,
        "year": years,
        "month": months,
        "day": days,
        "time": times,
        "area": request.bbox.to_cds_area(),  # [North, West, South, East]
    }


def normalize_wind_dataset(ds: Any) -> Any:
    """
    Normalize raw ERA5 xarray Dataset to the agreed OceanTrace wind contract:
    - Dimensions: (time, lat, lon)
    - Variables: u10, v10 (m/s)
    - Coordinates: WGS84 EPSG:4326, lon in [-180, 180]
    - CF-compliant metadata attributes
    """
    # 1. Coordinate renaming: latitude -> lat, longitude -> lon, valid_time -> time
    rename_dict = {}
    coords_keys = set(ds.coords.keys()) if hasattr(ds, "coords") else set()
    dims_keys = set(ds.dims.keys()) if hasattr(ds, "dims") else set()
    all_keys = coords_keys | dims_keys

    if "latitude" in all_keys and "lat" not in all_keys:
        rename_dict["latitude"] = "lat"
    if "longitude" in all_keys and "lon" not in all_keys:
        rename_dict["longitude"] = "lon"
    if "valid_time" in all_keys and "time" not in all_keys:
        rename_dict["valid_time"] = "time"

    if rename_dict and hasattr(ds, "rename"):
        ds = ds.rename(rename_dict)

    # 2. Variable mapping: map ERA5 internal names to u10 and v10
    var_names = set(ds.data_vars.keys()) if hasattr(ds, "data_vars") else set()
    var_rename = {}
    if "u10" not in var_names:
        for u_alias in ["10m_u_component_of_wind", "var165", "u", "eastward_wind"]:
            if u_alias in var_names:
                var_rename[u_alias] = "u10"
                break

    if "v10" not in var_names:
        for v_alias in ["10m_v_component_of_wind", "var166", "v", "northward_wind"]:
            if v_alias in var_names:
                var_rename[v_alias] = "v10"
                break

    if var_rename and hasattr(ds, "rename"):
        ds = ds.rename(var_rename)

    var_names = set(ds.data_vars.keys()) if hasattr(ds, "data_vars") else set()
    if "u10" not in var_names or "v10" not in var_names:
        raise BadResponseError(
            f"ERA5 response missing required wind variables 'u10', 'v10'. Found: {list(var_names)}",
            provider="era5"
        )

    # Drop any extra variables
    drop_vars = [v for v in ds.data_vars if v not in ("u10", "v10")]
    if drop_vars and hasattr(ds, "drop_vars"):
        ds = ds.drop_vars(drop_vars)

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
            logger.debug("Longitude normalization notice for ERA5: %s", e)

    # 4. Sort dimensions: ensure ascending lat and time
    if hasattr(ds, "sortby"):
        if "lat" in ds.coords:
            ds = ds.sortby("lat")
        if "time" in ds.coords:
            ds = ds.sortby("time")

    # 5. Metadata standardization (CF-1.8 Conventions)
    if hasattr(ds, "__getitem__"):
        if "u10" in ds.data_vars:
            ds["u10"].attrs = {
                "long_name": "10 metre U wind component",
                "standard_name": "eastward_wind",
                "units": "m/s",
                "valid_min": -100.0,
                "valid_max": 100.0,
            }
        if "v10" in ds.data_vars:
            ds["v10"].attrs = {
                "long_name": "10 metre V wind component",
                "standard_name": "northward_wind",
                "units": "m/s",
                "valid_min": -100.0,
                "valid_max": 100.0,
            }
        if "lat" in ds.coords:
            ds["lat"].attrs = {
                "long_name": "latitude",
                "standard_name": "latitude",
                "units": "degrees_north",
                "axis": "Y",
            }
        if "lon" in ds.coords:
            ds["lon"].attrs = {
                "long_name": "longitude",
                "standard_name": "longitude",
                "units": "degrees_east",
                "axis": "X",
            }
        if "time" in ds.coords:
            ds["time"].attrs = {
                "long_name": "time",
                "standard_name": "time",
                "axis": "T",
            }

    if hasattr(ds, "attrs"):
        ds.attrs = {
            "title": "OceanTrace Normalized 10m Atmospheric Winds",
            "provider": "ECMWF ERA5 Reanalysis (CDS API)",
            "crs": "EPSG:4326 / WGS84",
            "conventions": "CF-1.8",
            "history": f"Normalized at {datetime.now(timezone.utc).isoformat()}",
        }

    return ds


def validate_wind_dataset(ds: Any) -> None:
    """
    Validate that an xarray Dataset strictly conforms to the OceanTrace wind contract.
    """
    if hasattr(ds, "data_vars"):
        required_vars = {"u10", "v10"}
        found_vars = set(ds.data_vars.keys())
        if not required_vars.issubset(found_vars):
            raise BadResponseError(
                f"NetCDF wind contract violation: missing variables {required_vars - found_vars}",
                provider="era5"
            )

    if hasattr(ds, "dims"):
        required_dims = {"time", "lat", "lon"}
        found_dims = set(ds.dims.keys())
        if not required_dims.issubset(found_dims):
            raise BadResponseError(
                f"NetCDF wind contract violation: missing dimensions {required_dims - found_dims}",
                provider="era5"
            )

    # Unit checks
    if hasattr(ds, "__getitem__"):
        for v in ["u10", "v10"]:
            if v in ds.data_vars and hasattr(ds[v], "attrs"):
                unit = ds[v].attrs.get("units", "")
                if unit not in ["m/s", "m s-1", "meter/sec", "meters/second"]:
                    logger.warning("ERA5 variable %s unit '%s' expected to be 'm/s'", v, unit)


class ERA5Adapter:
    """
    Adapter for ECMWF ERA5 Atmospheric Reanalysis Winds.
    Connects to the Climate Data Store (CDS) via cdsapi, downloads 10m wind fields,
    normalizes to standard schema (u10, v10 in m/s), and outputs 'wind.nc'.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        key: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.url = url or os.getenv("CDSAPI_URL")
        self.key = key or os.getenv("CDSAPI_KEY")
        self.client = client  # Injected client for unit testing/mocking

    def _get_client(self) -> Any:
        """Initialize or return the cdsapi Client."""
        if self.client is not None:
            return self.client

        try:
            import cdsapi
            if self.url and self.key:
                return cdsapi.Client(url=self.url, key=self.key, quiet=True)
            return cdsapi.Client(quiet=True)
        except ImportError:
            raise UnavailableError(
                "cdsapi library is not installed. Please install cdsapi to query live ERA5 wind data.",
                provider="era5"
            )
        except Exception as exc:
            self._map_cds_exception(exc)

    def fetch_data(
        self,
        request: Union[MetoceanRequest, Dict[str, Any]],
        output_path: Optional[str] = None,
    ) -> str:
        """
        Fetch atmospheric winds from ERA5 for the requested bbox and time window.

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
                    provider=request.get("provider", "era5"),
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

        # 2. Build CDS request payload
        payload = build_cds_request_payload(request)
        logger.info("ERA5 Querying dataset '%s' for area %s", ERA5_DATASET_NAME, payload["area"])

        # 3. Retrieve dataset from CDS
        ds = self._query_cds(payload, target_file)

        # 4. Normalize dataset
        norm_ds = normalize_wind_dataset(ds)

        # 5. Validate NetCDF schema contract
        validate_wind_dataset(norm_ds)

        # 6. Save to NetCDF
        try:
            norm_ds.to_netcdf(str(target_file), format="NETCDF4", engine="netcdf4")
            logger.info("Successfully generated wind.nc at %s", target_file.resolve())
        except TypeError:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write wind.nc to disk: {inner_exc}", provider="era5")
        except Exception as exc:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write wind.nc to disk: {exc}; {inner_exc}", provider="era5")

        return str(target_file.resolve())

    def _query_cds(self, payload: Dict[str, Any], target_file: Path) -> Any:
        """Internal helper to retrieve CDS dataset."""
        client = self._get_client()

        # Check retrieve method
        if hasattr(client, "retrieve") and callable(getattr(client, "retrieve")):
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
                    tmp_path = tmp.name

                result = client.retrieve(ERA5_DATASET_NAME, payload, tmp_path)
                if hasattr(result, "data_vars"):
                    return result

                if XARRAY_AVAILABLE and xr is not None:
                    ds = xr.open_dataset(tmp_path)
                    return ds
                else:
                    return result
            except Exception as exc:
                self._map_cds_exception(exc)

        # Alternative open_dataset pattern
        if hasattr(client, "open_dataset") and callable(getattr(client, "open_dataset")):
            try:
                return client.open_dataset(ERA5_DATASET_NAME, payload)
            except Exception as exc:
                self._map_cds_exception(exc)

        raise UnavailableError(
            "CDS API client does not expose 'retrieve' or 'open_dataset' interface.",
            provider="era5"
        )

    def _map_cds_exception(self, exc: Exception) -> None:
        """Map CDS API exceptions to structured MetoceanError classes."""
        err_msg = str(exc)
        err_lower = err_msg.lower()

        if any(w in err_lower for w in ["unauthorized", "auth", "invalid key", "forbidden", "401", "403", "missing key", ".cdsapirc"]):
            raise AuthFailedError(f"CDS API Authentication failed: {err_msg}", provider="era5", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["licence", "license", "terms", "agreement", "required to accept"]):
            raise LicenceNotAcceptedError(f"CDS API Licence/Terms not accepted: {err_msg}", provider="era5", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["not found", "no data", "empty result", "out of range"]):
            raise NoDataForPeriodError(f"CDS API has no data for the requested period: {err_msg}", provider="era5", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["timeout", "timed out", "queued too long", "queue timeout"]):
            raise TimeoutError(f"CDS API request timed out: {err_msg}", provider="era5", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["rate limit", "too many requests", "429", "quota", "user queue full"]):
            raise RateLimitedError(f"CDS API rate limit or queue capacity reached: {err_msg}", provider="era5", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["service unavailable", "503", "502", "500", "gateway", "down"]):
            raise UnavailableError(f"CDS API service unavailable: {err_msg}", provider="era5", details={"raw_error": err_msg})
        else:
            raise BadResponseError(f"CDS API query failed with error: {err_msg}", provider="era5", details={"raw_error": err_msg})
