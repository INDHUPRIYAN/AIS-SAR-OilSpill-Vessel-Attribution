"""
Contract 3 — slick.geojson        (Detection + Engine A -> Drift, UI)
Contract 4 — origin_cloud.geojson (Engine B hindcast  -> Engine C, UI)
Contract 5 — forecast.geojson     (Engine B forecast  -> UI)

All three are GeoJSON FeatureCollections so QGIS / MapLibre / geopandas can open them
directly. The pipeline-specific fields live under `properties`; collection-level metadata
lives under a top-level `metadata` object (legal GeoJSON — foreign members are allowed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .common import (
    ContractModel,
    LonLat,
    Score,
    SourceFlag,
    UTCDateTime,
    validate_lonlat,
)


def _flatten_coords(coords) -> List[List[float]]:
    """Return every [lon, lat] pair in a nested GeoJSON coordinate structure."""
    if not isinstance(coords, list):
        return []
    if coords and isinstance(coords[0], (int, float)):
        return [list(coords[:2])]
    out: List[List[float]] = []
    for c in coords:
        out.extend(_flatten_coords(c))
    return out


class Geometry(ContractModel):
    type: Literal["Point", "Polygon", "MultiPolygon", "LineString"]
    coordinates: Any = Field(description="GeoJSON coordinates, [lon, lat] order, EPSG:4326")


# --------------------------------------------------------------------------
# slick.geojson
# --------------------------------------------------------------------------


class SlickProperties(ContractModel):
    slick_id: str
    confidence: Score
    area_km2: float = Field(ge=0)
    perimeter_km: float = Field(ge=0)
    centroid: LonLat
    major_axis_m: float = Field(ge=0, description="Best-fit ellipse major axis")
    minor_axis_m: float = Field(ge=0)
    orientation_deg: float = Field(ge=0, lt=180, description="Major-axis bearing, 0=N, clockwise")
    damping_ratio: Optional[float] = Field(
        default=None, description="Mean sea dB minus mean slick dB; age/thickness proxy"
    )
    age_hours_estimate: Optional[float] = Field(default=None, ge=0)
    age_confidence: Optional[Score] = Field(
        default=None, description="Low is expected and honest — Fay spreading is a rough proxy"
    )
    engine: str = Field(default="ml", description="ml | threshold_fallback")
    source: SourceFlag = SourceFlag.REAL

    @field_validator("centroid")
    @classmethod
    def _pt(cls, v: List[float]) -> List[float]:
        return validate_lonlat(v)


class SlickFeature(ContractModel):
    type: Literal["Feature"] = "Feature"
    geometry: Geometry
    properties: SlickProperties

    @model_validator(mode="after")
    def _centroid_inside_geometry(self) -> "SlickFeature":
        """The centroid must sit inside the polygon's own extent. This is the cheapest
        possible detector for a [lat, lon] swap or a CRS mix-up upstream."""
        pts = _flatten_coords(self.geometry.coordinates)
        if not pts:
            return self
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        c_lon, c_lat = self.properties.centroid
        pad = 1e-6
        if not (min(lons) - pad <= c_lon <= max(lons) + pad and
                min(lats) - pad <= c_lat <= max(lats) + pad):
            raise ValueError(
                f"centroid {self.properties.centroid} lies outside its own polygon extent "
                f"[{min(lons):.4f}, {min(lats):.4f}, {max(lons):.4f}, {max(lats):.4f}] "
                "— check for a [lat, lon] swap or a wrong CRS"
            )
        return self


class SlickMetadata(ContractModel):
    scene_id: str
    detected_utc: UTCDateTime
    acquired_utc: UTCDateTime = Field(description="Scene acquisition time — the drift clock starts here")
    model_version: str
    mask_path: Optional[str] = None
    crs: str = "EPSG:4326"


class SlickCollection(ContractModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: SlickMetadata
    features: List[SlickFeature]


# --------------------------------------------------------------------------
# origin_cloud.geojson
# --------------------------------------------------------------------------


class ParticleProperties(ContractModel):
    feature_type: Literal["particle"] = "particle"
    particle_id: int = Field(ge=0)
    t_utc: UTCDateTime = Field(description="Backtracked position time (earlier than acquisition)")
    step_index: int = Field(ge=0, description="0 = at acquisition time, increasing backwards")
    weight: float = Field(ge=0, le=1, description="Particle probability weight")


class EllipseProperties(ContractModel):
    feature_type: Literal["ellipse"] = "ellipse"
    t_utc: UTCDateTime
    step_index: int = Field(ge=0)
    center: LonLat
    semi_major_m: float = Field(ge=0)
    semi_minor_m: float = Field(ge=0)
    orientation_deg: float = Field(ge=0, lt=180)
    confidence_level: float = Field(gt=0, lt=1, description="e.g. 0.5, 0.9")

    @field_validator("center")
    @classmethod
    def _pt(cls, v: List[float]) -> List[float]:
        return validate_lonlat(v)


class OriginFeature(ContractModel):
    type: Literal["Feature"] = "Feature"
    geometry: Geometry
    properties: Dict[str, Any]

    @field_validator("properties")
    @classmethod
    def _discriminate(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        kind = v.get("feature_type")
        if kind == "particle":
            ParticleProperties(**v)
        elif kind == "ellipse":
            EllipseProperties(**v)
        else:
            raise ValueError("properties.feature_type must be 'particle' or 'ellipse'")
        return v


class OriginMetadata(ContractModel):
    scene_id: str
    origin_window_start_utc: UTCDateTime = Field(description="Earliest plausible discharge time")
    origin_window_end_utc: UTCDateTime = Field(description="Latest plausible discharge time")
    backtrack_hours: float = Field(gt=0)
    n_particles: int = Field(gt=0)
    timestep_minutes: float = Field(gt=0)
    forcing: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance of the physics, e.g. {'currents':'CMEMS','wind':'ERA5','windage':0.03}",
    )
    source: SourceFlag = SourceFlag.REAL
    crs: str = "EPSG:4326"


class OriginCloud(ContractModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: OriginMetadata
    features: List[OriginFeature]


# --------------------------------------------------------------------------
# forecast.geojson
# --------------------------------------------------------------------------


class ForecastProperties(ContractModel):
    horizon_h: int = Field(description="+6 / +12 / +24 hours from acquisition")
    valid_utc: UTCDateTime
    confidence_level: float = Field(gt=0, lt=1, description="Contour probability, e.g. 0.5 / 0.9")
    area_km2: float = Field(ge=0)
    source: SourceFlag = SourceFlag.REAL


class ForecastFeature(ContractModel):
    type: Literal["Feature"] = "Feature"
    geometry: Geometry
    properties: ForecastProperties


class ForecastMetadata(ContractModel):
    scene_id: str
    issued_utc: UTCDateTime
    horizons_h: List[int]
    forcing: Dict[str, Any] = Field(default_factory=dict)
    crs: str = "EPSG:4326"


class ForecastCollection(ContractModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: ForecastMetadata
    features: List[ForecastFeature]
