"""
Metocean Service Error Taxonomy & Custom Exceptions.
Provides machine-readable error codes and structured error classes.
"""

from typing import Any, Dict, Optional


class MetoceanError(Exception):
    """Base exception for all metocean service operations."""

    error_code: str = "UNKNOWN_ERROR"

    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider or "metocean"
        if error_code:
            self.error_code = error_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to a structured dictionary for JSON telemetry."""
        return {
            "error_code": self.error_code,
            "provider": self.provider,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} code={self.error_code} provider={self.provider}: {self.message}>"


class AuthFailedError(MetoceanError):
    """Raised when authentication with an external provider fails."""
    error_code = "AUTH_FAILED"


class LicenceNotAcceptedError(MetoceanError):
    """Raised when user terms or terms of service/license are not accepted."""
    error_code = "LICENCE_NOT_ACCEPTED"


class NoDataForPeriodError(MetoceanError):
    """Raised when provider has no dataset for the requested date/time window."""
    error_code = "NO_DATA_FOR_PERIOD"


class TimeoutError(MetoceanError):
    """Raised when request to provider or download times out."""
    error_code = "TIMEOUT"


class UnavailableError(MetoceanError):
    """Raised when provider endpoint or THREDDS/OPeNDAP server is unreachable."""
    error_code = "UNAVAILABLE"


class BadResponseError(MetoceanError):
    """Raised when provider returns an unparseable or corrupted response."""
    error_code = "BAD_RESPONSE"


class RateLimitedError(MetoceanError):
    """Raised when provider HTTP 429 or quota limit is encountered."""
    error_code = "RATE_LIMITED"


class ValidationError(MetoceanError):
    """Raised when request input bbox, dates, or parameters are invalid."""
    error_code = "VALIDATION_ERROR"


class CacheCorruptedError(MetoceanError):
    """Raised when local cache NetCDF file is unreadable or truncated."""
    error_code = "CACHE_CORRUPTED"
