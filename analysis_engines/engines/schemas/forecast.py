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
        ge=0.0, description="ellipse area at the horizon / at seeding, same level"
    )
    level: float | None = Field(default=None, gt=0.0, lt=1.0)
    confidence_level: float | None = Field(
        default=None, gt=0.0, lt=1.0,
        description="same value as `level`, under the frozen contract's field name "
        "(contracts/schemas/tabular-adjacent geo.py: ForecastProperties)",
    )
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
    def _one_per_horizon_and_level(
        cls, features: list[ForecastFeature]
    ) -> list[ForecastFeature]:
        """One polygon per (horizon, confidence level), ascending.

        The frozen contract carries a per-feature confidence level, so a horizon may
        legitimately appear once per level (e.g. 0.5 and 0.9); duplicates of the same
        pair are still an error. Legacy single-level files (no ``level``) keep their
        old one-per-horizon rule, because every pair then collapses to the horizon.
        """
        if not features:
            raise ValueError("forecast.geojson must carry at least one horizon")
        keys = [
            (
                f.properties.horizon_h,
                -1.0 if f.properties.level is None else f.properties.level,
            )
            for f in features
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate forecast (horizon_h, level) pairs: {keys}")
        if keys != sorted(keys):
            raise ValueError(
                "forecast features must be written in ascending (horizon_h, level) order"
            )
        return features


def validate_forecast(document: dict[str, Any]) -> ForecastCollection:
    """Validate a forecast.geojson payload; raises ValidationError if broken."""
    return ForecastCollection.model_validate(document)
