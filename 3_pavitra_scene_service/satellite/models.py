"""Data models for Satellite Scene Service (Sentinel-1 SAR acquisition).

Defines Pydantic v2 data models conforming to OceanTrace shared contracts
for scene metadata, bounding boxes, search results, provider health,
and retrieval responses.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class GeoBoundingBox(BaseModel):
    """Represents a geographic bounding box [W, S, E, N]."""

    min_lon: float = Field(..., description="Westernmost longitude (-180 to 180)")
    min_lat: float = Field(..., description="Southernmost latitude (-90 to 90)")
    max_lon: float = Field(..., description="Easternmost longitude (-180 to 180)")
    max_lat: float = Field(..., description="Northernmost latitude (-90 to 90)")

    @field_validator("min_lon", "max_lon")
    @classmethod
    def validate_longitude(cls, v: float) -> float:
        if not -180.0 <= v <= 180.0:
            raise ValueError(f"Longitude must be between -180 and 180 degrees, got {v}")
        return float(v)

    @field_validator("min_lat", "max_lat")
    @classmethod
    def validate_latitude(cls, v: float) -> float:
        if not -90.0 <= v <= 90.0:
            raise ValueError(f"Latitude must be between -90 and 90 degrees, got {v}")
        return float(v)

    @model_validator(mode="after")
    def validate_bounds_order(self) -> "GeoBoundingBox":
        if self.min_lon > self.max_lon:
            raise ValueError(
                f"min_lon ({self.min_lon}) cannot be greater than max_lon ({self.max_lon})"
            )
        if self.min_lat > self.max_lat:
            raise ValueError(
                f"min_lat ({self.min_lat}) cannot be greater than max_lat ({self.max_lat})"
            )
        return self

    def to_list(self) -> List[float]:
        """Returns coordinates as [W, S, E, N] -> [min_lon, min_lat, max_lon, max_lat]."""
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    @classmethod
    def from_list(cls, coords: List[float]) -> "GeoBoundingBox":
        """Instantiates a GeoBoundingBox from a 4-element list [min_lon, min_lat, max_lon, max_lat]."""
        if len(coords) != 4:
            raise ValueError(
                f"Expected exactly 4 coordinates [min_lon, min_lat, max_lon, max_lat], got {len(coords)}"
            )
        return cls(min_lon=coords[0], min_lat=coords[1], max_lon=coords[2], max_lat=coords[3])

    def to_wkt(self) -> str:
        """Generates simple 2D Polygon Well-Known Text (WKT)."""
        return (
            f"POLYGON(({self.min_lon} {self.min_lat}, {self.max_lon} {self.min_lat}, "
            f"{self.max_lon} {self.max_lat}, {self.min_lon} {self.max_lat}, "
            f"{self.min_lon} {self.min_lat}))"
        )


class SceneMetadata(BaseModel):
    """Standardized Sentinel-1 SAR Scene Metadata matching project contract."""

    scene_id: str = Field(..., description="Unique Sentinel-1 scene identifier")
    platform: Optional[str] = Field(
        default="Sentinel-1", description="Satellite platform (e.g., Sentinel-1A, Sentinel-1B)"
    )
    acquisition_time: datetime = Field(..., description="UTC timestamp of scene acquisition")
    bbox: Union[GeoBoundingBox, List[float]] = Field(
        ..., description="Geographic bounding box as GeoBoundingBox or [min_lon, min_lat, max_lon, max_lat]"
    )
    product_type: Optional[str] = Field(
        default=None, description="SAR product type (e.g., GRD, SLC, OCN)"
    )
    polarisation: Optional[str] = Field(
        default=None, description="Polarisation channels (e.g., VV, VH, VV+VH, HH, HV)"
    )
    orbit_direction: Optional[str] = Field(
        default=None, description="Orbit direction (ASCENDING, DESCENDING)"
    )
    file_path: Optional[str] = Field(
        default=None, description="Local filesystem path to downloaded/cached scene file"
    )
    checksum: Optional[str] = Field(
        default=None, description="SHA-256 or MD5 checksum of downloaded/cached file"
    )
    file_size_bytes: Optional[int] = Field(
        default=None, description="File size in bytes"
    )
    download_url: Optional[str] = Field(
        default=None, description="Source provider direct download URL"
    )

    # --- frozen-contract fields -------------------------------------------
    # scene_meta.json is what detection and every downstream stage read. The
    # fields above are provider bookkeeping the contract does not care about;
    # these are the ones it requires. db_range in particular is not optional
    # in practice: /detect compares it against the dB range the model was
    # trained on, and a mismatch is the silent SAR domain-gap failure.
    crs: str = Field(default="EPSG:4326", description="Contract requires WGS84")
    db_range: Optional[List[float]] = Field(
        default=None,
        description="[db_min, db_max] Sigma0 clip range baked into the GeoTIFF, e.g. [-35.0, 0.0]",
    )
    provider_used: Optional[str] = Field(
        default=None, description="CDSE | ASF | LocalCache — which chain member served this"
    )
    source: str = Field(default="real", description="real | cached | synthetic — drives the UI badge")
    pixel_spacing_m: Optional[float] = Field(default=None, description="Ground pixel size")

    @field_validator("acquisition_time", mode="after")
    @classmethod
    def validate_utc_datetime(cls, v: datetime) -> datetime:
        """Ensures acquisition_time is UTC timezone-aware."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("bbox", mode="before")
    @classmethod
    def validate_bbox_input(cls, v: Any) -> Any:
        """Converts raw 4-element list/tuple into GeoBoundingBox if provided."""
        if isinstance(v, (list, tuple)):
            if len(v) != 4:
                raise ValueError(
                    f"bbox list must contain exactly 4 numbers [min_lon, min_lat, max_lon, max_lat], got {len(v)}"
                )
            return GeoBoundingBox.from_list(list(v))
        return v

    @property
    def bbox_list(self) -> List[float]:
        """Convenience property to obtain [min_lon, min_lat, max_lon, max_lat]."""
        if isinstance(self.bbox, GeoBoundingBox):
            return self.bbox.to_list()
        return list(self.bbox)

    def to_contract(self, db_range: Optional[List[float]] = None) -> Dict[str, Any]:
        """Emit `scene_meta.json` exactly as the frozen contract defines it.

        This model carries provider bookkeeping (checksum, download URL, file
        size) that the rest of the pipeline neither needs nor understands. The
        contract is the narrower, stable shape everyone downstream reads, so
        translation happens here rather than leaking provider details across
        the boundary.

        Field names differ deliberately: the contract uses `acquired_utc`, not
        `acquisition_time`. Renaming inside this method keeps the service's own
        vocabulary intact while still honouring the contract.
        """
        rng = db_range or self.db_range or [-35.0, 0.0]
        payload: Dict[str, Any] = {
            "scene_id": self.scene_id,
            "acquired_utc": self.acquisition_time.astimezone(timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bbox": self.bbox_list,
            "crs": self.crs,
            "db_range": [float(rng[0]), float(rng[1])],
            "file_path": self.file_path or "",
            "provider_used": self.provider_used or "unknown",
            "source": self.source,
        }
        if self.pixel_spacing_m is not None:
            payload["pixel_spacing_m"] = self.pixel_spacing_m
        if self.polarisation is not None:
            payload["polarisation"] = self.polarisation
        return payload

    def write_contract(self, path: Any, db_range: Optional[List[float]] = None) -> Any:
        """Write `scene_meta.json` to disk. The unit downstream stages consume."""
        import json
        from pathlib import Path as _Path

        p = _Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_contract(db_range), indent=2), encoding="utf-8")
        return p


