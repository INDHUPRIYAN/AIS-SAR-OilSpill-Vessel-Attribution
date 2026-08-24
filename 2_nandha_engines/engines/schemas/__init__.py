"""Pydantic contracts for the four files this module produces.

slick.geojson        Engine A   (implemented)
origin_cloud.geojson Engine B   (Phase 2)
forecast.geojson     Engine B   (Phase 4)
suspects.json        Engine C   (Phase 6)
"""

from .slick import SlickCollection, SlickFeature, SlickProperties, validate_slick

__all__ = ["SlickCollection", "SlickFeature", "SlickProperties", "validate_slick"]
