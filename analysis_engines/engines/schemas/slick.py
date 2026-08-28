"""Pydantic contract for ``slick.geojson`` (Engine A output).

Frozen by handbook §4.2. Field names and types are the law; example values there are
illustrative. Engine A validates every file it writes against this model, so a contract
break fails in my own tests rather than during Indhu's integration.

    { "type": "FeatureCollection",
      "features": [ { "type": "Feature",
                      "geometry": { "type": "Polygon", "coordinates": [...] },
                      "properties": { ...SlickProperties... } } ] }
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..common.timeutil import parse_utc


class Polygon(BaseModel):
    """GeoJSON Polygon in EPSG:4326, coordinates ordered [lon, lat]."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]

    @field_validator("coordinates")
    @classmethod
    def _rings_are_closed(cls, rings: list[list[list[float]]]) -> list[list[list[float]]]:
        if not rings:
            raise ValueError("polygon must have at least an exterior ring")
        for i, ring in enumerate(rings):
            if len(ring) < 4:
                raise ValueError(f"ring {i} needs at least 4 positions, got {len(ring)}")
            if ring[0] != ring[-1]:
                raise ValueError(f"ring {i} is not closed (first position != last)")
            for pos in ring:
                if len(pos) != 2:
                    raise ValueError("positions must be [lon, lat] pairs")
                lon, lat = pos
                if not -180.0 <= lon <= 180.0:
                    raise ValueError(f"longitude {lon} outside [-180, 180] - is this EPSG:4326?")
                if not -90.0 <= lat <= 90.0:
                    raise ValueError(f"latitude {lat} outside [-90, 90] - is this EPSG:4326?")
        return rings


class SlickProperties(BaseModel):
    """Per-slick properties, handbook §4.2."""

    model_config = ConfigDict(extra="allow")  # optional shape descriptors may be added

    slick_id: str
    scene_id: str
    detected_utc: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    area_km2: float = Field(ge=0.0)
    perimeter_km: float = Field(ge=0.0)
    centroid: list[float] = Field(description="[lon, lat] in EPSG:4326")

    major_axis_km: float = Field(ge=0.0)
    minor_axis_km: float = Field(ge=0.0)
    orientation_deg: float = Field(
        ge=0.0, lt=180.0,
        description="Major-axis bearing from true north, clockwise, folded to [0, 180).",
    )

    damping_ratio_db: float | None = None
    age_hours_est: float | None = Field(default=None, ge=0.0)
    age_method: str | None = None
    age_confidence: Literal["low", "medium", "high"] | None = None

    @field_validator("detected_utc")
    @classmethod
    def _utc(cls, v: str) -> str:
        parse_utc(v, field="detected_utc")  # raises if naive or malformed
        return v

    @field_validator("centroid")
    @classmethod
    def _centroid_lonlat(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            raise ValueError("centroid must be [lon, lat]")
        lon, lat = v
        if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
            raise ValueError(f"centroid {v} is not a valid EPSG:4326 [lon, lat]")
        return v


class SlickFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"]
    geometry: Polygon
    properties: SlickProperties


class SlickCollection(BaseModel):
    """The whole ``slick.geojson`` document."""

    model_config = ConfigDict(extra="allow")

    type: Literal["FeatureCollection"]
    features: list[SlickFeature]


def validate_slick(document: dict[str, Any]) -> SlickCollection:
    """Validate a slick.geojson payload; raises ``pydantic.ValidationError`` if broken.

    Engine A calls this immediately before writing, so an off-contract file is never
    produced in the first place.
    """
    return SlickCollection.model_validate(document)
