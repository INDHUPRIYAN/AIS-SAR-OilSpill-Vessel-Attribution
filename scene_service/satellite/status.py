"""Provider Status and Health Check Module for Satellite Scene Service.

Monitors real-time API health, connectivity, and response latency for:
- CDSE (Copernicus Data Space Ecosystem)
- ASF (Alaska Satellite Facility)
"""

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .models import ProviderHealth

logger = logging.getLogger(__name__)

# Probe Endpoints
CDSE_PROBE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$top=1"
ASF_PROBE_URL = (
    "https://api.daac.asf.alaska.edu/services/search/param?dataset=SENTINEL-1&maxResults=1&output=json"
)

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_LATENCY_THRESHOLD_MS = 3000.0


def check_cdse_health(
    username: Optional[str] = None,
    password: Optional[str] = None,
    probe_url: str = CDSE_PROBE_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
    mock_mode: bool = False,
    require_credentials: bool = False,
) -> ProviderHealth:
    """Probes CDSE catalogue endpoint and evaluates operational health."""
    if mock_mode:
        return ProviderHealth(
            provider_name="CDSE",
            is_available=True,
            status="UP",
            latency_ms=45.0,
            details={
                "endpoint": probe_url,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "mode": "mock",
            },
        )

    user = username or os.getenv("CDSE_USERNAME")
    pwd = password or os.getenv("CDSE_PASSWORD")

    if require_credentials and (not user or not pwd):
        return ProviderHealth(
            provider_name="CDSE",
            is_available=False,
            status="UNCONFIGURED",
            latency_ms=None,
            details={
                "reason": "Missing CDSE credentials (CDSE_USERNAME/CDSE_PASSWORD)",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    req = urllib.request.Request(
        probe_url,
        headers={"Accept": "application/json", "User-Agent": "OceanTrace-HealthProbe"},
        method="GET",
    )

    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            status_code = resp.status
            if status_code == 200:
                health_status = "DEGRADED" if latency_ms > latency_threshold_ms else "UP"
                return ProviderHealth(
                    provider_name="CDSE",
                    is_available=True,
                    status=health_status,
                    latency_ms=round(latency_ms, 2),
                    details={
                        "endpoint": probe_url,
                        "status_code": status_code,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            else:
                return ProviderHealth(
                    provider_name="CDSE",
                    is_available=False,
                    status="DOWN",
                    latency_ms=round(latency_ms, 2),
                    details={
                        "endpoint": probe_url,
                        "status_code": status_code,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    except urllib.error.HTTPError as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        health_status = "DEGRADED" if err.code < 500 else "DOWN"
        return ProviderHealth(
            provider_name="CDSE",
            is_available=False,
            status=health_status,
            latency_ms=round(latency_ms, 2),
            details={
                "endpoint": probe_url,
                "error": f"HTTP {err.code}",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except (urllib.error.URLError, TimeoutError) as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        reason_str = str(getattr(err, "reason", err))
        return ProviderHealth(
            provider_name="CDSE",
            is_available=False,
            status="DOWN",
            latency_ms=round(latency_ms, 2),
            details={
                "endpoint": probe_url,
                "error": f"Connection/Timeout error: {reason_str}",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return ProviderHealth(
            provider_name="CDSE",
            is_available=False,
            status="DOWN",
            latency_ms=round(latency_ms, 2),
            details={
                "endpoint": probe_url,
                "error": str(err),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def check_asf_health(
    username: Optional[str] = None,
    password: Optional[str] = None,
    probe_url: str = ASF_PROBE_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
    mock_mode: bool = False,
    require_credentials: bool = False,
) -> ProviderHealth:
    """Probes ASF Vertex search endpoint and evaluates operational health."""
    if mock_mode:
        return ProviderHealth(
            provider_name="ASF",
            is_available=True,
            status="UP",
            latency_ms=50.0,
            details={
                "endpoint": probe_url,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "mode": "mock",
            },
        )

    user = username or os.getenv("ASF_USERNAME")
    pwd = password or os.getenv("ASF_PASSWORD")

    if require_credentials and (not user or not pwd):
        return ProviderHealth(
            provider_name="ASF",
            is_available=False,
            status="UNCONFIGURED",
            latency_ms=None,
            details={
                "reason": "Missing ASF credentials (ASF_USERNAME/ASF_PASSWORD)",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    req = urllib.request.Request(
        probe_url,
        headers={"Accept": "application/json", "User-Agent": "OceanTrace-HealthProbe"},
        method="GET",
    )

    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            status_code = resp.status
            if status_code == 200:
                health_status = "DEGRADED" if latency_ms > latency_threshold_ms else "UP"
                return ProviderHealth(
                    provider_name="ASF",
                    is_available=True,
                    status=health_status,
                    latency_ms=round(latency_ms, 2),
                    details={
                        "endpoint": probe_url,
                        "status_code": status_code,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            else:
                return ProviderHealth(
                    provider_name="ASF",
                    is_available=False,
                    status="DOWN",
                    latency_ms=round(latency_ms, 2),
                    details={
                        "endpoint": probe_url,
                        "status_code": status_code,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    except urllib.error.HTTPError as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        health_status = "DEGRADED" if err.code < 500 else "DOWN"
        return ProviderHealth(
            provider_name="ASF",
            is_available=False,
            status=health_status,
            latency_ms=round(latency_ms, 2),
            details={
                "endpoint": probe_url,
                "error": f"HTTP {err.code}",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except (urllib.error.URLError, TimeoutError) as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        reason_str = str(getattr(err, "reason", err))
        return ProviderHealth(
            provider_name="ASF",
            is_available=False,
            status="DOWN",
            latency_ms=round(latency_ms, 2),
            details={
                "endpoint": probe_url,
                "error": f"Connection/Timeout error: {reason_str}",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return ProviderHealth(
            provider_name="ASF",
            is_available=False,
            status="DOWN",
            latency_ms=round(latency_ms, 2),
            details={
                "endpoint": probe_url,
                "error": str(err),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def get_api_status(
    mock_mode: bool = False,
    require_credentials: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, ProviderHealth]:
    """Retrieves health check reports for both CDSE and ASF providers.

    Returns:
        Dictionary mapping provider name ("cdse", "asf") to ProviderHealth model.
    """
    cdse_health = check_cdse_health(
        mock_mode=mock_mode,
        require_credentials=require_credentials,
        timeout=timeout,
    )
    asf_health = check_asf_health(
        mock_mode=mock_mode,
        require_credentials=require_credentials,
        timeout=timeout,
    )

    return {
        "cdse": cdse_health,
        "asf": asf_health,
    }

