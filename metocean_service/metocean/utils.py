"""
Utility functions for Coordinate normalization, Vector math, and Grid helpers.
Supports WGS84 EPSG:4326 standards, longitude normalization [-180, 180],
and meteorological vector decompositions.
"""

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union


def normalize_longitude(lon: float) -> float:
    """
    Normalize any longitude to the standard WGS84 EPSG:4326 range [-180.0, 180.0].
    Handles 0..360 degrees coordinates from ocean models like HYCOM.
    """
    lon = float(lon)
    # Wrap to [-180, 180]
    normalized = (lon + 180.0) % 360.0 - 180.0
    # Boundary check for +180
    if normalized == -180.0 and lon > 0:
        return 180.0
    return round(normalized, 6)


def lon_to_360(lon: float) -> float:
    """
    Convert standard WGS84 longitude [-180.0, 180.0] to [0.0, 360.0]
    as required by HYCOM GOFS OPeNDAP servers.
    """
    lon = float(lon)
    return lon % 360.0


def wind_speed_dir_to_uv(speed: float, direction_deg: float) -> Tuple[float, float]:
    """
    Convert meteorological wind speed (m/s) and wind direction (degrees from North)
    into eastward (u) and northward (v) velocity vector components.

    Meteorological convention:
    Direction is WHERE the wind blows FROM (0° = North, 90° = East, 180° = South, 270° = West).
    Vector convention:
    u = eastward velocity (positive = blowing toward East)
    v = northward velocity (positive = blowing toward North)

    Formula:
    u = -speed * sin(rad(direction))
    v = -speed * cos(rad(direction))
    """
    speed = float(speed)
    dir_rad = math.radians(float(direction_deg))

    u = -speed * math.sin(dir_rad)
    v = -speed * math.cos(dir_rad)

    return (round(u, 4), round(v, 4))


def uv_to_speed_dir(u: float, v: float) -> Tuple[float, float]:
    """
    Convert eastward (u) and northward (v) velocity components to
    speed (m/s) and meteorological direction (degrees from North).
    """
    u = float(u)
    v = float(v)

    speed = math.sqrt(u * u + v * v)
    # Meteorological direction (from where wind blows)
    dir_rad = math.atan2(-u, -v)
    dir_deg = math.degrees(dir_rad) % 360.0

    return (round(speed, 4), round(dir_deg, 2))


def ensure_dir(path: Union[str, Path]) -> Path:
    """Ensure directory exists and return Path object."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def generate_hourly_timestamps(start: datetime, end: datetime) -> List[datetime]:
    """Generate a list of UTC hourly timestamps between start and end (inclusive)."""
    current = start.astimezone(timezone.utc)
    target_end = end.astimezone(timezone.utc)
    timestamps = []

    while current <= target_end:
        timestamps.append(current)
        current += timedelta(hours=1)

    return timestamps
