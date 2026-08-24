"""
OceanTrace contract schemas — the law of the project.

    from contracts.schemas import SceneMeta, DetectResponse, SlickCollection, ...

If your output validates against these models, integration works without touching your
code. Changing a frozen field requires team sign-off.
"""

from .common import (
    BBox,
    CandidateClass,
    ContractModel,
    CRS_WGS84,
    Engine,
    ErrorClass,
    LonLat,
    ProviderStatusValue,
    Score,
    SourceFlag,
    to_utc_z,
    validate_bbox,
    validate_lonlat,
)
from .geo import (
    ForecastCollection,
    ForecastFeature,
    ForecastProperties,
    OriginCloud,
    OriginFeature,
    OriginMetadata,
    SlickCollection,
    SlickFeature,
    SlickProperties,
)
from .scene import Candidate, DetectRequest, DetectResponse, SceneMeta
from .tabular import (
    Evidence,
    FilteredVessel,
    ProviderStatus,
    ProviderStatusFile,
    REQUIRED_VESSEL_COLUMNS,
    SubScores,
    Suspect,
    SuspectsReport,
    VESSEL_COLUMNS,
    VESSEL_TYPES,
    validate_vessels_df,
)

# name -> (model, mock filename).  The main system uses this table to validate any
# contract file generically: CONTRACTS["slick"][0].model_validate_json(text)
CONTRACTS = {
    "scene_meta": (SceneMeta, "scene_meta.json"),
    "detect": (DetectResponse, "detect_response.json"),
    "slick": (SlickCollection, "slick.geojson"),
    "origin_cloud": (OriginCloud, "origin_cloud.geojson"),
    "forecast": (ForecastCollection, "forecast.geojson"),
    "suspects": (SuspectsReport, "suspects.json"),
    "provider_status": (ProviderStatusFile, "provider_status.json"),
    # vessels.parquet is tabular: validated with validate_vessels_df(), not a model
}

__all__ = [n for n in dir() if not n.startswith("_")]
