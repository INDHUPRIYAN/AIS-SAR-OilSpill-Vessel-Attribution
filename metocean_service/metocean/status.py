"""
Provider Health, Circuit Breaker, and Status Tracking Module.
Maintains live operational telemetry for CMEMS, ERA5, HYCOM, Open-Meteo, and Static Cache,
enforcing circuit breaker states (CLOSED, OPEN, HALF_OPEN) and persisting provider_status.json.
"""

from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class CircuitBreakerState(str, Enum):
    """Operational states of the provider circuit breaker."""
    CLOSED = "CLOSED"        # Normal operations allowed
    OPEN = "OPEN"            # Fast-fail; calls blocked during cooldown
    HALF_OPEN = "HALF_OPEN"  # Single trial request permitted after cooldown


class CircuitBreaker:
    """
    Per-provider Circuit Breaker to prevent hammering failing external APIs.
    Transitions:
    - CLOSED -> OPEN when failure_count >= failure_threshold
    - OPEN -> HALF_OPEN when elapsed time > cooldown_seconds
    - HALF_OPEN -> CLOSED on successful trial execution
    - HALF_OPEN -> OPEN on failed trial execution
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.01, cooldown_seconds)
        self.state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self.failure_count: int = 0
        self.last_failure_time: Optional[float] = None
        self.last_state_change: float = time.time()

    def can_execute(self) -> bool:
        """Check if request to provider should be permitted."""
        now = time.time()

        if self.state == CircuitBreakerState.CLOSED:
            return True

        if self.state == CircuitBreakerState.OPEN:
            if self.last_failure_time and (now - self.last_failure_time >= self.cooldown_seconds):
                logger.info("Circuit breaker for %s entering HALF_OPEN trial state.", self.name)
                self.state = CircuitBreakerState.HALF_OPEN
                self.last_state_change = now
                return True
            return False

        if self.state == CircuitBreakerState.HALF_OPEN:
            return True

        return False

    def record_success(self) -> None:
        """Record successful execution and reset circuit breaker to CLOSED."""
        if self.state != CircuitBreakerState.CLOSED or self.failure_count > 0:
            logger.info("Circuit breaker for %s reset to CLOSED after success.", self.name)
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_state_change = time.time()

    def record_failure(self) -> None:
        """Record provider failure and open circuit if threshold exceeded."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitBreakerState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            self.last_state_change = self.last_failure_time
            logger.warning(
                "Circuit breaker for %s OPENED after %d consecutive failures. Cooldown: %.1fs.",
                self.name, self.failure_count, self.cooldown_seconds
            )


