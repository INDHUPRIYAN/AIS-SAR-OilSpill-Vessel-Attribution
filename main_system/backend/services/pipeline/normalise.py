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
                                    + age_confidence_label "low" (kept: the UI
                                      must render LOW, not infer it from 0.25)
      age_method                    age_method            (carried; an inversion
                                      of assumed thickness must say so)
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


def _age_label(value: Any) -> Optional[str]:
    """The engine's categorical age confidence, preserved as a word.

    Flattening 'low' to 0.25 loses the label the UI must render (the truth
    rules require age to show LOW wherever it appears) and makes a category
    look like a measurement. A numeric input is mapped back to the band it
    falls in, so a run that only ever had the score still renders honestly.
    """
    if isinstance(value, str):
        label = value.strip().lower()
        return label if label in AGE_CONFIDENCE else None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    for label, threshold in (("low", 0.25), ("medium", 0.5), ("high", 0.75)):
        if score <= threshold:
            return label
    return "high"


def _merge_forcing(engine_block: Optional[dict], run_block: Optional[dict]) -> Dict[str, Any]:
    """Engine forcing provenance, enriched with what the run resolved.

    The engine records structure the run does not have (per-field variables,
    fallback, windage, and whether the ML residual was applied); the run
    records identity the engine does not have (which provider actually served
    the grid, and which file). Replacing one with the other loses half the
    story -- which is what reduced the published block to two filenames
    (audit H-11). Neither side is invented: keys absent on both stay absent.
    """
    merged: Dict[str, Any] = json.loads(json.dumps(engine_block or {}))
    for key, value in (run_block or {}).items():
        if key in ("currents", "wind"):
            # Per-field: keep the engine's structure, add the run's identity.
            # A field neither side has stays absent -- an empty {} would read
            # as "we looked and found nothing", which is not what happened.
            if value is None and key not in merged:
                continue
            field = merged.get(key)
            if not isinstance(field, dict):
                field = {} if value is None else {"provider": value}
                merged[key] = field
            if isinstance(value, dict):
                field.update({k: v for k, v in value.items() if v is not None})
            elif value is not None:
                field.setdefault("file", value)
        elif value is not None:
            merged[key] = value
    return merged


class MissingEllipseAxes(ValueError):
    """The engine drew a confidence ellipse but published no axis lengths."""


def _require_axes(props: dict, step_index: int) -> Dict[str, float]:
    """Ellipse semi-axes, or a loud failure.

    Zero-filling absent axes is what made every published ellipse zero-radius
    while the UI rendered them as certainty (audit H-06). A silent 0.0 is the
    one outcome this must never produce, so a partially-populated ellipse
    raises. A cloud too degenerate to fit is a separate, legitimate case: the
    engine omits all three keys together, and the ellipse is then dropped by
    the caller rather than published with invented dimensions.
    """
    keys = ("semi_major_m", "semi_minor_m", "orientation_deg")
    present = [k for k in keys if props.get(k) is not None]
    if not present:
        raise MissingEllipseAxes(
            f"confidence ellipse at step {step_index} carries no axis lengths; "
            f"the engine must emit {keys} or omit the ellipse entirely")
    if len(present) != len(keys):
        raise MissingEllipseAxes(
            f"confidence ellipse at step {step_index} is partially specified "
            f"(has {present}); refusing to default the rest to zero")
    return {
        "semi_major_m": float(props["semi_major_m"]),
        "semi_minor_m": float(props["semi_minor_m"]),
        "orientation_deg": float(props["orientation_deg"]) % 180.0,
    }


