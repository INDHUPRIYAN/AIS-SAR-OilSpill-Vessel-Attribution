"""
Metocean Data Service Dynamic Fallback Chain and Orchestrator.
Executes independent fallback chains per data type:
- Ocean Currents: CMEMS -> HYCOM -> Static Cache -> Degraded
- Atmospheric Wind: ERA5 -> Open-Meteo -> Static Cache -> Degraded
Integrates circuit breakers, live telemetry tracking, caching, and structured degraded states.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union

from metocean.cache import MetoceanCache
from metocean.cmems_adapter import CMEMSAdapter
from metocean.era5_adapter import ERA5Adapter
from metocean.errors import (
    AuthFailedError,
    BadResponseError,
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
from metocean.status import ProviderStatusTracker, _GLOBAL_TRACKER
from metocean.utils import ensure_dir

logger = logging.getLogger(__name__)


class MetoceanChain:
    """
    Orchestrator for Metocean data retrieval with automatic fallback and circuit breaking.
    """

    def __init__(
        self,
        cmems_adapter: Optional[CMEMSAdapter] = None,
        hycom_adapter: Optional[HYCOMAdapter] = None,
        era5_adapter: Optional[ERA5Adapter] = None,
        openmeteo_adapter: Optional[OpenMeteoAdapter] = None,
        cache: Optional[MetoceanCache] = None,
        status_tracker: Optional[ProviderStatusTracker] = None,
    ):
        self.cmems = cmems_adapter or CMEMSAdapter()
        self.hycom = hycom_adapter or HYCOMAdapter()
        self.era5 = era5_adapter or ERA5Adapter()
        self.openmeteo = openmeteo_adapter or OpenMeteoAdapter()
        self.cache = cache or MetoceanCache()
        self.status = status_tracker or _GLOBAL_TRACKER

    def fetch_currents(
        self,
        request: MetoceanRequest,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute ocean currents fallback chain: CMEMS -> HYCOM -> Static Cache -> Degraded.
        """
        # Explicit provider override check
        if request.provider not in ("auto", "cache"):
            if request.provider == "cmems":
                return self._try_provider("CMEMS", self.cmems, request, output_path, "currents")
            elif request.provider == "hycom":
                return self._try_provider("HYCOM", self.hycom, request, output_path, "currents")
            else:
                logger.warning("Explicit provider '%s' not applicable for currents, defaulting to auto chain", request.provider)

        # 1. Primary: CMEMS
        res = self._try_provider("CMEMS", self.cmems, request, output_path, "currents")
        if res["success"]:
            return res

        # 2. Secondary Fallback: HYCOM GOFS
        logger.warning("CMEMS failed (%s); initiating fallback to HYCOM", res.get("error"))
        res = self._try_provider("HYCOM", self.hycom, request, output_path, "currents", active_provider="HYCOM")
        if res["success"]:
            return res

        # 3. Final Fallback: Static Cache
        logger.warning("HYCOM fallback failed (%s); checking Static Cache", res.get("error"))
        cache_hit = self.cache.get_currents(request)
        if cache_hit:
            self.status.record_attempt("StaticCache", success=True, latency_ms=1.0, active_provider="StaticCache")
            return {
                "success": True,
                "path": cache_hit,
                "provider": "StaticCache",
                "degraded": False,
            }

        static_fallback = self.cache.get_static_fallback("currents", output_path)
        if static_fallback:
            self.status.record_attempt("StaticCache", success=True, latency_ms=1.0, active_provider="StaticCache")
            return {
                "success": True,
                "path": static_fallback,
                "provider": "StaticCache",
                "degraded": True,
                "warning": "Operating in static fallback demo mode",
            }

        # 4. Structured Degraded State
        self.status.record_attempt("StaticCache", success=False, error=UnavailableError("No cache available"), active_provider=None)
        return {
            "success": False,
            "path": None,
            "provider": None,
            "degraded": True,
            "error": "All ocean currents providers (CMEMS, HYCOM, Cache) failed or unavailable",
        }

    def fetch_wind(
        self,
        request: MetoceanRequest,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute atmospheric wind fallback chain: ERA5 -> Open-Meteo -> Static Cache -> Degraded.
        """
        # Explicit provider override check
        if request.provider not in ("auto", "cache"):
            if request.provider in ("era5", "cds"):
                return self._try_provider("ERA5", self.era5, request, output_path, "wind")
            elif request.provider in ("openmeteo", "open_meteo"):
                return self._try_provider("OpenMeteo", self.openmeteo, request, output_path, "wind")
            else:
                logger.warning("Explicit provider '%s' not applicable for wind, defaulting to auto chain", request.provider)

        # 1. Primary: ERA5
        res = self._try_provider("ERA5", self.era5, request, output_path, "wind")
        if res["success"]:
            return res

        # 2. Secondary Fallback: Open-Meteo
        logger.warning("ERA5 failed (%s); initiating fallback to Open-Meteo", res.get("error"))
        res = self._try_provider("OpenMeteo", self.openmeteo, request, output_path, "wind", active_provider="OpenMeteo")
        if res["success"]:
            return res

        # 3. Final Fallback: Static Cache
        logger.warning("Open-Meteo fallback failed (%s); checking Static Cache", res.get("error"))
        cache_hit = self.cache.get_wind(request)
        if cache_hit:
            self.status.record_attempt("StaticCache", success=True, latency_ms=1.0, active_provider="StaticCache")
            return {
                "success": True,
                "path": cache_hit,
                "provider": "StaticCache",
                "degraded": False,
            }

        static_fallback = self.cache.get_static_fallback("wind", output_path)
        if static_fallback:
            self.status.record_attempt("StaticCache", success=True, latency_ms=1.0, active_provider="StaticCache")
            return {
                "success": True,
                "path": static_fallback,
                "provider": "StaticCache",
                "degraded": True,
                "warning": "Operating in static fallback demo mode",
            }

        # 4. Structured Degraded State
        self.status.record_attempt("StaticCache", success=False, error=UnavailableError("No cache available"), active_provider=None)
        return {
            "success": False,
            "path": None,
            "provider": None,
            "degraded": True,
            "error": "All atmospheric wind providers (ERA5, Open-Meteo, Cache) failed or unavailable",
        }

    def _try_provider(
        self,
        provider_name: str,
        adapter: Any,
        request: MetoceanRequest,
        output_path: Optional[str],
        data_type: str,
        active_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Attempt single provider execution with circuit breaker and telemetry tracking."""
        # 1. Circuit breaker gate check
        if not self.status.can_attempt(provider_name):
            logger.warning("Circuit breaker for %s is OPEN. Fast-failing and skipping to next provider.", provider_name)
            return {
                "success": False,
                "path": None,
                "provider": provider_name,
                "error": f"Circuit breaker for {provider_name} is OPEN",
            }

        start_time = time.perf_counter()
        try:
            path = adapter.fetch_data(request, output_path=output_path)
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            # Store in cache
            if data_type == "currents":
                self.cache.put_currents(request, path)
            else:
                self.cache.put_wind(request, path)

            self.status.record_attempt(
                provider=provider_name,
                success=True,
                latency_ms=latency_ms,
                active_provider=active_provider or provider_name,
            )

            return {
                "success": True,
                "path": path,
                "provider": provider_name,
                "degraded": False,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            self.status.record_attempt(
                provider=provider_name,
                success=False,
                latency_ms=latency_ms,
                error=exc,
                active_provider=None,
            )
            return {
                "success": False,
                "path": None,
                "provider": provider_name,
                "error": str(exc),
            }

    def fetch_metocean(
        self,
        request: Union[MetoceanRequest, Dict[str, Any]],
        output_dir: Optional[str] = None,
    ) -> MetoceanResponse:
        """
        Main entrypoint: Fetches currents and winds using independent fallback chains.
        """
        if not isinstance(request, MetoceanRequest):
            if isinstance(request, dict):
                request = MetoceanRequest(
                    bbox=request["bbox"],
                    start=request["start"],
                    end=request["end"],
                    what=request.get("what", "both"),
                    provider=request.get("provider", "auto"),
                    output_dir=output_dir or request.get("output_dir"),
                )
            else:
                raise ValidationError(f"Expected MetoceanRequest or dict, got {type(request).__name__}")

        dest_dir = Path(request.output_dir) if request.output_dir else Path.cwd()
        ensure_dir(dest_dir)

        currents_path = None
        wind_path = None
        providers_used = {}
        degraded = False
        audit_trail = []

        # 1. Currents Execution
        if request.what in ("currents", "both"):
            cur_out = str(dest_dir / "currents.nc")
            cur_res = self.fetch_currents(request, output_path=cur_out)
            if cur_res["success"]:
                currents_path = cur_res["path"]
                providers_used["currents"] = cur_res["provider"]
                if cur_res.get("degraded"):
                    degraded = True
            else:
                providers_used["currents"] = "DEGRADED"
                degraded = True
                audit_trail.append(f"Currents: {cur_res.get('error')}")

        # 2. Wind Execution
        if request.what in ("wind", "both"):
            wind_out = str(dest_dir / "wind.nc")
            wind_res = self.fetch_wind(request, output_path=wind_out)
            if wind_res["success"]:
                wind_path = wind_res["path"]
                providers_used["wind"] = wind_res["provider"]
                if wind_res.get("degraded"):
                    degraded = True
            else:
                providers_used["wind"] = "DEGRADED"
                degraded = True
                audit_trail.append(f"Wind: {wind_res.get('error')}")

        # 3. Save status to provider_status.json
        status_file = dest_dir / "provider_status.json"
        self.status.save_to_json(status_file)

        status_str = "success"
        if degraded:
            status_str = "degraded" if (currents_path or wind_path) else "failed"

        return MetoceanResponse(
            currents_path=currents_path,
            wind_path=wind_path,
            providers_used=providers_used,
            status=status_str,
            metadata={
                "degraded": degraded,
                "status_file": str(status_file.resolve()),
                "audit_trail": audit_trail,
            },
        )
