"""
Contract 1 — scene_meta.json      (produced by Pavitra, consumed by detection + UI)
Contract 2 — POST /detect I/O     (produced by Indhu, consumed by Engine A + UI)
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import Field, field_validator

from .common import (
    BBox,
    CandidateClass,
    ContractModel,
    CRSMixin,
    Engine,
    Score,
    SourceFlag,
    UTCDateTime,
    validate_bbox,
)


class SceneMeta(ContractModel, CRSMixin):
    """Metadata accompanying one calibrated Sentinel-1 GeoTIFF (Sigma0 dB, single band)."""

    scene_id: str = Field(min_length=3, description="Unique scene identifier, e.g. S1A_IW_GRDH_...")
    acquired_utc: UTCDateTime = Field(description="SAR acquisition time, UTC")
    bbox: BBox = Field(description="[min_lon, min_lat, max_lon, max_lat] in EPSG:4326")
    db_range: Annotated[List[float], Field(min_length=2, max_length=2)] = Field(
        description="[db_min, db_max] clip range baked into the GeoTIFF, e.g. [-35.0, 0.0]"
    )
    file_path: str = Field(description="Path to the calibrated Sigma0 dB GeoTIFF")
    provider_used: str = Field(description="CDSE | ASF | ESA | LocalCache | ...")
    source: SourceFlag = SourceFlag.REAL
    pixel_spacing_m: Optional[float] = Field(default=None, gt=0, description="Ground pixel size")
    polarisation: Optional[str] = Field(default=None, description="VV | VH | HH | HV")
    incidence_angle_band: Optional[int] = Field(
        default=None, ge=1, description="1-based band index of the incidence-angle band, if present"
    )

    @field_validator("bbox")
    @classmethod
    def _bbox_ok(cls, v: List[float]) -> List[float]:
        return validate_bbox(v)

    @field_validator("db_range")
    @classmethod
    def _db_ok(cls, v: List[float]) -> List[float]:
        if v[0] >= v[1]:
            raise ValueError("db_range must be [min, max] with min < max")
        return v


# --------------------------------------------------------------------------
# /detect
# --------------------------------------------------------------------------


class DetectRequest(ContractModel):
    scene_path: str
    scene_id: str
    mode: Literal["full", "tile"] = "full"


class Candidate(ContractModel):
    """One detected object. Look-alikes are reported, not silently dropped — that is the
    whole point of the DARTIS screening stage and it is what the judges ask about."""

    bbox: BBox
    class_: CandidateClass = Field(alias="class")
    score: Score
    phenomenon: Optional[str] = Field(
        default=None,
        description="For look-alikes: low_wind | internal_wave | biogenic_film | rain_cell | eddy | rfi",
    )

    @field_validator("bbox")
    @classmethod
    def _bbox_ok(cls, v: List[float]) -> List[float]:
        return validate_bbox(v)


class DetectResponse(ContractModel):
    """Frozen output of the detection service. Engine A reads mask_path + candidates only —
    never model internals."""

    scene_id: str
    mask_path: str = Field(description="Georeferenced 0/1 GeoTIFF on the same grid as the scene")
    confidence: Score = Field(description="0.0 when nothing was detected")
    candidates: List[Candidate] = Field(default_factory=list)
    model_version: str = Field(description="e.g. unet-r34-v1.2+yolo11n-v1.0, or 'none' for fallback")
    engine: Engine = Engine.ML
    runtime_ms: Optional[int] = Field(default=None, ge=0)
