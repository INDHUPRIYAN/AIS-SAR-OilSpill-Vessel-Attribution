"""Pydantic contracts for the four files this module produces.

slick.geojson        Engine A
origin_cloud.geojson Engine B (hindcast)
forecast.geojson     Engine B (forecast)
suspects.json        Engine C
"""

from .forecast import ForecastCollection, validate_forecast
from .origin_cloud import OriginCloudCollection, validate_origin_cloud
from .slick import SlickCollection, SlickFeature, SlickProperties, validate_slick
from .suspects import SuspectsDocument, validate_suspects

__all__ = [
    "SlickCollection", "SlickFeature", "SlickProperties", "validate_slick",
    "OriginCloudCollection", "validate_origin_cloud",
    "ForecastCollection", "validate_forecast",
    "SuspectsDocument", "validate_suspects",
]
