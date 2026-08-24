"""Engine C - Attribution: origin cloud + vessels -> ranked, explainable suspects.

    from engines.attribution import attribute
    status = attribute("origin_cloud.geojson", "vessels.parquet", "suspects.json")
"""

from .gates import GateConfig, apply_gates, build_origin_context
from .runner import attribute
from .scoring import ScoringConfig, score_vessel
from .tracks import VesselTrack, load_vessels

__all__ = [
    "attribute",
    "load_vessels",
    "VesselTrack",
    "GateConfig",
    "apply_gates",
    "build_origin_context",
    "ScoringConfig",
    "score_vessel",
]
