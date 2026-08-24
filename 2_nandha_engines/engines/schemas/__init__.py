"""Pydantic contracts for the four files this module produces.

slick.geojson        Engine A   (implemented)
origin_cloud.geojson Engine B   (implemented)
forecast.geojson     Engine B   (Phase 4)
suspects.json        Engine C   (Phase 6)
"""

from .origin_cloud import OriginCloudCollection, validate_origin_cloud
from .slick import SlickCollection, SlickFeature, SlickProperties, validate_slick

__all__ = [
    "SlickCollection",
    "SlickFeature",
    "SlickProperties",
    "validate_slick",
    "OriginCloudCollection",
    "validate_origin_cloud",
]
