"""The three filtering gates, applied before any vessel reaches the scoring stage.

Handbook §4.7 / §6 Phase 5:

    1. Spatial    - the track must intersect the buffered high-probability origin region
    2. Temporal   - the vessel must be present within the origin window +/- a buffer
    3. Trajectory - its course must be roughly compatible with the slick's major axis,
                    since a discharge trails behind a moving vessel

A vessel failing any gate is *excluded with the reason recorded*, never silently
dropped: the UI shows "filtered out: outside time window".

One refinement of the handbook wording
--------------------------------------
§4.7 defines the temporal gate as "presence within the origin time window +/- buffer".
Read literally - any fix at all inside the window - it filters almost nothing, because
AIS tracks are continuous and practically every vessel in a regional extract is
transmitting during the window; it just happens to be somewhere else. The gate here
therefore asks the operationally meaningful question: was the vessel *in the origin
region* during the window. Both counts are reported in the metrics so the stricter and
looser readings stay visible.

Where the slick axis comes from
-------------------------------
The trajectory gate needs the slick's orientation, but Engine C's contract inputs are
only ``origin_cloud.geojson`` and ``vessels.parquet`` - ``slick.geojson`` is not among
them, and requiring it would be a contract change needing team sign-off. It is not
needed: the particles at ``timestep_h == 0`` are the seeded slick itself, so the
orientation of *their* covariance ellipse is the slick's major axis. Engine A's
authoritative value can still be supplied when it is on hand.

Everything geometric happens in a local metric frame. Buffering in degrees would stretch
a 5 km buffer differently along latitude and longitude (handbook pitfall #3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from ..common.errors import missing_input
from ..common.geo import LocalFrame, covariance_ellipse
from ..common.timeutil import parse_utc

# Reported in handbook order, so the reason a vessel was dropped is deterministic.
REASON_SPATIAL = "outside origin region"
REASON_TEMPORAL = "outside time window"
REASON_TRAJECTORY = "course incompatible with slick axis"


@dataclass
class GateConfig:
    spatial_buffer_km: float = 5.0
    temporal_buffer_min: float = 90.0
    max_axis_offset_deg: float = 45.0

    @classmethod
    def from_config(cls, cfg: dict | None) -> "GateConfig":
        cfg = dict(cfg or {})
        return cls(**{k: float(v) for k, v in cfg.items() if k in cls.__dataclass_fields__})


@dataclass
class OriginContext:
    """Everything the gates need from ``origin_cloud.geojson``."""

    frame: LocalFrame
    region_m: Any = field(repr=False)         # buffered high-probability region, metres
    start_s: float = 0.0
    end_s: float = 0.0
    peak_s: float = 0.0
    axis_deg: float | None = None
    axis_source: str = "origin_cloud"
    engine_used: str | None = None


@dataclass
class GateResult:
    passed: bool
    filter_reason: str | None
    failed: list[str]
    metrics: dict[str, Any]


def _window_feature(document: dict[str, Any]) -> dict[str, Any]:
    for feature in document.get("features", []):
        if (feature.get("properties") or {}).get("kind") == "origin_window":
            return feature
    raise missing_input(
        "origin_cloud.geojson has no 'origin_window' feature; Engine C cannot gate "
        "without a time window"
    )


def _seeded_particles(document: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Particle positions at ``timestep_h == 0`` - i.e. the slick as it was observed."""
    lons, lats = [], []
    for feature in document.get("features", []):
        properties = feature.get("properties") or {}
        if properties.get("kind"):
            continue
        if abs(float(properties.get("timestep_h", -1))) < 1e-9:
            lon, lat = feature["geometry"]["coordinates"]
            lons.append(lon)
            lats.append(lat)
    return np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)


def slick_axis_from_cloud(document: dict[str, Any]) -> float | None:
    """Recover the slick's major-axis bearing from the seeded particle cloud."""
    lons, lats = _seeded_particles(document)
    if lons.size < 3:
        return None
    frame = LocalFrame(float(lats.mean()), float(lons.mean()))
    x, y = frame.to_metres(lons, lats)
    _, _, orientation = covariance_ellipse(x, y)
    return orientation


def _to_metres(geometry, frame: LocalFrame):
    return shapely_transform(lambda xs, ys, z=None: frame.to_metres(xs, ys), geometry)


