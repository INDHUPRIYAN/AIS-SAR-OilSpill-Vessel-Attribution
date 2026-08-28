"""Pydantic contract for ``forecast.geojson`` (Engine B forward output).

Handbook §4.3: "one predicted-extent Polygon per horizon with
``{ "horizon_h": 6|12|24, "uncertainty_growth": ... }``".

``uncertainty_growth`` is left as "..." in the handbook; Engine B defines it as the
ratio of the 90% ellipse area at the horizon to the same area at seeding, so it is
dimensionless and starts near 1. See ``engines/drift/forecast.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..common.timeutil import parse_utc
from .slick import Polygon


class ForecastProperties(BaseModel):
    model_config = ConfigDict(extra="allow")

    horizon_h: float = Field(gt=0.0, description="hours ahead of detection")
    uncertainty_growth: float = Field(
        ge=0.0, description="90% ellipse area at the horizon / at seeding"
    )
    level: float | None = Field(default=None, gt=0.0, lt=1.0)
    time_utc: str | None = None
    area_km2: float | None = Field(default=None, ge=0.0)
    engine_used: str | None = None

    @field_validator("time_utc")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        if v is not None:
            parse_utc(v, field="time_utc")
        return v


class ForecastFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"]
    geometry: Polygon
    properties: ForecastProperties


class ForecastCollection(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["FeatureCollection"]
    features: list[ForecastFeature]

    @field_validator("features")
    @classmethod
    def _one_per_horizon(cls, features: list[ForecastFeature]) -> list[ForecastFeature]:
        if not features:
            raise ValueError("forecast.geojson must carry at least one horizon")
        horizons = [f.properties.horizon_h for f in features]
        if len(set(horizons)) != len(horizons):
            raise ValueError(f"duplicate forecast horizons: {horizons}")
        if horizons != sorted(horizons):
            raise ValueError("forecast horizons must be written in ascending order")
        return features


def validate_forecast(document: dict[str, Any]) -> ForecastCollection:
    """Validate a forecast.geojson payload; raises ValidationError if broken."""
    return ForecastCollection.model_validate(document)
