"""Provider Status and Health Check Module for Satellite Scene Service.

Monitors real-time API health, connectivity, and response latency for:
- CDSE (Copernicus Data Space Ecosystem)
- ASF (Alaska Satellite Facility)
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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


# ---------------------------------------------------------------------------
# provider_status.json — frozen contract 8 (contracts/schemas/tabular.py)
# ---------------------------------------------------------------------------

_HEALTH_TO_CONTRACT_STATUS = {
    "UP": "WORKING",
    "DEGRADED": "DEGRADED",
    "DOWN": "FAILED",
    "UNCONFIGURED": "UNKNOWN",
}

_PROVIDER_PURPOSE = {
    "CDSE": "Sentinel-1 IW GRDH scenes (primary provider)",
    "ASF": "Sentinel-1 IW GRDH scenes (fallback provider)",
}

SCENE_PROVIDER_CHAIN = ["CDSE", "ASF", "LocalCache"]


def _error_class_for(health: ProviderHealth) -> str:
    """Map a probe result onto the frozen ErrorClass taxonomy."""
    if health.is_available:
        return "NONE"
    detail = str((health.details or {}).get("error", "")).lower()
    reason = str((health.details or {}).get("reason", "")).lower()
    text = detail + " " + reason
    if "401" in text or "403" in text or "credential" in text or "auth" in text:
        return "AUTH_FAILED"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if "429" in text or "rate" in text:
        return "RATE_LIMITED"
    if "http 4" in text:
        return "BAD_RESPONSE"
    return "UNAVAILABLE"


def build_provider_status_payload(
    status_map: Optional[Dict[str, ProviderHealth]] = None,
    owner: str = "scene_service (Pavitra)",
    mock_mode: bool = False,
) -> Dict:
    """Assemble the provider_status.json payload per the ProviderStatusFile contract."""
    if status_map is None:
        status_map = get_api_status(mock_mode=mock_mode)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    providers = []
    active = "LocalCache"
    for name in ("CDSE", "ASF"):
        health = status_map.get(name.lower())
        if health is None:
            continue
        code = None
        if health.details:
            code = health.details.get("status_code")
            if code is None:
                err = str(health.details.get("error", ""))
                if err.startswith("HTTP "):
                    try:
                        code = int(err.split()[1])
                    except (IndexError, ValueError):
                        code = None
        providers.append(
            {
                "provider": name,
                "purpose": _PROVIDER_PURPOSE.get(name, "Sentinel-1 scene provider"),
                "status": _HEALTH_TO_CONTRACT_STATUS.get(health.status, "UNKNOWN"),
                "last_code": code,
                "last_latency_ms": int(health.latency_ms) if health.latency_ms is not None else None,
                "last_success_utc": now_utc if health.is_available else None,
                "last_failure_utc": None if health.is_available else now_utc,
                "last_error_class": _error_class_for(health),
                "chain": list(SCENE_PROVIDER_CHAIN),
                "active_provider": name,  # provisional; fixed up below
            }
        )
    # active_provider: the first WORKING/DEGRADED chain member, else LocalCache.
    for p in providers:
        if p["status"] in ("WORKING", "DEGRADED"):
            active = p["provider"]
            break
    for p in providers:
        p["active_provider"] = active

    return {
        "generated_utc": now_utc,
        "owner": owner,
        "providers": providers,
    }


def write_provider_status_file(
    path,
    status_map: Optional[Dict[str, ProviderHealth]] = None,
    owner: str = "scene_service (Pavitra)",
    mock_mode: bool = False,
) -> Path:
    """Write provider_status.json, validated against the frozen contract when
    the ``contracts`` package is importable (it always is under pytest; a
    standalone ``python -m satellite.cli`` from scene_service/ may not see it,
    in which case the payload is still written in the exact contract shape)."""
    payload = build_provider_status_payload(status_map, owner=owner, mock_mode=mock_mode)

    try:
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from contracts.schemas.tabular import ProviderStatusFile

        ProviderStatusFile.model_validate(payload)  # raises on contract breach
    except ImportError:
        logger.warning("contracts package not importable; writing unvalidated provider_status.json")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p