def _already_contract(payload: dict) -> bool:
    """True if the engine already emits the contract shape.

    Checked rather than assumed, so this module becomes a no-op the day the
    engines are aligned, instead of corrupting output that is already correct.
    """
    # Presence of a metadata dict proves nothing: Engine B emits
    # metadata={"forcing": {...}}, which passed this check and let the file
    # skip normalisation without scene_id / origin_window_*_utc. Test for a
    # key every contract requires instead.
    meta = payload.get("metadata")
    return isinstance(meta, dict) and "scene_id" in meta


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
            # The numeric score alone loses the two things that make the age
            # defensible: how it was derived, and that the engine itself calls
            # it low. Both were dropped here (audit C-05/C-07), leaving a bare
            # 32.8 h that reads like a measurement.
            "age_method": p.get("age_method"),
            "age_confidence_label": _age_label(p.get("age_confidence")),
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
    dropped_ellipses: List[int] = []
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
            # A cloud too degenerate to fit is a real outcome; the engine says
            # so by omitting all three axis keys. Drop the ellipse instead of
            # publishing one with no dimensions -- the particles still carry
            # the spread, and a missing ellipse is visibly missing.
            if not any(p.get(k) is not None
                       for k in ("semi_major_m", "semi_minor_m", "orientation_deg")):
                dropped_ellipses.append(step_index)
                continue
            ellipses.append({
                "type": "Feature", "geometry": f["geometry"],
                "properties": {
                    "feature_type": "ellipse",
                    "t_utc": _utc(p.get("time_utc") or p.get("t_utc")),
                    "step_index": step_index,
                    "center": list(p.get("center") or _centroid(f["geometry"])),
                    # Carried verbatim from the engine. These used to default to
                    # 0.0 when absent, which published a zero-radius ellipse for
                    # every step and quietly claimed perfect certainty (audit
                    # H-06). The engine now emits real axes; when it genuinely
                    # cannot fit one it omits them, and _require_axes says so
                    # loudly rather than filling the gap with a zero.
                    **_require_axes(p, step_index),
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
    if dropped_ellipses:
        # Lands in the run log next to the stage lines. Fewer ellipses than
        # steps is a real property of the result and should not be inferable
        # only by counting features.
        print(f"[normalise] {len(dropped_ellipses)} confidence ellipse(s) had no "
              f"axis fit and were not published (steps {sorted(dropped_ellipses)})")
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
            # The engine's own structured provenance (per-field provider,
            # variables, fallback, windage, ml_residual) merged under the run's
            # resolved file/provider context. Previously the run-level dict
            # replaced the engine block outright, reducing the whole thing to
            # two filenames (audit H-11).
            "forcing": _merge_forcing(payload.get("metadata", {}).get("forcing"), forcing),
            # Real uncertainty, lifted off the engine's origin_window feature.
            # Absent stays absent: a degenerate cloud has no honest radius.
            **{k: v for k, v in {
                "origin_uncertainty_km": window.get("origin_uncertainty_km"),
                "origin_uncertainty_coverage": window.get("origin_uncertainty_coverage"),
                "origin_uncertainty_method": window.get("origin_uncertainty_method"),
            }.items() if v is not None},
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
    engine_meta = payload.get("metadata", {}) or {}
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": scene_meta.get("scene_id", "unknown"),
            "issued_utc": acquired,
            "horizons_h": sorted(set(horizons)) or [6, 12, 24],
            "forcing": _merge_forcing(engine_meta.get("forcing"), forcing),
            # The fate model states its own assumptions -- assumed oil type and
            # sea temperature, a confidence fixed at 'low', and the processes it
            # does NOT model. All of it was computed and then dropped here, so
            # the forecast reached the UI with no way to qualify it. Absent when
            # the engine did not run weathering; never synthesised.
            **({"weathering": engine_meta["weathering"]}
               if isinstance(engine_meta.get("weathering"), dict) else {}),
            "crs": "EPSG:4326",
        },
        "features": features,
    }


def normalise_suspects(payload: dict, scene_meta: dict, run_id: str,
                       vessel_sources: Optional[Dict[int, str]] = None) -> dict:
    """Map the handbook's suspects shape onto the contract's.

    `vessel_sources` (mmsi -> "real"|"synthetic") comes from the vessels file
    attribution actually ranked. A suspect's provenance is the VESSEL's, never
    the scene's: stamping the scene flag here published synthetic vessels as
    `source: real` in suspects.json.
    """
    vessel_sources = {int(k): str(v).lower() for k, v in (vessel_sources or {}).items()}
    def _src(mmsi: int) -> str:
        v = vessel_sources.get(int(mmsi))
        if v in ("synthetic", "mock"):
            return "synthetic"
        if v in ("real", "sensor"):
            return "real"
        # Unknown vessel provenance is never promoted to real.
        return "synthetic" if vessel_sources else scene_meta.get("source", "real")

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
                             "reason": v.get("reason") or v.get("filter_reason") or "filtered"})
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
            "source": _src(v["mmsi"]),
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
        # The ranking is synthetic if ANY vessel in it is: a planted culprit
        # poisons the whole list's evidentiary value.
        "source": ("synthetic" if (vessel_sources and any(v in ("synthetic", "mock")
                                                             for v in vessel_sources.values()))
                   or any(x["source"] == "synthetic" for x in suspects)
                   else scene_meta.get("source", "real")),
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
