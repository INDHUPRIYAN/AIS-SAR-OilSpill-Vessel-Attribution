"""Engine B - Drift: hindcast (and, from Phase 4, forecast).

    from engines.drift import hindcast
    status = hindcast("slick.geojson", "origin_cloud.geojson",
                      currents_path="currents.nc", wind_path="wind.nc")
"""

from .runner import hindcast

__all__ = ["hindcast"]
