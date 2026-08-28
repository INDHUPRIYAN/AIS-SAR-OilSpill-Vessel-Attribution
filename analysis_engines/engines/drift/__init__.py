"""Engine B - Drift: hindcast and forecast.

    from engines.drift import hindcast, forecast
    status = hindcast("slick.geojson", "origin_cloud.geojson",
                      currents_path="currents.nc", wind_path="wind.nc")
    status = forecast("slick.geojson", "forecast.geojson",
                      currents_path="currents.nc", wind_path="wind.nc")
"""

from .runner import forecast, hindcast

__all__ = ["hindcast", "forecast"]