class SceneSearchResult(BaseModel):
    """Collection/result container for candidate satellite scenes matching a query."""

    query_bbox: Optional[GeoBoundingBox] = Field(default=None, description="Query bounding box")
    query_start: Optional[datetime] = Field(default=None, description="Query start timestamp (UTC)")
    query_end: Optional[datetime] = Field(default=None, description="Query end timestamp (UTC)")
    total_count: int = Field(default=0, description="Total number of matching scenes found")
    scenes: List[SceneMetadata] = Field(default_factory=list, description="List of matching scene metadata records")
    provider: Optional[str] = Field(default=None, description="Query provider source (e.g. CDSE, ASF, CACHE)")


class ProviderHealth(BaseModel):
    """Health and connectivity status of an upstream satellite scene provider."""

    provider_name: str = Field(..., description="Provider identifier (e.g., CDSE, ASF, LOCAL_CACHE)")
    is_available: bool = Field(..., description="True if provider is reachable and responding successfully")
    status: str = Field(..., description="Standardized status string: UP, DOWN, DEGRADED, UNCONFIGURED")
    latency_ms: Optional[float] = Field(default=None, description="Round-trip response latency in milliseconds")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Diagnostic and health probe metadata")


class RetrievalResponse(BaseModel):
    """Result of a scene retrieval attempt returned by the satellite acquisition pipeline."""

    success: bool = Field(..., description="True if scene was successfully acquired/retrieved")
    scene_id: str = Field(..., description="Target Sentinel-1 scene identifier")
    source_provider: Optional[str] = Field(
        default=None, description="Provider that satisfied request: CACHE, CDSE, ASF, DEMO"
    )
    metadata: Optional[SceneMetadata] = Field(default=None, description="Validated scene metadata")
    geotiff_path: Optional[str] = Field(
        default=None, description="Filesystem path to validated Sentinel-1 GeoTIFF"
    )
    error_message: Optional[str] = Field(
        default=None, description="Detailed error description if retrieval failed"
    )
