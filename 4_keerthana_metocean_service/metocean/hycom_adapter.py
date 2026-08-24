"""
HYCOM GOFS Ocean Currents Fallback Adapter.
Retrieves surface currents (water_u, water_v -> uo, vo) via OPeNDAP / THREDDS,
converting 0..360 longitude to standard WGS84 [-180, +180], extracting surface depth,
normalizing units to m/s, and generating standardized 'currents.nc'.
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
from metocean.utils import ensure_dir, lon_to_360, normalize_longitude

logger = logging.getLogger(__name__)

# Default HYCOM GOFS OPeNDAP Server Endpoint
HYCOM_OPENDAP_URL = "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_53.X"


def get_hycom_bbox_360(bbox: BBox) -> Tuple[float, float, float, float]:
    """
    Convert standard WGS84 BBox [-180, 180] to HYCOM's native 0..360 longitude coordinates.
    Returns: (min_lon_360, min_lat, max_lon_360, max_lat)
    """
    min_lon_360 = lon_to_360(bbox.min_lon)
    max_lon_360 = lon_to_360(bbox.max_lon)
    return (min_lon_360, bbox.min_lat, max_lon_360, bbox.max_lat)


def normalize_hycom_dataset(ds: Any) -> Any:
    """
    Normalize raw HYCOM Dataset to the agreed OceanTrace currents contract:
    - Dimensions: (time, lat, lon)
    - Variables: uo, vo (m/s)
    - Surface layer only (depth top slice)
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

    # 2. Extract surface current layer if depth dimension exists
    for depth_dim in ["depth", "deptho", "depth_layer"]:
        if depth_dim in dims_keys and hasattr(ds, "isel"):
            ds = ds.isel({depth_dim: 0})
            if hasattr(ds, "squeeze"):
                ds = ds.squeeze(drop=True)

    # 3. Variable mapping: map HYCOM water_u / water_v / surf_u / surf_v to uo and vo
    var_names = set(ds.data_vars.keys()) if hasattr(ds, "data_vars") else set()
    var_rename = {}
    if "uo" not in var_names:
        for u_alias in ["water_u", "surf_u", "u", "eastward_velocity"]:
            if u_alias in var_names:
                var_rename[u_alias] = "uo"
                break

    if "vo" not in var_names:
        for v_alias in ["water_v", "surf_v", "v", "northward_velocity"]:
            if v_alias in var_names:
                var_rename[v_alias] = "vo"
                break

    if var_rename and hasattr(ds, "rename"):
        ds = ds.rename(var_rename)

    var_names = set(ds.data_vars.keys()) if hasattr(ds, "data_vars") else set()
    if "uo" not in var_names or "vo" not in var_names:
        raise BadResponseError(
            f"HYCOM response missing required velocity variables 'uo', 'vo'. Found: {list(var_names)}",
            provider="hycom"
        )

    # Drop any non-velocity variables
    drop_vars = [v for v in ds.data_vars if v not in ("uo", "vo")]
    if drop_vars and hasattr(ds, "drop_vars"):
        ds = ds.drop_vars(drop_vars)

    # 4. Longitude normalization: convert 0..360 grid to standard WGS84 [-180, 180]
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
            logger.debug("HYCOM Longitude normalization notice: %s", e)

    # 5. Coordinate sorting: ensure ascending lat and time
    if hasattr(ds, "sortby"):
        if "lat" in ds.coords:
            ds = ds.sortby("lat")
        if "time" in ds.coords:
            ds = ds.sortby("time")

    # 6. Standardize metadata attributes (CF-1.8 Conventions)
    if hasattr(ds, "__getitem__"):
        if "uo" in ds.data_vars and hasattr(ds["uo"], "attrs"):
            ds["uo"].attrs = {
                "long_name": "Eastward surface current velocity",
                "standard_name": "eastward_sea_water_velocity",
                "units": "m/s",
                "valid_min": -10.0,
                "valid_max": 10.0,
            }
        if "vo" in ds.data_vars and hasattr(ds["vo"], "attrs"):
            ds["vo"].attrs = {
                "long_name": "Northward surface current velocity",
                "standard_name": "northward_sea_water_velocity",
                "units": "m/s",
                "valid_min": -10.0,
                "valid_max": 10.0,
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
            "title": "OceanTrace Normalized Surface Currents (HYCOM GOFS Fallback)",
            "provider": "HYCOM GOFS 3.1 (OPeNDAP)",
            "crs": "EPSG:4326 / WGS84",
            "conventions": "CF-1.8",
            "history": f"Normalized at {datetime.now(timezone.utc).isoformat()}",
        }

    return ds


def validate_hycom_dataset(ds: Any) -> None:
    """
    Validate that an xarray Dataset strictly conforms to the OceanTrace currents contract.
    """
    if hasattr(ds, "data_vars"):
        required_vars = {"uo", "vo"}
        found_vars = set(ds.data_vars.keys())
        if not required_vars.issubset(found_vars):
            raise BadResponseError(
                f"NetCDF currents contract violation: missing variables {required_vars - found_vars}",
                provider="hycom"
            )

    if hasattr(ds, "dims"):
        required_dims = {"time", "lat", "lon"}
        found_dims = set(ds.dims.keys())
        if not required_dims.issubset(found_dims):
            raise BadResponseError(
                f"NetCDF currents contract violation: missing dimensions {required_dims - found_dims}",
                provider="hycom"
            )

    # Unit checks
    if hasattr(ds, "__getitem__"):
        for v in ["uo", "vo"]:
            if v in ds.data_vars and hasattr(ds[v], "attrs"):
                unit = ds[v].attrs.get("units", "")
                if unit not in ["m/s", "m s-1", "meter/sec", "meters/second"]:
                    logger.warning("HYCOM variable %s unit '%s' expected to be 'm/s'", v, unit)