def build_origin_context(
    document: dict[str, Any],
    config: GateConfig,
    *,
    slick_axis_deg: float | None = None,
) -> tuple[OriginContext, list[str]]:
    """Assemble the gating context from an origin cloud document."""
    warnings: list[str] = []
    window = _window_feature(document)["properties"]
    start_s = parse_utc(window["start_utc"]).timestamp()
    end_s = parse_utc(window["end_utc"]).timestamp()
    peak_s = parse_utc(window["peak_utc"]).timestamp()

    # The high-probability region: the confidence ellipses that fall inside the origin
    # window, which is where the discharge is actually believed to have happened.
    in_window, all_ellipses = [], []
    for feature in document.get("features", []):
        properties = feature.get("properties") or {}
        if properties.get("kind") != "confidence_ellipse":
            continue
        geometry = shape(feature["geometry"])
        all_ellipses.append(geometry)
        stamp = properties.get("time_utc")
        if stamp and start_s <= parse_utc(stamp).timestamp() <= end_s:
            in_window.append(geometry)

    ellipses = in_window or all_ellipses
    if not ellipses:
        raise missing_input(
            "origin_cloud.geojson has no confidence-ellipse features; there is no "
            "origin region to gate against"
        )
    if not in_window:
        warnings.append(
            "no confidence ellipse falls inside the origin window; gating against every "
            "timestep instead, which widens the spatial gate"
        )

    centroid = unary_union(ellipses).centroid
    frame = LocalFrame(centroid.y, centroid.x)
    region_m = unary_union([_to_metres(e, frame) for e in ellipses]).buffer(
        config.spatial_buffer_km * 1000.0
    )

    axis_source = "engine_a"
    if slick_axis_deg is None:
        slick_axis_deg = slick_axis_from_cloud(document)
        axis_source = "origin_cloud"
        if slick_axis_deg is None:
            warnings.append(
                "could not derive the slick axis from the origin cloud; the trajectory "
                "gate is skipped and every vessel passes it"
            )
            axis_source = "unavailable"

    return (
        OriginContext(
            frame=frame,
            region_m=region_m,
            start_s=start_s,
            end_s=end_s,
            peak_s=peak_s,
            axis_deg=slick_axis_deg,
            axis_source=axis_source,
            engine_used=window.get("engine_used"),
        ),
        warnings,
    )


def axis_offset_deg(course_deg: float, axis_deg: float) -> float:
    """Angle between a course and an undirected axis, folded to [0, 90].

    A vessel can run either way along the slick's axis and still be compatible with it,
    so 200 deg against a 20 deg axis is an offset of 0, not 180.
    """
    difference = abs(course_deg - axis_deg) % 180.0
    return min(difference, 180.0 - difference)


def apply_gates(track, origin: OriginContext, config: GateConfig) -> GateResult:
    """Run all three gates, reporting every failure and the first as the reason."""
    failed: list[str] = []
    metrics: dict[str, Any] = {}

    # --- spatial -------------------------------------------------------------------
    geometry_m = _to_metres(track.line(), origin.frame)
    distance_m = geometry_m.distance(origin.region_m)
    metrics["distance_to_region_km"] = round(distance_m / 1000.0, 3)
    if not geometry_m.intersects(origin.region_m):
        failed.append(REASON_SPATIAL)

    # --- temporal ------------------------------------------------------------------
    buffer_s = config.temporal_buffer_min * 60.0
    lo, hi = origin.start_s - buffer_s, origin.end_s + buffer_s
    in_window = (track.times_s >= lo) & (track.times_s <= hi)
    metrics["fixes_in_window"] = int(in_window.sum())

    in_region = _in_region_mask(track, origin)
    metrics["fixes_in_region"] = int(in_region.sum())
    if in_region.any():
        relevant = in_region
    else:
        # The track crosses the region only between two fixes - during an AIS gap, for
        # instance. Fall back to the closest approach so a vessel that went dark over
        # the origin is not handed a free pass.
        relevant = np.zeros_like(in_window)
        relevant[_closest_index(track, origin)] = True

    overlap = in_window & relevant
    metrics["fixes_in_region_and_window"] = int(overlap.sum())
    if not overlap.any():
        failed.append(REASON_TEMPORAL)
        when = track.times_s[relevant]
        gap_s = min(abs(when.min() - hi), abs(when.max() - lo))
        metrics["hours_outside_window"] = round(gap_s / 3600.0, 2)

    # --- trajectory ----------------------------------------------------------------
    if origin.axis_deg is None:
        metrics["axis_offset_deg"] = None
    else:
        index = _closest_index(track, origin)
        course = track.course_at(index)
        metrics["course_deg"] = None if course is None else round(course, 1)
        if course is None:
            metrics["axis_offset_deg"] = None
        else:
            offset = axis_offset_deg(course, origin.axis_deg)
            metrics["axis_offset_deg"] = round(offset, 1)
            if offset > config.max_axis_offset_deg:
                failed.append(REASON_TRAJECTORY)

    return GateResult(
        passed=not failed,
        filter_reason=failed[0] if failed else None,
        failed=failed,
        metrics=metrics,
    )


def _in_region_mask(track, origin: OriginContext) -> np.ndarray:
    """Which fixes fall inside the buffered origin region."""
    x, y = origin.frame.to_metres(track.lons, track.lats)
    from shapely import contains_xy

    return np.asarray(contains_xy(origin.region_m, x, y), dtype=bool)


def _closest_index(track, origin: OriginContext) -> int:
    """Index of the fix nearest the origin region - where a discharge would happen."""
    x, y = origin.frame.to_metres(track.lons, track.lats)
    centre = origin.region_m.centroid
    return int(np.argmin(np.hypot(x - centre.x, y - centre.y)))
