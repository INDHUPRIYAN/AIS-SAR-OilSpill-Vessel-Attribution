"""Pydantic contract for ``origin_cloud.geojson`` (Engine B hindcast output).

Frozen by handbook §4.3. The file mixes three kinds of feature in one collection:

* **particle** - Point, ``{time_utc, weight 0-1, timestep_h}``
* **confidence_ellipse** - Polygon, ``{kind, level, timestep_h}``
* **origin_window** - Point, ``{kind, start_utc, end_utc, peak_utc, engine_used}``

Validation is by ``kind``: a feature without one is a particle, which is how the
handbook's example writes them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..common.timeutil import parse_utc
from .slick import Polygon


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"]
    coordinates: list[float]

    @field_validator("coordinates")
    @classmethod
    def _lonlat(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            raise ValueError("Point coordinates must be [lon, lat]")
        lon, lat = v
        if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
            raise ValueError(f"{v} is not a valid EPSG:4326 [lon, lat]")
        return v


def _utc(value: str, field: str) -> str:
    parse_utc(value, field=field)
    return value


class ParticleProperties(BaseModel):
    model_config = ConfigDict(extra="allow")

    time_utc: str
    weight: float = Field(ge=0.0, le=1.0)
    timestep_h: float

    @field_validator("time_utc")
    @classmethod
    def _t(cls, v: str) -> str:
        return _utc(v, "time_utc")


class EllipseProperties(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["confidence_ellipse"]
    level: float = Field(gt=0.0, lt=1.0)
    timestep_h: float
    time_utc: str | None = None


class OriginWindowProperties(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["origin_window"]
    start_utc: str
    end_utc: str
    peak_utc: str
    engine_used: str

    @field_validator("start_utc", "end_utc", "peak_utc")
    @classmethod
    def _t(cls, v: str) -> str:
        return _utc(v, "origin window time")


class OriginCloudCollection(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["FeatureCollection"]
    features: list[dict[str, Any]]

    @field_validator("features")
    @classmethod
    def _features(cls, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_window = False
        for index, feature in enumerate(features):
            if feature.get("type") != "Feature":
                raise ValueError(f"feature {index} is not a GeoJSON Feature")
            geometry = feature.get("geometry") or {}
            properties = feature.get("properties") or {}
            kind = properties.get("kind")

            if kind == "origin_window":
                Point.model_validate(geometry)
                OriginWindowProperties.model_validate(properties)
                seen_window = True
            elif kind == "confidence_ellipse":
                Polygon.model_validate(geometry)
                EllipseProperties.model_validate(properties)
            else:
                Point.model_validate(geometry)
                ParticleProperties.model_validate(properties)

        if not seen_window:
            raise ValueError(
                "origin_cloud.geojson must carry exactly one 'origin_window' summary "
                "feature (handbook §4.3)"
            )
        return features


def validate_origin_cloud(document: dict[str, Any]) -> OriginCloudCollection:
    """Validate an origin_cloud.geojson payload; raises ValidationError if broken."""
    return OriginCloudCollection.model_validate(document)
