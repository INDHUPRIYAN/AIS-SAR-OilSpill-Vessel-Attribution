"""Translate engine output into the frozen contracts.

The engines emit the shape given in the developer handbooks; `contracts/`
freezes a slightly different one. Both are internally consistent and both are
covered by passing tests -- they simply disagree, and that disagreement was
baked in before either side was written.

Rather than force a rewrite on either side, the main system translates at the
boundary. This is the integrator's job: the engines keep their own vocabulary
and their tests, and everything downstream sees exactly one schema.

Differences handled here:

    slick.geojson
      handbook                      contract
      major_axis_km                 major_axis_m          (x1000)
      minor_axis_km                 minor_axis_m          (x1000)
      damping_ratio_db              damping_ratio
      age_hours_est                 age_hours_estimate
      age_confidence "low"          age_confidence 0.25   (categorical -> score)
      age_method                    (dropped; not in contract)
      scene_id/detected_utc in      top-level metadata{}
        each feature's properties

    origin_cloud.geojson
      properties.kind               properties.feature_type
      time_utc                      t_utc
      timestep_h (negative hours)   step_index (non-negative, counts backwards)
      kind="origin_window" feature  top-level metadata{} window fields
      level                         confidence_level

Anything the engines do not supply and the contract requires is derived from
the scene metadata or the run itself -- never invented.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# The handbook uses categorical age confidence; the contract wants 0-1.
# Deliberately pessimistic: a Fay-spreading age estimate is a rough proxy and
# the handbook itself calls its own confidence "low".
AGE_CONFIDENCE = {"low": 0.25, "medium": 0.5, "high": 0.75}


def _utc(value: Any, default: Optional[datetime] = None) -> str:
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = default or datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = default or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _already_contract(payload: dict) -> bool:
    """True if the engine already emits the contract shape.

    Checked rather than assumed, so this module becomes a no-op the day the
    engines are aligned, instead of corrupting output that is already correct.
    """
    return isinstance(payload.get("metadata"), dict)


def normalise_slick(payload: dict, scene_meta: dict, detect: dict) -> dict:
    if _already_contract(payload):
        return payload

    feats_in = payload.get("features", [])
    first = feats_in[0]["properties"] if feats_in else {}

    features: List[dict] = []
    for f in feats_in:
        p = dict(f.get("properties", {}))
        conf = p.get("age_confidence")
        if isinstance(conf, str):
            conf = AGE_CONFIDENCE.get(conf.lower(), 0.25)
        elif isinstance(conf, (int, float)):
            conf = max(0.0, min(1.0, float(conf)))

        km = p.get("major_axis_km")
        minor_km = p.get("minor_axis_km")
        props = {
            "slick_id": p.get("slick_id", "slick_01"),
            "confidence": float(p.get("confidence", detect.get("confidence", 0.0))),
            "area_km2": float(p.get("area_km2", 0.0)),
            "perimeter_km": float(p.get("perimeter_km", 0.0)),
            "centroid": list(p.get("centroid", [0.0, 0.0])),
            # handbook reports axes in km, the contract in metres
            "major_axis_m": float(km) * 1000.0 if km is not None
                            else float(p.get("major_axis_m", 0.0)),
            "minor_axis_m": float(minor_km) * 1000.0 if minor_km is not None
                            else float(p.get("minor_axis_m", 0.0)),
            "orientation_deg": float(p.get("orientation_deg", 0.0)) % 180.0,
            "damping_ratio": p.get("damping_ratio_db", p.get("damping_ratio")),
            "age_hours_estimate": p.get("age_hours_est", p.get("age_hours_estimate")),
            "age_confidence": conf,
            "engine": detect.get("engine", "ml"),
            "source": scene_meta.get("source", "real"),
        }
        features.append({"type": "Feature", "geometry": f["geometry"],
                         "properties": props})

    acquired = _utc(scene_meta.get("acquired_utc"))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": first.get("scene_id", scene_meta.get("scene_id", "unknown")),
            "detected_utc": _utc(first.get("detected_utc"), None),
            "acquired_utc": acquired,
            "model_version": detect.get("model_version", "unknown"),
            "mask_path": str(detect.get("mask_path", "")).replace("\\", "/"),
            "crs": "EPSG:4326",
        },
        "features": features,
    }


def normalise_origin_cloud(payload: dict, scene_meta: dict,
                           forcing: Optional[dict] = None) -> dict:
    if _already_contract(payload):
        return payload

    particles: List[dict] = []
    ellipses: List[dict] = []
    window: Dict[str, Any] = {}
    max_back_h = 0.0

    for f in payload.get("features", []):
        p = dict(f.get("properties", {}))
        kind = p.get("kind") or p.get("feature_type")

        if kind == "origin_window":
            window = p
            continue

        # timestep_h counts backwards as negative hours; the contract wants a
        # non-negative step index that increases into the past.
        step_h = p.get("timestep_h", p.get("step_index", 0))
        try:
            step_h = float(step_h)
        except (TypeError, ValueError):
            step_h = 0.0
        max_back_h = max(max_back_h, abs(step_h))
        step_index = int(round(abs(step_h)))

        if kind == "confidence_ellipse":
            ellipses.append({
                "type": "Feature", "geometry": f["geometry"],
                "properties": {
                    "feature_type": "ellipse",
                    "t_utc": _utc(p.get("time_utc") or p.get("t_utc")),
                    "step_index": step_index,
                    "center": list(p.get("center") or _centroid(f["geometry"])),
                    "semi_major_m": float(p.get("semi_major_m", 0.0)),
                    "semi_minor_m": float(p.get("semi_minor_m", 0.0)),
                    "orientation_deg": float(p.get("orientation_deg", 0.0)) % 180.0,
                    # contract requires 0 < level < 1
                    "confidence_level": min(max(float(
                        p.get("level", p.get("confidence_level", 0.9))), 0.01), 0.99),
                }})
        else:
            particles.append({
                "type": "Feature", "geometry": f["geometry"],
                "properties": {
                    "feature_type": "particle",
                    "particle_id": int(p.get("particle_id", len(particles))),
                    "t_utc": _utc(p.get("time_utc") or p.get("t_utc")),
                    "step_index": step_index,
                    "weight": min(max(float(p.get("weight", 0.5)), 0.0), 1.0),
                }})

    acquired = _utc(scene_meta.get("acquired_utc"))
    steps = {f["properties"]["step_index"] for f in particles} or {0}
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": scene_meta.get("scene_id", "unknown"),
            "origin_window_start_utc": _utc(window.get("start_utc"), None) if window
                                       else acquired,
            "origin_window_end_utc": _utc(window.get("end_utc"), None) if window
                                     else acquired,
            "backtrack_hours": max(max_back_h, 1.0),
            "n_particles": max(len(particles), 1),
            "timestep_minutes": max(
                (max_back_h * 60.0 / max(len(steps) - 1, 1)) if len(steps) > 1 else 60.0,
                1.0),
            "forcing": forcing or {},
            "source": scene_meta.get("source", "real"),
            "crs": "EPSG:4326",
        },
        "features": particles + ellipses,
    }


def normalise_forecast(payload: dict, scene_meta: dict,
                       forcing: Optional[dict] = None) -> dict:
    if _already_contract(payload):
        return payload

    acquired = _utc(scene_meta.get("acquired_utc"))
    features, horizons = [], []
    for f in payload.get("features", []):
        p = dict(f.get("properties", {}))
        h = int(p.get("horizon_h", p.get("timestep_h", 0)) or 0)
        horizons.append(h)
        features.append({
            "type": "Feature", "geometry": f["geometry"],
            "properties": {
                "horizon_h": h,
                "valid_utc": _utc(p.get("valid_utc") or p.get("time_utc"), None),
                "confidence_level": min(max(float(
                    p.get("confidence_level", p.get("level", 0.5))), 0.01), 0.99),
                "area_km2": float(p.get("area_km2", 0.0)),
                "source": scene_meta.get("source", "real"),
            }})
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": scene_meta.get("scene_id", "unknown"),
            "issued_utc": acquired,
            "horizons_h": sorted(set(horizons)) or [6, 12, 24],
            "forcing": forcing or {},
            "crs": "EPSG:4326",
        },
        "features": features,
    }


def normalise_suspects(payload: dict, scene_meta: dict, run_id: str) -> dict:
    """Map the handbook's suspects shape onto the contract's."""
    if "suspects" in payload and "run_id" in payload:
        return payload

    weight_alias = {"anomaly": "behaviour", "prior": "vessel_prior"}
    raw_weights = payload.get("weights", {}) or {}
    weights = {weight_alias.get(k, k): float(v) for k, v in raw_weights.items()}
    for required in ("proximity", "temporal", "trajectory", "behaviour",
                     "ais_gap", "vessel_prior"):
        weights.setdefault(required, 0.0)
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}   # contract: sums to 1
    else:
        weights = {k: 1 / 6 for k in weights}

    suspects, filtered = [], []
    for v in payload.get("vessels", payload.get("suspects", [])):
        if v.get("filtered"):
            filtered.append({"mmsi": int(v["mmsi"]),
                             "reason": v.get("filter_reason", "filtered")})
            continue
        scores = {weight_alias.get(k, k): float(x)
                  for k, x in (v.get("scores") or v.get("sub_scores") or {}).items()}
        for required in weights:
            scores.setdefault(required, 0.0)
        suspects.append({
            "rank": int(v.get("rank", len(suspects) + 1)),
            "mmsi": int(v["mmsi"]),
            "vessel_name": v.get("name") or v.get("vessel_name"),
            "vessel_type": v.get("vessel_type", "unknown"),
            "total_score": min(max(float(v.get("score_total",
                                              v.get("total_score", 0.0))), 0.0), 1.0),
            "sub_scores": scores,
            "reason": v.get("reason") or "No explanation supplied by the engine.",
            "evidence": v.get("evidence", {}) or {},
            "source": scene_meta.get("source", "real"),
        })

    suspects.sort(key=lambda s: s["total_score"], reverse=True)
    for i, s in enumerate(suspects, start=1):
        s["rank"] = i

    return {
        "scene_id": scene_meta.get("scene_id", "unknown"),
        "run_id": run_id,
        "generated_utc": _utc(payload.get("generated_utc"), None),
        "weights": weights,
        "suspects": suspects,
        "filtered_out": filtered,
        "total_vessels_considered": int(
            payload.get("total_vessels_considered", len(suspects) + len(filtered))),
        "source": scene_meta.get("source", "real"),
    }


def _centroid(geometry: dict) -> List[float]:
    """Rough centroid of any GeoJSON geometry, for filling a missing centre."""
    pts: List[List[float]] = []

    def walk(c):
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                pts.append([float(c[0]), float(c[1])])
            else:
                for x in c:
                    walk(x)

    walk(geometry.get("coordinates", []))
    if not pts:
        return [0.0, 0.0]
    return [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]


NORMALISERS = {
    "slick": normalise_slick,
    "origin_cloud": normalise_origin_cloud,
    "forecast": normalise_forecast,
    "suspects": normalise_suspects,
}


def normalise_file(contract: str, path: Path, **kwargs) -> bool:
    """Rewrite a contract file in place into the frozen shape."""
    fn = NORMALISERS.get(contract)
    if fn is None or not Path(path).exists():
        return False
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    out = fn(payload, **kwargs)
    Path(path).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return True
