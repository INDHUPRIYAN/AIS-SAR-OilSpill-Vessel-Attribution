"""
OceanTrace Met-Ocean Data Service (Module 4).
Provides automated retrieval, subsetting, normalization, caching, and fallback orchestration
for ocean currents (CMEMS / HYCOM) and atmospheric wind (ERA5 / Open-Meteo).
"""

from metocean.cache import MetoceanCache, validate_cached_netcdf
from metocean.chain import MetoceanChain
from metocean.cmems_adapter import CMEMSAdapter, select_cmems_product
from metocean.era5_adapter import ERA5Adapter
from metocean.errors import (
    AuthFailedError,
    BadResponseError,
    CacheCorruptedError,
    LicenceNotAcceptedError,
    MetoceanError,
    NoDataForPeriodError,
    RateLimitedError,
    TimeoutError,
    UnavailableError,
    ValidationError,
)
from metocean.hycom_adapter import HYCOMAdapter
from metocean.models import BBox, MetoceanRequest, MetoceanResponse
from metocean.openmeteo_adapter import OpenMeteoAdapter
from metocean.status import CircuitBreaker, CircuitBreakerState, ProviderStatusTracker, get_status

__version__ = "1.0.0"
__author__ = "Keerthana (Developer 4 - OceanTrace Metocean Service)"

# Primary functional API
_default_chain = MetoceanChain()


def fetch_metocean(request, output_dir=None) -> MetoceanResponse:
    """
    Top-level programmatic entrypoint for Met-Ocean service.
    
    Parameters:
        request: MetoceanRequest or dict with bbox, start, end, what, provider.
        output_dir: Optional destination directory for NetCDF outputs.

    Returns:
        MetoceanResponse with paths to currents.nc, wind.nc, and telemetry metadata.
    """
    return _default_chain.fetch_metocean(request, output_dir=output_dir)


__all__ = [
    "fetch_metocean",
    "MetoceanChain",
    "MetoceanCache",
    "CMEMSAdapter",
    "ERA5Adapter",
    "HYCOMAdapter",
    "OpenMeteoAdapter",
    "ProviderStatusTracker",
    "CircuitBreaker",
    "CircuitBreakerState",
    "get_status",
    "select_cmems_product",
    "validate_cached_netcdf",
    "BBox",
    "MetoceanRequest",
    "MetoceanResponse",
    "MetoceanError",
    "AuthFailedError",
    "LicenceNotAcceptedError",
    "NoDataForPeriodError",
    "TimeoutError",
    "UnavailableError",
    "BadResponseError",
    "RateLimitedError",
    "ValidationError",
    "CacheCorruptedError",
]
