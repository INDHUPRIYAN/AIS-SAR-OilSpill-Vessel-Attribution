"""Validating an operator-drawn AOI before it becomes a watched area.

`aoi.py` validates the bbox numbers that come out of `aois.yaml`. That is the
right check for a file a developer edits and reviews. It is not enough for a
polygon drawn on a map by someone who may have dragged the wrong corner, pasted
a GeoJSON in the wrong coordinate order, or outlined a city.

The checks here are deliberately shallow and deliberately loud:

* **structure** -- a Polygon with a closed exterior ring of at least four
  positions, because that is what the scene search and the map both expect;
* **range** -- longitude before latitude, the frozen convention everywhere in
  this codebase, checked so that a swapped pair fails here rather than
  producing an AOI that quietly matches no scenes forever;
* **size** -- an AOI smaller than a Sentinel-1 pixel or larger than a basin is
  almost always a mistake, and an empty watch is indistinguishable from a
  working one until someone notices `polls` climbing with `scenes_seen` at zero;
* **at sea** -- an AOI entirely over land can never contain an oil slick, so
  registering one means the operator drew in the wrong place.

The land test uses `global-land-mask`, the same package the calibration land
mask uses, so the two agree about where the coast is. It samples a grid rather
than testing the polygon exactly: this is a sanity check meant to catch "you
have outlined Kansas", not a coastline-accurate GIS operation, and it says so
rather than implying more precision than it has.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

# A Sentinel-1 IW frame is ~250 km across. An AOI larger than this in either
# direction is not a mistake we can rule out, but it is worth refusing to
# register silently -- the scene search would return every pass over a basin.
MAX_SPAN_DEG = 20.0
# Below this the bbox is smaller than a few GRD pixels and no scene footprint
# will ever be judged to "cover" it in a useful way.
MIN_SPAN_DEG = 0.01
# Grid resolution for the at-sea sample. 12x12 over the bbox is enough to find
# water in any AOI that has a usable amount of it.
SEA_SAMPLES = 12


class AoiGeometryError(ValueError):
    """The drawn AOI cannot be watched, and the message says why."""


def _numbers(pair: Any) -> Tuple[float, float]:
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        raise AoiGeometryError(
            "each position must be [longitude, latitude]; got "
            f"{pair!r}")
    try:
        return float(pair[0]), float(pair[1])
    except (TypeError, ValueError):
        raise AoiGeometryError(f"non-numeric coordinate in {pair!r}")


def validate_polygon(geometry: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Check a GeoJSON Polygon and return its exterior ring as (lon, lat)."""
    if not isinstance(geometry, dict):
        raise AoiGeometryError("aoi must be a GeoJSON geometry object")

    kind = geometry.get("type")
    if kind == "Feature":
        # Accept a Feature and unwrap it: a drawing tool exports one, and
        # rejecting it would be pedantry rather than safety.
        return validate_polygon(geometry.get("geometry") or {})
    if kind != "Polygon":
        raise AoiGeometryError(
            f"aoi geometry must be a Polygon, got {kind!r}. Multi-part areas "
            "are not supported: the scene search takes one footprint.")

    rings = geometry.get("coordinates")
    if not isinstance(rings, (list, tuple)) or not rings:
        raise AoiGeometryError("aoi polygon has no coordinates")

    ring = [_numbers(p) for p in rings[0]]
    if len(ring) < 4:
        raise AoiGeometryError(
            f"aoi exterior ring needs at least 4 positions, got {len(ring)}")
    if ring[0] != ring[-1]:
        raise AoiGeometryError(
            "aoi exterior ring is not closed: the first and last positions "
            "must be identical")

    for lon, lat in ring:
        if not -180.0 <= lon <= 180.0:
            raise AoiGeometryError(
                f"longitude {lon} is outside [-180, 180]. Positions are "
                "[longitude, latitude] -- a swapped pair lands here.")
        if not -90.0 <= lat <= 90.0:
            raise AoiGeometryError(
                f"latitude {lat} is outside [-90, 90]. Positions are "
                "[longitude, latitude] -- a swapped pair lands here.")
    return ring


def bbox_of(ring: Sequence[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def check_span(bbox: Tuple[float, float, float, float]) -> None:
    lon_min, lat_min, lon_max, lat_max = bbox
    width, height = lon_max - lon_min, lat_max - lat_min
    if width < MIN_SPAN_DEG or height < MIN_SPAN_DEG:
        raise AoiGeometryError(
            f"aoi is {width:.4f} x {height:.4f} deg, smaller than the "
            f"{MIN_SPAN_DEG} deg minimum. No scene footprint would usefully "
            "cover it, so the watch would poll forever and see nothing.")
    if width > MAX_SPAN_DEG or height > MAX_SPAN_DEG:
        raise AoiGeometryError(
            f"aoi is {width:.1f} x {height:.1f} deg, larger than the "
            f"{MAX_SPAN_DEG} deg maximum. Every Sentinel-1 pass over the "
            "basin would open an investigation.")


def sea_fraction(bbox: Tuple[float, float, float, float]) -> float:
    """Fraction of a sampled grid over the bbox that is sea.

    Returns -1.0 when the land mask is unavailable, which the caller must treat
    as "not tested" rather than as "all land" -- refusing an AOI because an
    optional dependency is missing would be its own kind of dishonesty.
    """
    try:
        from global_land_mask import globe
    except Exception:                                   # noqa: BLE001
        return -1.0

    lon_min, lat_min, lon_max, lat_max = bbox
    n = SEA_SAMPLES
    sea = 0
    for i in range(n):
        lat = lat_min + (lat_max - lat_min) * (i + 0.5) / n
        for j in range(n):
            lon = lon_min + (lon_max - lon_min) * (j + 0.5) / n
            try:
                if not bool(globe.is_land(lat, lon)):
                    sea += 1
            except Exception:                           # noqa: BLE001
                return -1.0
    return sea / float(n * n)


def check_at_sea(bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
    """Refuse an AOI with no water in it. Report what was measured either way."""
    fraction = sea_fraction(bbox)
    if fraction < 0:
        return {"tested": False,
                "note": "global-land-mask is not installed, so the at-sea "
                        "check did not run. The AOI was accepted untested."}
    if fraction == 0.0:
        raise AoiGeometryError(
            f"every one of the {SEA_SAMPLES * SEA_SAMPLES} sampled points "
            "inside this AOI is over land. An oil slick cannot appear here, "
            "so the area is almost certainly drawn in the wrong place.")
    return {"tested": True, "sea_fraction": round(fraction, 4)}


def validate_aoi_geometry(geometry: Dict[str, Any]) -> Dict[str, Any]:
    """Full check. Returns {bbox, ring, sea}. Raises AoiGeometryError."""
    ring = validate_polygon(geometry)
    bbox = bbox_of(ring)
    check_span(bbox)
    sea = check_at_sea(bbox)
    return {"bbox": list(bbox), "ring": ring, "sea": sea}
