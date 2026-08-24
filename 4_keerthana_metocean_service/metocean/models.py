"""
Data models and input validation schemas for Metocean Service.
Supports strict validation of bounding boxes, UTC time windows, and provider options.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from pydantic import BaseModel, Field, field_validator, model_validator
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore

from metocean.errors import ValidationError


def parse_iso8601_utc(val: Union[str, datetime]) -> datetime:
    """Parse ISO-8601 string and return timezone-aware UTC datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    if not isinstance(val, str):
        raise ValidationError(f"Invalid timestamp type: expected string or datetime, got {type(val).__name__}")

    raw = val.strip()
    # Normalize Z to +00:00
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception as exc:
        raise ValidationError(f"Invalid ISO-8601 timestamp string '{val}': {exc}")


class BBox:
    """Represents and validates an EPSG:4326 Bounding Box [min_lon, min_lat, max_lon, max_lat]."""

    def __init__(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float):
        self.min_lon = float(min_lon)
        self.min_lat = float(min_lat)
        self.max_lon = float(max_lon)
        self.max_lat = float(max_lat)
        self.validate()

    def validate(self) -> None:
        if not (-180.0 <= self.min_lon <= 180.0):
            raise ValidationError(f"min_lon ({self.min_lon}) out of bounds [-180, 180]")
        if not (-180.0 <= self.max_lon <= 180.0):
            raise ValidationError(f"max_lon ({self.max_lon}) out of bounds [-180, 180]")
        if not (-90.0 <= self.min_lat <= 90.0):
            raise ValidationError(f"min_lat ({self.min_lat}) out of bounds [-90, 90]")
        if not (-90.0 <= self.max_lat <= 90.0):
            raise ValidationError(f"max_lat ({self.max_lat}) out of bounds [-90, 90]")
        if self.min_lon > self.max_lon:
            raise ValidationError(f"min_lon ({self.min_lon}) cannot be greater than max_lon ({self.max_lon})")
        if self.min_lat > self.max_lat:
            raise ValidationError(f"min_lat ({self.min_lat}) cannot be greater than max_lat ({self.max_lat})")

    @classmethod
    def from_list_or_tuple(cls, val: Union[List[float], Tuple[float, float, float, float]]) -> "BBox":
        if not isinstance(val, (list, tuple)) or len(val) != 4:
            raise ValidationError(f"BBox must be a list or tuple of 4 floats: [min_lon, min_lat, max_lon, max_lat], got {val}")
        try:
            return cls(float(val[0]), float(val[1]), float(val[2]), float(val[3]))
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"BBox coordinates must be numeric: {exc}")

    def as_list(self) -> List[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    def to_cds_area(self) -> List[float]:
        """ERA5 CDS API expects [North, West, South, East] = [max_lat, min_lon, min_lat, max_lon]."""
        return [self.max_lat, self.min_lon, self.min_lat, self.max_lon]

    def to_cmems_bounds(self) -> Dict[str, float]:
        """Copernicus Marine bounds dictionary."""
        return {
            "minimum_longitude": self.min_lon,
            "maximum_longitude": self.max_lon,
            "minimum_latitude": self.min_lat,
            "maximum_latitude": self.max_lat,
        }

    def __repr__(self) -> str:
        return f"BBox(min_lon={self.min_lon}, min_lat={self.min_lat}, max_lon={self.max_lon}, max_lat={self.max_lat})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, BBox):
            return False
        return (
            abs(self.min_lon - other.min_lon) < 1e-6
            and abs(self.min_lat - other.min_lat) < 1e-6
            and abs(self.max_lon - other.max_lon) < 1e-6
            and abs(self.max_lat - other.max_lat) < 1e-6
        )


class MetoceanRequest:
    """Validated Metocean Data Service Request."""

    VALID_WHAT = {"currents", "wind", "both"}
    VALID_PROVIDERS = {"auto", "cmems", "hycom", "era5", "openmeteo", "cache"}

    def __init__(
        self,
        bbox: Union[BBox, List[float], Tuple[float, float, float, float]],
        start: Union[str, datetime],
        end: Union[str, datetime],
        what: str = "both",
        provider: str = "auto",
        output_dir: Optional[str] = None,
    ):
        if isinstance(bbox, BBox):
            self.bbox = bbox
        else:
            self.bbox = BBox.from_list_or_tuple(bbox)

        self.start_dt: datetime = parse_iso8601_utc(start)
        self.end_dt: datetime = parse_iso8601_utc(end)

        if self.start_dt >= self.end_dt:
            raise ValidationError(
                f"start timestamp ({self.start_dt.isoformat()}) must be strictly earlier than end timestamp ({self.end_dt.isoformat()})"
            )

        what_norm = str(what).lower().strip()
        if what_norm not in self.VALID_WHAT:
            raise ValidationError(f"Invalid 'what' parameter '{what}'. Allowed: {sorted(list(self.VALID_WHAT))}")
        self.what: str = what_norm

        prov_norm = str(provider).lower().strip()
        if prov_norm not in self.VALID_PROVIDERS:
            raise ValidationError(f"Invalid 'provider' parameter '{provider}'. Allowed: {sorted(list(self.VALID_PROVIDERS))}")
        self.provider: str = prov_norm

        self.output_dir: Optional[str] = output_dir

    @property
    def start_iso(self) -> str:
        return self.start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def end_iso(self) -> str:
        return self.end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def is_historical_cmems(self) -> bool:
        """
        Historical routing cutoff rule:
        Requests prior to 2021-01-01 (or multiyear archive) route to GLOBAL_MULTIYEAR_PHY_001_030 (GLORYS12V1).
        Recent / Near-Real-Time requests route to GLOBAL_ANALYSIS_FORECAST_PHY_001_024.
        """
        cutoff = datetime(2021, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        return self.end_dt < cutoff

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bbox": self.bbox.as_list(),
            "start": self.start_iso,
            "end": self.end_iso,
            "what": self.what,
            "provider": self.provider,
            "output_dir": self.output_dir,
        }

    def __repr__(self) -> str:
        return (
            f"MetoceanRequest(bbox={self.bbox.as_list()}, start='{self.start_iso}', "
            f"end='{self.end_iso}', what='{self.what}', provider='{self.provider}')"
        )


class MetoceanResponse:
    """Standardized response from Metocean Service."""

    def __init__(
        self,
        currents_path: Optional[str] = None,
        wind_path: Optional[str] = None,
        providers_used: Optional[Dict[str, str]] = None,
        status: str = "success",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.currents_path = currents_path
        self.wind_path = wind_path
        self.providers_used = providers_used or {}
        self.status = status
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "currents": self.currents_path,
            "wind": self.wind_path,
            "providers_used": self.providers_used,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        return f"MetoceanResponse(status='{self.status}', currents='{self.currents_path}', wind='{self.wind_path}', providers={self.providers_used})"
