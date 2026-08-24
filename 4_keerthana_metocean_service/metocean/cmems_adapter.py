"""
Copernicus Marine Service (CMEMS) Ocean Currents Adapter.
Retrieves and subsets GLORYS12V1 Multiyear and Analysis/Forecast surface currents (uo, vo),
enforcing date-based routing, spatial/temporal subsetting, coordinate normalization,
and generating standardized 'currents.nc'.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

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
from metocean.utils import ensure_dir, normalize_longitude

logger = logging.getLogger(__name__)

# CMEMS Dataset Routing Definitions
HISTORICAL_CUTOFF_DATE = datetime(2021, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

CMEMS_HISTORICAL_PRODUCT = "GLOBAL_MULTIYEAR_PHY_001_030"
CMEMS_HISTORICAL_DATASET = "cmems_mod_glo_phy_my_0.083deg_P1D-m"

CMEMS_RECENT_PRODUCT = "GLOBAL_ANALYSIS_FORECAST_PHY_001_024"
CMEMS_RECENT_DATASET = "cmems_mod_glo_phy_anfc_0.083deg_P1D-m"


def select_cmems_product(request_date: Union[datetime, str]) -> Dict[str, str]:
    """
    Select the correct CMEMS product and dataset ID based on the target date.
    
    Routing Rule:
    - Dates prior to 2021-01-01 (Historical archive, e.g. 2017 Chennai scene)
      -> GLOBAL_MULTIYEAR_PHY_001_030 (GLORYS12V1 Reanalysis)
    - Dates on/after 2021-01-01 (Recent / Near-Real-Time / Forecast)
      -> GLOBAL_ANALYSIS_FORECAST_PHY_001_024 (Analysis & Forecast)
    """
    if isinstance(request_date, str):
        from metocean.models import parse_iso8601_utc
        dt = parse_iso8601_utc(request_date)
    elif isinstance(request_date, datetime):
        dt = request_date.astimezone(timezone.utc) if request_date.tzinfo else request_date.replace(tzinfo=timezone.utc)
    else:
        raise ValidationError(f"Invalid date type for CMEMS product selection: {type(request_date).__name__}")

    if dt < HISTORICAL_CUTOFF_DATE:
        return {
            "product_id": CMEMS_HISTORICAL_PRODUCT,
            "dataset_id": CMEMS_HISTORICAL_DATASET,
            "type": "historical",
            "description": "GLORYS12V1 Multiyear Ocean Physical Reanalysis",
        }
    else:
        return {
            "product_id": CMEMS_RECENT_PRODUCT,
            "dataset_id": CMEMS_RECENT_DATASET,
            "type": "recent",
            "description": "Global Ocean Physics Analysis and Forecast",
        }


def normalize_currents_dataset(ds: Any) -> Any:
    """
    Normalize raw CMEMS Dataset to the agreed OceanTrace currents contract:
    - Dimensions: (time, lat, lon)
    - Variables: uo, vo (m/s)
    - Surface layer only (depth top slice)
    - Coordinates: WGS84 EPSG:4326, lon in [-180, 180]
    - CF-compliant metadata attributes
    """
    # 1. Rename coordinate aliases to standard lat, lon, time
    rename_dict = {}
    coords_keys = set(ds.coords.keys()) if hasattr(ds, "coords") else set()
    if "latitude" in coords_keys and "lat" not in coords_keys:
        rename_dict["latitude"] = "lat"
    if "longitude" in coords_keys and "lon" not in coords_keys:
        rename_dict["longitude"] = "lon"

    if rename_dict and hasattr(ds, "rename"):
        ds = ds.rename(rename_dict)

    # 2. Extract surface current layer if depth dimension exists
    dims_keys = set(ds.dims.keys()) if hasattr(ds, "dims") else set()
    for depth_dim in ["depth", "deptho", "depth_layer"]:
        if depth_dim in dims_keys and hasattr(ds, "isel"):
            ds = ds.isel({depth_dim: 0})
            if hasattr(ds, "squeeze"):
                ds = ds.squeeze(drop=True)

    # 3. Verify and extract required variables uo and vo
    var_names = set(ds.data_vars.keys()) if hasattr(ds, "data_vars") else set()
    if "uo" not in var_names or "vo" not in var_names:
        # Check alternative naming
        alt_u = [k for k in var_names if k in ["u", "eastward_velocity", "water_u"]]
        alt_v = [k for k in var_names if k in ["v", "northward_velocity", "water_v"]]
        if alt_u and alt_v and hasattr(ds, "rename"):
            ds = ds.rename({alt_u[0]: "uo", alt_v[0]: "vo"})
        else:
            raise BadResponseError(
                f"CMEMS response missing required velocity variables 'uo', 'vo'. Found: {list(var_names)}",
                provider="cmems"
            )

    # Keep only uo and vo
    drop_vars = [v for v in ds.data_vars if v not in ("uo", "vo")]
    if drop_vars and hasattr(ds, "drop_vars"):
        ds = ds.drop_vars(drop_vars)

    # 4. Longitude normalization: ensure [-180, 180]
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
            logger.debug("Longitude normalization notice: %s", e)

    # 5. Coordinate sorting: ensure ascending lat and time
    if hasattr(ds, "sortby"):
        if "lat" in ds.coords:
            ds = ds.sortby("lat")
        if "time" in ds.coords:
            ds = ds.sortby("time")

    # 6. Standardize metadata attributes (CF Conventions)
    if hasattr(ds, "__getitem__"):
        if "uo" in ds.data_vars:
            ds["uo"].attrs = {
                "long_name": "Eastward surface current velocity",
                "standard_name": "eastward_sea_water_velocity",
                "units": "m/s",
                "valid_min": -10.0,
                "valid_max": 10.0,
            }
        if "vo" in ds.data_vars:
            ds["vo"].attrs = {
                "long_name": "Northward surface current velocity",
                "standard_name": "northward_sea_water_velocity",
                "units": "m/s",
                "valid_min": -10.0,
                "valid_max": 10.0,
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
            "title": "OceanTrace Normalized Surface Currents",
            "provider": "Copernicus Marine Service (CMEMS)",
            "crs": "EPSG:4326 / WGS84",
            "conventions": "CF-1.8",
            "history": f"Normalized at {datetime.now(timezone.utc).isoformat()}",
        }

    return ds


def validate_currents_dataset(ds: Any) -> None:
    """
    Validate that a Dataset strictly conforms to the OceanTrace currents contract.
    """
    if hasattr(ds, "data_vars"):
        required_vars = {"uo", "vo"}
        found_vars = set(ds.data_vars.keys())
        if not required_vars.issubset(found_vars):
            raise BadResponseError(
                f"NetCDF contract violation: missing variables {required_vars - found_vars}",
                provider="cmems"
            )

    if hasattr(ds, "dims"):
        required_dims = {"time", "lat", "lon"}
        found_dims = set(ds.dims.keys())
        if not required_dims.issubset(found_dims):
            raise BadResponseError(
                f"NetCDF contract violation: missing dimensions {required_dims - found_dims}",
                provider="cmems"
            )

    # Unit checks
    if hasattr(ds, "__getitem__"):
        for v in ["uo", "vo"]:
            if v in ds.data_vars and hasattr(ds[v], "attrs"):
                unit = ds[v].attrs.get("units", "")
                if unit not in ["m/s", "m s-1", "meter/sec", "meters/second"]:
                    logger.warning("CMEMS variable %s unit '%s' expected to be 'm/s'", v, unit)


class CMEMSAdapter:
    """
    Adapter for Copernicus Marine Service (CMEMS) Ocean Currents.
    Handles authentication, date routing, spatial/temporal subsetting,
    unit normalization, and NetCDF file generation.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.username = username or os.getenv("CMEMS_USERNAME")
        self.password = password or os.getenv("CMEMS_PASSWORD")
        self.client = client  # Injected client for unit testing/mocking

    def _get_client(self) -> Any:
        """Initialize or return the copernicusmarine client."""
        if self.client is not None:
            return self.client

        try:
            import copernicusmarine
            return copernicusmarine
        except ImportError:
            raise UnavailableError(
                "copernicusmarine library is not installed. Please install copernicusmarine to query live CMEMS data.",
                provider="cmems"
            )

    def fetch_data(
        self,
        request: Union[MetoceanRequest, Dict[str, Any]],
        output_path: Optional[str] = None,
    ) -> str:
        """
        Fetch surface currents from CMEMS for the requested bbox and time window.
        
        Parameters:
            request: Validated MetoceanRequest or dict with bbox, start, end.
            output_path: Optional destination file path (defaults to 'currents.nc').

        Returns:
            Absolute file path to the generated currents.nc.
        """
        if not isinstance(request, MetoceanRequest):
            if isinstance(request, dict):
                request = MetoceanRequest(
                    bbox=request["bbox"],
                    start=request["start"],
                    end=request["end"],
                    what=request.get("what", "currents"),
                    provider=request.get("provider", "cmems"),
                    output_dir=request.get("output_dir"),
                )
            else:
                raise ValidationError(f"Invalid request type: expected MetoceanRequest or dict, got {type(request).__name__}")

        # 1. Determine product & dataset routing
        routing_info = select_cmems_product(request.start_dt)
        product_id = routing_info["product_id"]
        dataset_id = routing_info["dataset_id"]
        logger.info("CMEMS Routing selected product: %s (%s) for start %s", product_id, routing_info["type"], request.start_iso)

        # 2. Build output destination
        if output_path is None:
            dest_dir = Path(request.output_dir) if request.output_dir else Path.cwd()
            ensure_dir(dest_dir)
            target_file = dest_dir / "currents.nc"
        else:
            target_file = Path(output_path)
            ensure_dir(target_file.parent)

        # 3. Retrieve and subset data
        ds = self._query_cmems(request, dataset_id)

        # 4. Normalize dataset
        norm_ds = normalize_currents_dataset(ds)

        # 5. Validate NetCDF schema contract
        validate_currents_dataset(norm_ds)

        # 6. Save to NetCDF
        try:
            norm_ds.to_netcdf(str(target_file), format="NETCDF4", engine="netcdf4")
            logger.info("Successfully generated currents.nc at %s", target_file.resolve())
        except TypeError:
            # Fallback for simpler mock/custom objects that don't accept format/engine kwargs
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write currents.nc to disk: {inner_exc}", provider="cmems")
        except Exception as exc:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write currents.nc to disk: {exc}; {inner_exc}", provider="cmems")

        return str(target_file.resolve())

    def _query_cmems(self, request: MetoceanRequest, dataset_id: str) -> Any:
        """Internal helper to query copernicusmarine dataset with spatial/temporal subset."""
        client = self._get_client()

        # If client is a callable/mock providing open_dataset or subset
        if hasattr(client, "open_dataset"):
            try:
                bounds = request.bbox.to_cmems_bounds()
                ds = client.open_dataset(
                    dataset_id=dataset_id,
                    username=self.username,
                    password=self.password,
                    minimum_longitude=bounds["minimum_longitude"],
                    maximum_longitude=bounds["maximum_longitude"],
                    minimum_latitude=bounds["minimum_latitude"],
                    maximum_latitude=bounds["maximum_latitude"],
                    start_datetime=request.start_iso,
                    end_datetime=request.end_iso,
                    variables=["uo", "vo"],
                )
                return ds
            except Exception as exc:
                self._map_cmems_exception(exc)
        elif hasattr(client, "subset"):
            try:
                bounds = request.bbox.to_cmems_bounds()
                ds = client.subset(
                    dataset_id=dataset_id,
                    username=self.username,
                    password=self.password,
                    minimum_longitude=bounds["minimum_longitude"],
                    maximum_longitude=bounds["maximum_longitude"],
                    minimum_latitude=bounds["minimum_latitude"],
                    maximum_latitude=bounds["maximum_latitude"],
                    start_datetime=request.start_iso,
                    end_datetime=request.end_iso,
                    variables=["uo", "vo"],
                )
                return ds
            except Exception as exc:
                self._map_cmems_exception(exc)
        else:
            raise UnavailableError(
                "Copernicus Marine client does not expose 'open_dataset' or 'subset' interface.",
                provider="cmems"
            )

    def _map_cmems_exception(self, exc: Exception) -> None:
        """Map generic Copernicus Marine SDK exceptions to structured MetoceanError classes."""
        err_msg = str(exc)
        err_lower = err_msg.lower()

        if any(w in err_lower for w in ["unauthorized", "auth", "credential", "login", "forbidden", "401", "403"]):
            raise AuthFailedError(f"CMEMS Authentication failed: {err_msg}", provider="cmems", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["licence", "license", "terms"]):
            raise LicenceNotAcceptedError(f"CMEMS Licence not accepted: {err_msg}", provider="cmems", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["not found", "no data", "empty dataset", "out of range", "coverage"]):
            raise NoDataForPeriodError(f"CMEMS has no data for the requested period/bbox: {err_msg}", provider="cmems", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["timeout", "timed out", "connection reset", "slow"]):
            raise TimeoutError(f"CMEMS connection timed out: {err_msg}", provider="cmems", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["rate limit", "too many requests", "429", "quota"]):
            raise RateLimitedError(f"CMEMS rate limit reached: {err_msg}", provider="cmems", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["service unavailable", "503", "502", "500", "gateway"]):
            raise UnavailableError(f"CMEMS service unavailable: {err_msg}", provider="cmems", details={"raw_error": err_msg})
        else:
            raise BadResponseError(f"CMEMS query failed with error: {err_msg}", provider="cmems", details={"raw_error": err_msg})
