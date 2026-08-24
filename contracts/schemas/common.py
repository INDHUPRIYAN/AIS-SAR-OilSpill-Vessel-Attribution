"""
OceanTrace — shared contract primitives.

Everything in contracts/ obeys two laws:
  * coordinates are WGS84 lon/lat  (EPSG:4326)   -> [lon, lat] order, GeoJSON style
  * timestamps are UTC with a 'Z' suffix         -> never local IST time

Import these types instead of re-declaring them, so a change lands everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, List

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, field_validator

# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class ErrorClass(str, Enum):
    """Standard error taxonomy. Every component returns one of these, never a crash."""

    AUTH_FAILED = "AUTH_FAILED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    BAD_RESPONSE = "BAD_RESPONSE"
    NONE = "NONE"  # last call succeeded


class SourceFlag(str, Enum):
    """Provenance of a layer. The UI renders this as a REAL / CACHED / SYNTHETIC badge."""

    REAL = "real"
    CACHED = "cached"
    SYNTHETIC = "synthetic"


class ProviderStatusValue(str, Enum):
    WORKING = "WORKING"
    DEGRADED = "DEGRADED"   # responding but slow, or serving from fallback
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"     # not yet probed


class Engine(str, Enum):
    ML = "ml"
    THRESHOLD_FALLBACK = "threshold_fallback"


class CandidateClass(str, Enum):
    OIL = "oil"
    LOOKALIKE = "lookalike"


# --------------------------------------------------------------------------
# Reusable field types
# --------------------------------------------------------------------------

# [min_lon, min_lat, max_lon, max_lat]
BBox = Annotated[List[float], Field(min_length=4, max_length=4)]

# [lon, lat]  -- GeoJSON order. NOT [lat, lon].
LonLat = Annotated[List[float], Field(min_length=2, max_length=2)]

Score = Annotated[float, Field(ge=0.0, le=1.0)]

CRS_WGS84 = "EPSG:4326"


def to_utc_z(dt: datetime) -> str:
    """Serialise any datetime as an ISO-8601 UTC string ending in Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ContractModel(BaseModel):
    """Base for every contract model: strict about unknown fields, UTC on the way out."""

    model_config = ConfigDict(
        extra="forbid",          # an unexpected key is a contract breach, not a warning
        populate_by_name=True,
    )


# Timestamps: must arrive timezone-aware, always serialise back as '...Z'.
UTCDateTime = Annotated[
    AwareDatetime,
    PlainSerializer(to_utc_z, return_type=str, when_used="json"),
]


class UTCMixin:
    """Reusable validator: reject naive datetimes, normalise everything to UTC."""

    @staticmethod
    def _as_utc(v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC (ends with 'Z')")
        return v.astimezone(timezone.utc)


def validate_bbox(bbox: List[float]) -> List[float]:
    """Sanity-check a WGS84 bbox: ordered, in range, non-degenerate."""
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180):
        raise ValueError(f"longitude out of order/range: {bbox}")
    if not (-90 <= min_lat < max_lat <= 90):
        raise ValueError(f"latitude out of order/range: {bbox}")
    return bbox


def validate_lonlat(pt: List[float]) -> List[float]:
    """Catch the classic [lat, lon] swap early."""
    lon, lat = pt[0], pt[1]
    if not -180 <= lon <= 180:
        raise ValueError(f"longitude {lon} out of range — did you pass [lat, lon]?")
    if not -90 <= lat <= 90:
        raise ValueError(f"latitude {lat} out of range — did you pass [lat, lon]?")
    return pt


class CRSMixin(BaseModel):
    """Mixin for models carrying an explicit CRS: only WGS84 is legal in contracts."""

    crs: str = CRS_WGS84

    @field_validator("crs")
    @classmethod
    def _wgs84_only(cls, v: str) -> str:
        if v.upper() != CRS_WGS84:
            raise ValueError(f"contracts are {CRS_WGS84} only; got {v}. Reproject at ingest.")
        return CRS_WGS84