class ProviderStatusTracker:
    """
    Centralized health monitor and telemetry exporter for metocean providers.
    Persists structured provider_status.json.
    """

    PROVIDER_METADATA = {
        "CMEMS": {
            "purpose": "Primary surface ocean currents (GLORYS / Analysis-Forecast)",
            "chain": ["CMEMS", "HYCOM", "StaticCache"],
        },
        "HYCOM": {
            "purpose": "Secondary fallback surface ocean currents (GOFS 3.1 OPeNDAP)",
            "chain": ["CMEMS", "HYCOM", "StaticCache"],
        },
        "ERA5": {
            "purpose": "Primary 10m atmospheric winds (ECMWF CDS Reanalysis)",
            "chain": ["ERA5", "OpenMeteo", "StaticCache"],
        },
        "OpenMeteo": {
            "purpose": "Secondary fallback 10m atmospheric winds (Open-Meteo REST API)",
            "chain": ["ERA5", "OpenMeteo", "StaticCache"],
        },
        "StaticCache": {
            "purpose": "Final offline fallback datasets for drift engine",
            "chain": ["StaticCache"],
        },
    }

    def __init__(self, output_dir: Optional[Union[str, Path]] = None):
        self.output_dir = Path(output_dir) if output_dir else Path.cwd()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {
            "CMEMS": CircuitBreaker("CMEMS", failure_threshold=3, cooldown_seconds=60.0),
            "HYCOM": CircuitBreaker("HYCOM", failure_threshold=3, cooldown_seconds=60.0),
            "ERA5": CircuitBreaker("ERA5", failure_threshold=3, cooldown_seconds=60.0),
            "OpenMeteo": CircuitBreaker("OpenMeteo", failure_threshold=3, cooldown_seconds=60.0),
            "StaticCache": CircuitBreaker("StaticCache", failure_threshold=1, cooldown_seconds=1.0),
        }
        self.records: Dict[str, Dict[str, Any]] = {}
        self._init_default_records()

    def _init_default_records(self) -> None:
        """Initialize default status records for all known providers."""
        for prov, meta in self.PROVIDER_METADATA.items():
            self.records[prov] = {
                "provider": prov,
                "purpose": meta["purpose"],
                "status": "HEALTHY",
                "last_code": 200,
                "last_latency_ms": None,
                "last_success_utc": None,
                "last_failure_utc": None,
                "last_error_class": None,
                "circuit_breaker": "CLOSED",
                "chain": meta["chain"],
                "active_provider": prov,
            }

    def can_attempt(self, provider: str) -> bool:
        """Check whether provider's circuit breaker allows an attempt."""
        cb = self.circuit_breakers.get(provider)
        return cb.can_execute() if cb else True

    def record_attempt(
        self,
        provider: str,
        success: bool,
        latency_ms: Optional[float] = None,
        error: Optional[Any] = None,
        active_provider: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        """
        Record the outcome of a provider request.
        Updates circuit breaker state and structured status dictionary.
        """
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prov_norm = self._normalize_provider_name(provider)

        if prov_norm not in self.records:
            self.records[prov_norm] = {
                "provider": prov_norm,
                "purpose": "External metocean provider",
                "status": "HEALTHY",
                "last_code": None,
                "last_latency_ms": None,
                "last_success_utc": None,
                "last_failure_utc": None,
                "last_error_class": None,
                "circuit_breaker": "CLOSED",
                "chain": [],
                "active_provider": None,
            }

        rec = self.records[prov_norm]
        rec["last_latency_ms"] = round(latency_ms, 2) if latency_ms is not None else None

        cb = self.circuit_breakers.get(prov_norm)

        if success:
            if cb:
                cb.record_success()
            rec["status"] = "HEALTHY"
            rec["last_code"] = status_code or 200
            rec["last_success_utc"] = now_utc
            rec["last_error_class"] = None
            rec["active_provider"] = active_provider or prov_norm
        else:
            if cb:
                cb.record_failure()
            rec["status"] = "FAILED" if cb and cb.state == CircuitBreakerState.OPEN else "DEGRADED"
            rec["last_code"] = status_code or getattr(error, "status_code", 500)
            rec["last_failure_utc"] = now_utc

            # Extract structured error code
            if hasattr(error, "error_code"):
                rec["last_error_class"] = error.error_code
            elif hasattr(error, "__class__"):
                rec["last_error_class"] = error.__class__.__name__
            else:
                rec["last_error_class"] = "UNKNOWN_ERROR"

            rec["active_provider"] = active_provider

        if cb:
            rec["circuit_breaker"] = cb.state.value

    def _normalize_provider_name(self, provider: str) -> str:
        """Map case-insensitive provider strings to canonical names."""
        p = str(provider).upper().replace("_", "").replace("-", "")
        if "CMEMS" in p or "GLORYS" in p:
            return "CMEMS"
        if "HYCOM" in p:
            return "HYCOM"
        if "ERA5" in p or "CDS" in p:
            return "ERA5"
        if "OPENMETEO" in p:
            return "OpenMeteo"
        if "CACHE" in p or "STATIC" in p:
            return "StaticCache"
        return provider

    def save_to_json(self, filepath: Optional[Union[str, Path]] = None) -> str:
        """Persist status records to provider_status.json."""
        target = Path(filepath) if filepath else self.output_dir / "provider_status.json"
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "providers": self.records,
        }

        with open(target, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return str(target.resolve())

    def get_status(self) -> Dict[str, Any]:
        """Return structured copy of current provider status telemetry."""
        return {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "providers": dict(self.records),
        }

    def get_provider_status(self, provider: str) -> Dict[str, Any]:
        """Return status record for a specific provider."""
        prov_norm = self._normalize_provider_name(provider)
        return self.records.get(prov_norm, {})


# Global status tracker singleton
_GLOBAL_TRACKER = ProviderStatusTracker()


def get_status() -> Dict[str, Any]:
    """Top-level function returning current provider health status dictionary."""
    return _GLOBAL_TRACKER.get_status()
