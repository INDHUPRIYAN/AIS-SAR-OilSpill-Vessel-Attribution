"""Engine C - Attribution: origin cloud + vessels -> ranked, explainable suspects.

Phase 5 (implemented): filtering gates.
Phase 6 (next): weighted scoring, explanation generator, suspects.json.
"""

from .gates import GateConfig, apply_gates, build_origin_context
from .tracks import VesselTrack, load_vessels

__all__ = [
    "load_vessels",
    "VesselTrack",
    "GateConfig",
    "apply_gates",
    "build_origin_context",
]
