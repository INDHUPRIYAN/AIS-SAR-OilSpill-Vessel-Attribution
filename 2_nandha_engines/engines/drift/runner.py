"""Engine B orchestration (hindcast): slick + met-ocean -> ``origin_cloud.geojson``.

Contract: handbook §4.3 (output) and §7 (CLI). Engine selection order at runtime is
OpenOil -> OceanDrift -> Euler (§6 Phase 3); only the Euler fallback exists so far, so
every run reports ``engine_used: "fallback"`` in the status object and ``"euler"`` in
the output's origin-window feature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import shape

from ..common.errors import EngineError, missing_input
from ..common.io import read_json, read_yaml, require_file, write_json
from ..common.status import FALLBACK, Status
from ..common.timeutil import format_utc, parse_utc
from ..schemas.origin_cloud import validate_origin_cloud
from .cloud import build_clouds, origin_window
from .euler_fallback import BACKWARD, DriftRun, run_euler, seed_particles
from .grids import load_metocean

DEFAULT_CONFIG = Path("config/drift.yaml")
EULER = "euler"

DEFAULTS = {
    "particles": 300,
    "hours": 24.0,
    "dt_minutes": 10.0,
    "leeway": 0.03,
    "diffusion_m2_s": 5.0,
    "confidence_level": 0.9,
    "output_every_h": 1.0,
    "window_fraction": 0.10,
    "grid_margin_deg": 0.05,
    "seed": 26143,
}


def _seconds(when: str) -> float:
    return parse_utc(when).timestamp()


def _utc(seconds: float) -> str:
    from datetime import datetime, timezone

    return format_utc(datetime.fromtimestamp(seconds, tz=timezone.utc))


def _pick_slick(document: dict[str, Any], slick_id: str | None, status: Status):
    """Choose which slick to seed from; largest by area unless one is named."""
    features = document.get("features") or []
    if not features:
        raise missing_input("slick.geojson contains no features")

    if slick_id:
        for feature in features:
            if (feature.get("properties") or {}).get("slick_id") == slick_id:
                return feature
        raise missing_input(
            f"no slick with slick_id {slick_id!r} in the input",
            available=[(f.get("properties") or {}).get("slick_id") for f in features],
        )

    ordered = sorted(
        features,
        key=lambda f: float((f.get("properties") or {}).get("area_km2") or 0.0),
        reverse=True,
    )
    if len(ordered) > 1:
        status.warn(
            f"{len(ordered)} slicks in the input; seeding from the largest "
            f"({(ordered[0].get('properties') or {}).get('slick_id')}). Use --slick-id "
            "to hindcast a different one."
        )
    return ordered[0]


def _particle_features(clouds, engine: str) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for cloud in clouds:
        time_utc = _utc(cloud.time_s)
        for lon, lat, weight in zip(cloud.lons, cloud.lats, cloud.weights):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(float(lon), 6), round(float(lat), 6)],
                    },
                    "properties": {
                        "time_utc": time_utc,
                        "weight": round(float(weight), 4),
                        "timestep_h": cloud.elapsed_h,
                    },
                }
            )
    return features


def _ellipse_features(clouds, level: float) -> list[dict[str, Any]]:
    features = []
    for cloud in clouds:
        if not cloud.ellipse:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(pt) for pt in cloud.ellipse]],
                },
                "properties": {
                    "kind": "confidence_ellipse",
                    "level": level,
                    "timestep_h": cloud.elapsed_h,
                    "time_utc": _utc(cloud.time_s),
                },
            }
        )
    return features


def _window_feature(window, clouds, engine: str) -> dict[str, Any]:
    peak = min(clouds, key=lambda c: abs(c.time_s - window.peak_s))
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [round(peak.centroid[0], 6), round(peak.centroid[1], 6)],
        },
        "properties": {
            "kind": "origin_window",
            "start_utc": _utc(window.start_s),
            "end_utc": _utc(window.end_s),
            "peak_utc": _utc(window.peak_s),
            "engine_used": engine,
            "method": window.method,
        },
    }


def hindcast(
    slick_path: str | Path,
    out_path: str | Path,
    *,
    currents_path: str | Path | None = None,
    wind_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    hours: float | None = None,
    slick_id: str | None = None,
) -> dict[str, Any]:
    """Backtrack a slick to its probable origin. Returns the §4.5 status object."""
    status = Status(FALLBACK)          # Euler is the fallback engine by design
    try:
        slick_file = require_file(slick_path, what="slick.geojson")
        document = read_json(slick_file, what="slick.geojson")

        config = dict(DEFAULTS)
        if Path(config_path).is_file():
            config.update(read_yaml(config_path, what="drift config").get("drift", {}))
        else:
            status.warn(f"config {config_path} not found; using built-in defaults")
        if hours is not None:
            config["hours"] = hours

        feature = _pick_slick(document, slick_id, status)
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            raise missing_input("selected slick has no geometry")

        polygon = shape(geometry)
        detected_utc = properties.get("detected_utc")
        if not detected_utc:
            raise missing_input("slick feature has no 'detected_utc'")
        start_s = _seconds(detected_utc)
        run_hours = float(config["hours"])

        # Load and validate the met-ocean grids against this slick and time window.
        metocean, grid_warnings = load_metocean(
            currents_path, wind_path, leeway=float(config["leeway"])
        )
        for warning in grid_warnings:
            status.warn(warning)

        margin = float(config["grid_margin_deg"])
        west, south, east, north = polygon.bounds
        bbox = (west - margin, south - margin, east + margin, north + margin)
        for warning in metocean.check_coverage(
            bbox, start_s - run_hours * 3600.0, start_s
        ):
            status.warn(warning)

        rng = np.random.default_rng(int(config["seed"]))
        seed_lons, seed_lats = seed_particles(polygon, int(config["particles"]), rng)

        run: DriftRun = run_euler(
            seed_lons, seed_lats, metocean, start_s,
            hours=run_hours,
            dt_seconds=float(config["dt_minutes"]) * 60.0,
            direction=BACKWARD,
            diffusion_m2_s=float(config["diffusion_m2_s"]),
            rng=rng,
        )

        level = float(config["confidence_level"])
        clouds = build_clouds(run, level=level, every_h=float(config["output_every_h"]))
        window, window_warnings = origin_window(
            clouds,
            age_hours_est=properties.get("age_hours_est"),
            window_fraction=float(config["window_fraction"]),
            diffusion_m2_s=float(config["diffusion_m2_s"]),
        )
        for warning in window_warnings:
            status.warn(warning)

        document_out = {
            "type": "FeatureCollection",
            "features": [
                *_particle_features(clouds, EULER),
                *_ellipse_features(clouds, level),
                _window_feature(window, clouds, EULER),
            ],
        }
        validate_origin_cloud(document_out)
        written = write_json(out_path, document_out)
        status.add_output("origin_cloud", str(written))
        return status.to_dict()

    except EngineError as err:
        return status.fail(err).to_dict()
    except ValueError as exc:
        return status.fail(missing_input(str(exc))).to_dict()
