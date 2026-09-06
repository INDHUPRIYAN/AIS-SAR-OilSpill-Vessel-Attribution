"""Typed provider error taxonomy for the satellite scene service.

The frozen contracts (contracts/schemas/common.py ErrorClass) define the
standard error taxonomy every component must speak: AUTH_FAILED, TIMEOUT,
RATE_LIMITED, UNAVAILABLE, BAD_RESPONSE. The adapters historically raised
bare ``RuntimeError`` which the chain (and provider_status.json) could not
classify. These exceptions subclass ``RuntimeError`` on purpose so every
existing ``except RuntimeError`` / ``assertRaises(RuntimeError)`` call site
keeps working while gaining a machine-readable ``error_class``.
"""

from typing import Optional


class ProviderError(RuntimeError):
    """Base class for a classified provider failure.

    Attributes:
        error_class: one of the frozen-contract ErrorClass string values.
        provider: which provider raised it ("CDSE", "ASF", ...), if known.
    """

    error_class: str = "UNAVAILABLE"

    def __init__(self, message: str, provider: Optional[str] = None):
        super().__init__(message)
        self.provider = provider


class AuthFailedError(ProviderError):
    """Credentials rejected, token expired, or 401/403 responses."""

    error_class = "AUTH_FAILED"


class ProviderTimeoutError(ProviderError):
    """The provider did not answer within the timeout."""

    error_class = "TIMEOUT"


class RateLimitedError(ProviderError):
    """HTTP 429 or an explicit throttling response."""

    error_class = "RATE_LIMITED"


class UnavailableError(ProviderError):
    """Network unreachable, DNS failure, or 5xx responses."""

    error_class = "UNAVAILABLE"


class BadResponseError(ProviderError):
    """The provider answered, but with something unusable (bad JSON,
    empty file, checksum mismatch, unexpected schema)."""

    error_class = "BAD_RESPONSE"


def classify_http_status(status_code: int, message: str, provider: Optional[str] = None) -> ProviderError:
    """Map an HTTP status code onto the typed taxonomy."""
    if status_code in (401, 403):
        return AuthFailedError(message, provider)
    if status_code == 429:
        return RateLimitedError(message, provider)
    if status_code == 408:
        return ProviderTimeoutError(message, provider)
    if status_code >= 500:
        return UnavailableError(message, provider)
    return BadResponseError(message, provider)


def error_class_of(err: BaseException) -> str:
    """Return the contract ErrorClass string for any exception."""
    if isinstance(err, ProviderError):
        return err.error_class
    if isinstance(err, TimeoutError):
        return "TIMEOUT"
    name = type(err).__name__.lower()
    if "timeout" in name:
        return "TIMEOUT"
    return "UNAVAILABLE"