class HYCOMAdapter:
    """
    Fallback Adapter for HYCOM Global Ocean Forecast System (GOFS).
    Accesses remote OPeNDAP endpoints, translates 0..360 coordinates to [-180, 180],
    subsets the target spatial/temporal window, normalizes variables, and saves 'currents.nc'.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.base_url = base_url or os.getenv("HYCOM_OPENDAP_URL", HYCOM_OPENDAP_URL)
        self.client = client  # Injected client or mock for unit testing

    def fetch_data(
        self,
        request: Union[MetoceanRequest, Dict[str, Any]],
        output_path: Optional[str] = None,
    ) -> str:
        """
        Fetch surface currents from HYCOM GOFS for the requested bbox and time window.

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
                    provider=request.get("provider", "hycom"),
                    output_dir=request.get("output_dir"),
                )
            else:
                raise ValidationError(f"Invalid request type: expected MetoceanRequest or dict, got {type(request).__name__}")

        # 1. Build output destination
        if output_path is None:
            dest_dir = Path(request.output_dir) if request.output_dir else Path.cwd()
            ensure_dir(dest_dir)
            target_file = dest_dir / "currents.nc"
        else:
            target_file = Path(output_path)
            ensure_dir(target_file.parent)

        # 2. Query HYCOM via OPeNDAP
        logger.info("HYCOM fallback querying OPeNDAP endpoint: %s for bbox %s", self.base_url, request.bbox.as_list())
        ds = self._query_hycom(request)

        # 3. Normalize dataset
        norm_ds = normalize_hycom_dataset(ds)

        # 4. Validate NetCDF schema contract
        validate_hycom_dataset(norm_ds)

        # 5. Save to NetCDF
        try:
            norm_ds.to_netcdf(str(target_file), format="NETCDF4", engine="netcdf4")
            logger.info("Successfully generated currents.nc (HYCOM) at %s", target_file.resolve())
        except TypeError:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write currents.nc to disk: {inner_exc}", provider="hycom")
        except Exception as exc:
            try:
                norm_ds.to_netcdf(str(target_file))
            except Exception as inner_exc:
                raise BadResponseError(f"Failed to write currents.nc to disk: {exc}; {inner_exc}", provider="hycom")

        return str(target_file.resolve())

    def _query_hycom(self, request: MetoceanRequest) -> Any:
        """Internal helper to retrieve HYCOM dataset via OPeNDAP."""
        # 1. Injected client / mock handling
        if self.client is not None:
            if hasattr(self.client, "open_dataset") and callable(getattr(self.client, "open_dataset")):
                try:
                    return self.client.open_dataset(self.base_url, request=request)
                except Exception as exc:
                    self._map_hycom_exception(exc)
            elif hasattr(self.client, "query"):
                try:
                    return self.client.query(request)
                except Exception as exc:
                    self._map_hycom_exception(exc)

        # 2. Live xarray OPeNDAP access
        if not XARRAY_AVAILABLE or xr is None:
            raise UnavailableError(
                "xarray library is required to access remote HYCOM OPeNDAP endpoints.",
                provider="hycom"
            )

        try:
            min_lon_360, min_lat, max_lon_360, max_lat = get_hycom_bbox_360(request.bbox)
            # Open remote OPeNDAP dataset
            ds = xr.open_dataset(self.base_url, decode_times=True)

            # Spatial slice
            lat_slice = slice(min_lat, max_lat)
            lon_slice = slice(min_lon_360, max_lon_360)
            time_slice = slice(request.start_iso, request.end_iso)

            sub_ds = ds.sel(lat=lat_slice, lon=lon_slice, time=time_slice)
            return sub_ds
        except Exception as exc:
            self._map_hycom_exception(exc)

    def _map_hycom_exception(self, exc: Exception) -> None:
        """Map OPeNDAP and network exceptions to structured MetoceanError classes."""
        err_msg = str(exc)
        err_lower = err_msg.lower()

        if any(w in err_lower for w in ["timeout", "timed out", "connection reset", "slow"]):
            raise TimeoutError(f"HYCOM OPeNDAP connection timed out: {err_msg}", provider="hycom", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["404", "not found", "no data", "out of bounds", "empty"]):
            raise NoDataForPeriodError(f"HYCOM OPeNDAP dataset or date not found: {err_msg}", provider="hycom", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["503", "502", "500", "dods", "unreachable", "refused", "unavailable"]):
            raise UnavailableError(f"HYCOM OPeNDAP server unavailable: {err_msg}", provider="hycom", details={"raw_error": err_msg})
        elif any(w in err_lower for w in ["401", "403", "forbidden", "unauthorized"]):
            raise AuthFailedError(f"HYCOM authentication failure: {err_msg}", provider="hycom", details={"raw_error": err_msg})
        else:
            raise BadResponseError(f"HYCOM query failed with error: {err_msg}", provider="hycom", details={"raw_error": err_msg})
