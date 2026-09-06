"""Loading and validating ``config/aois.yaml`` (design doc v2 §21).

A malformed AOI is caught here, loudly, at load time. The alternative is a
watcher that runs happily against a bbox with lat and lon transposed and opens
investigations over land -- Standing Rule 1 exists because that failure is
invisible until someone reads a map.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "aois.yaml"

#: Applied when `defaults:` omits a key, so the file can be minimal.
BUILTIN_DEFAULTS: Dict[str, Any] = {
    "poll_minutes": 60,
    "lookback_hours": 24,
    "auto_run": True,
    "enabled": True,
}


class AOIConfigError(ValueError):
    """Raised when aois.yaml cannot be trusted. Never downgraded to a warning."""


@dataclass(frozen=True)
class AOI:
    """One registered area of interest."""

    id: str
    name: str
    bbox: Tuple[float, float, float, float]     # lon_min, lat_min, lon_max, lat_max
    ais_region: Optional[str]
    poll_minutes: int
    lookback_hours: int
    auto_run: bool
    enabled: bool
    notes: str = ""

    @property
    def has_real_ais(self) -> bool:
        """False where no public bulk AIS exists, so Stage 6 must synthesise (§8).

        Carried onto every investigation this AOI opens: the provenance badge is
        then a declared property of the AOI, not something discovered when the
        AIS stage quietly falls back.
        """
        return self.ais_region is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "bbox": list(self.bbox),
                "ais_region": self.ais_region, "has_real_ais": self.has_real_ais,
                "poll_minutes": self.poll_minutes,
                "lookback_hours": self.lookback_hours,
                "auto_run": self.auto_run, "enabled": self.enabled,
                "notes": self.notes.strip()}


def _validate_bbox(raw: Any, aoi_id: str) -> Tuple[float, float, float, float]:
    """Check a bbox is four ordered WGS84 numbers in [lon, lat, lon, lat] order.

    Catches: non-numeric values, coordinates out of range, an inverted bbox, and
    the common lat/lon transposition where a longitude above 90 lands in the
    latitude slot.

    Does NOT catch: a transposition where every value happens to stay in range.
    [10.2, 55.2, 12.6, 57.2] transposed is [55.2, 10.2, 57.2, 12.6] -- a
    perfectly valid bbox in the Arabian Sea, indistinguishable from an intended
    one by any numeric test. That one shows up in the AOI's watch state instead:
    `polls` climbs while `scenes_seen` stays at zero, which /api/aois displays.
    Stated here so nobody later assumes this function already handled it.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise AOIConfigError(
            f"aoi '{aoi_id}': bbox must be [lon_min, lat_min, lon_max, lat_max]")
    try:
        lon_min, lat_min, lon_max, lat_max = (float(v) for v in raw)
    except (TypeError, ValueError):
        raise AOIConfigError(f"aoi '{aoi_id}': bbox values must be numbers")

    if not (-180 <= lon_min <= 180 and -180 <= lon_max <= 180):
        raise AOIConfigError(
            f"aoi '{aoi_id}': longitude outside [-180, 180] -- bbox is "
            f"[lon, lat, lon, lat], longitude FIRST (Standing Rule 1)")
    if not (-90 <= lat_min <= 90 and -90 <= lat_max <= 90):
        raise AOIConfigError(
            f"aoi '{aoi_id}': latitude outside [-90, 90] -- lat and lon are "
            f"probably transposed (Standing Rule 1)")
    if lon_min >= lon_max:
        raise AOIConfigError(f"aoi '{aoi_id}': lon_min must be < lon_max "
                             f"(antimeridian-crossing AOIs are not supported)")
    if lat_min >= lat_max:
        raise AOIConfigError(f"aoi '{aoi_id}': lat_min must be < lat_max")
    return lon_min, lat_min, lon_max, lat_max


def _positive_int(value: Any, field: str, aoi_id: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise AOIConfigError(f"aoi '{aoi_id}': {field} must be a whole number")
    if n <= 0:
        raise AOIConfigError(f"aoi '{aoi_id}': {field} must be positive, got {n}")
    return n


def load_aois(path: Optional[Path] = None) -> List[AOI]:
    """Parse and validate the AOI registry.

    Returns every AOI, enabled or not -- the API lists disabled ones so an
    operator can see the off switch is set rather than wondering where an AOI
    went. The watcher does its own filtering.
    """
    path = Path(path) if path else CONFIG_PATH
    if not path.is_file():
        raise AOIConfigError(f"no AOI registry at {path}")

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AOIConfigError(f"{path} is not valid YAML: {exc}")
    if not isinstance(payload, dict):
        raise AOIConfigError(f"{path}: top level must be a mapping")

    defaults = dict(BUILTIN_DEFAULTS)
    defaults.update(payload.get("defaults") or {})

    entries = payload.get("aois")
    if not isinstance(entries, list) or not entries:
        raise AOIConfigError(f"{path}: 'aois' must be a non-empty list")

    aois: List[AOI] = []
    seen: set = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise AOIConfigError(f"{path}: every aoi entry must be a mapping")
        aoi_id = str(raw.get("id") or "").strip()
        if not aoi_id:
            raise AOIConfigError(f"{path}: every aoi needs an 'id'")
        if aoi_id in seen:
            # Duplicate ids would share watch state and each poll would clobber
            # the other's "last scene seen", replaying scenes indefinitely.
            raise AOIConfigError(f"{path}: duplicate aoi id '{aoi_id}'")
        seen.add(aoi_id)

        merged = {**defaults, **raw}
        ais_region = merged.get("ais_region")
        aois.append(AOI(
            id=aoi_id,
            name=str(merged.get("name") or aoi_id),
            bbox=_validate_bbox(merged.get("bbox"), aoi_id),
            ais_region=str(ais_region) if ais_region else None,
            poll_minutes=_positive_int(merged.get("poll_minutes"),
                                       "poll_minutes", aoi_id),
            lookback_hours=_positive_int(merged.get("lookback_hours"),
                                         "lookback_hours", aoi_id),
            auto_run=bool(merged.get("auto_run")),
            enabled=bool(merged.get("enabled")),
            notes=str(merged.get("notes") or ""),
        ))
    return aois


def get_aoi(aoi_id: str, path: Optional[Path] = None) -> Optional[AOI]:
    return next((a for a in load_aois(path) if a.id == aoi_id), None)
