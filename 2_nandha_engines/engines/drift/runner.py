"""Engine B orchestration: slick + met-ocean -> ``origin_cloud.geojson`` / ``forecast.geojson``.

Contract: handbook §4.3 (outputs) and §7 (CLI). Engine selection order at runtime is
OpenOil -> OceanDrift -> Euler (§6 Phase 3); only the Euler fallback exists so far, so
every run reports ``engine_used: "fallback"`` in the status object and ``"euler"`` in
the output.

``hindcast`` and ``forecast`` share their entire setup - the only differences are the
sign of the timestep, the time window the met-ocean grids must cover, and how the result
is written out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import shape

from ..common.errors import EngineError, missing_input
from ..common.io import read_json, read_yaml, require_file, write_json
from ..common.status import FALLBACK, Status
from ..common.timeutil import format_utc, parse_utc
from ..schemas.forecast import validate_forecast
from ..schemas.origin_cloud import validate_origin_cloud
from .backends import AUTO, DriftRequest, select_backend
from .cloud import build_clouds, origin_window
from .euler_fallback import BACKWARD, FORWARD, DriftRun, seed_particles
from .forecast import DEFAULT_HORIZONS, build_forecast
from .grids import load_metocean

DEFAULT_CONFIG = Path("config/drift.yaml")

DEFAULTS: dict[str, Any] = {
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
    "forecast_horizons_h": list(DEFAULT_HORIZONS),
    "concave_ratio": 0.3,
    "engine": AUTO,
}


def _seconds(when: str) -> float:
    return parse_utc(when).timestamp()


def _utc(seconds: float) -> str:
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
            "to run a different one."
        )
    return ordered[0]


@dataclass
class _Prepared:
    """Everything both modes need before the integrator starts."""

    config: dict[str, Any]
    properties: dict[str, Any]
    polygon: Any
    start_s: float
    run_hours: float
    metocean: Any
    seed_lons: np.ndarray
    seed_lats: np.ndarray
    rng: np.random.Generator
    backend: Any = None
    currents_path: Any = None
    wind_path: Any = None


def _prepare(
    slick_path: str | Path,
    *,
    currents_path: str | Path | None,
    wind_path: str | Path | None,
    config_path: str | Path,
    hours: float | None,
    slick_id: str | None,
    direction: int,
    status: Status,
    engine: str | None = None,
) -> _Prepared:
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

    metocean, grid_warnings = load_metocean(
        currents_path, wind_path, leeway=float(config["leeway"])
    )
    for warning in grid_warnings:
        status.warn(warning)

    # Coverage must be checked over where the particles will *go*, not just where the
    # slick is: over 24 h they routinely travel several times the slick's own width.
    west, south, east, north = polygon.bounds
    centre_lat = (south + north) / 2.0
    margin = float(config["grid_margin_deg"])
    slick_bbox = (west - margin, south - margin, east + margin, north + margin)
    reach_lon, reach_lat = metocean.drift_margin_deg(run_hours, centre_lat, slick_bbox)
    reach_bbox = (
        slick_bbox[0] - reach_lon,
        slick_bbox[1] - reach_lat,
        slick_bbox[2] + reach_lon,
        slick_bbox[3] + reach_lat,
    )
    span = run_hours * 3600.0 * (1 if direction == FORWARD else -1)
    for warning in metocean.check_coverage(
        slick_bbox,
        min(start_s, start_s + span),
        max(start_s, start_s + span),
        reach_bbox=reach_bbox,
    ):
        status.warn(warning)

    rng = np.random.default_rng(int(config["seed"]))
    seed_lons, seed_lats = seed_particles(polygon, int(config["particles"]), rng)

    backend, engine_warnings = select_backend(engine or config.get("engine", AUTO))
    for warning in engine_warnings:
        status.warn(warning)

    return _Prepared(
        config, properties, polygon, start_s, run_hours, metocean,
        seed_lons, seed_lats, rng,
        backend=backend, currents_path=currents_path, wind_path=wind_path,
    )


def _integrate(prep: _Prepared, direction: int, status: Status) -> DriftRun:
    request = DriftRequest(
        seed_lons=prep.seed_lons,
        seed_lats=prep.seed_lats,
        start_time_s=prep.start_s,
        hours=prep.run_hours,
        dt_seconds=float(prep.config["dt_minutes"]) * 60.0,
        direction=direction,
        diffusion_m2_s=float(prep.config["diffusion_m2_s"]),
        rng=prep.rng,
        metocean=prep.metocean,
        currents_path=prep.currents_path,
        wind_path=prep.wind_path,
        leeway=float(prep.config["leeway"]),
    )
    run = prep.backend.run(request)
    status.set_engine(prep.backend.kind)

    # Particles that leave the grid get its edge velocity held constant - defensible,
    # but the caller must know it happened rather than reading a confident answer.
    escaped = prep.metocean.outside_fraction(run.lons, run.lats)
    if escaped > 0:
        status.warn(
            f"{escaped:.0%} of particles drifted outside the met-ocean grid; their "
            "velocities were held at the grid edge, so the cloud is less reliable the "
            "further it goes"
        )
    return run


# ------------------------------------------------------------------- hindcast ------
def _particle_features(clouds) -> list[dict[str, Any]]:
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
    engine: str | None = None,
) -> dict[str, Any]:
    """Backtrack a slick to its probable origin. Returns the §4.5 status object."""
    status = Status(FALLBACK)          # Euler is the fallback engine by design
    try:
        prep = _prepare(
            slick_path, currents_path=currents_path, wind_path=wind_path,
            config_path=config_path, hours=hours, slick_id=slick_id,
            direction=BACKWARD, status=status, engine=engine,
        )
        run = _integrate(prep, BACKWARD, status)

        level = float(prep.config["confidence_level"])
        clouds = build_clouds(
            run, level=level, every_h=float(prep.config["output_every_h"])
        )
        window, window_warnings = origin_window(
            clouds,
            age_hours_est=prep.properties.get("age_hours_est"),
            window_fraction=float(prep.config["window_fraction"]),
            diffusion_m2_s=float(prep.config["diffusion_m2_s"]),
        )
        for warning in window_warnings:
            status.warn(warning)

        document = {
            "type": "FeatureCollection",
            "features": [
                *_particle_features(clouds),
                *_ellipse_features(clouds, level),
                _window_feature(window, clouds, run.engine),
            ],
        }
        validate_origin_cloud(document)
        status.add_output("origin_cloud", str(write_json(out_path, document)))
        return status.to_dict()

    except EngineError as err:
        return status.fail(err).to_dict()
    except ValueError as exc:
        return status.fail(missing_input(str(exc))).to_dict()


# ------------------------------------------------------------------- forecast ------
def forecast(
    slick_path: str | Path,
    out_path: str | Path,
    *,
    currents_path: str | Path | None = None,
    wind_path: str | Path | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
    hours: float | None = None,
    slick_id: str | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    """Predict the slick's spread at +6 / +12 / +24 h. Returns the §4.5 status object.

    ``hours`` sets the run length, per the frozen CLI; horizons come from the config and
    are filtered to those the run actually reaches.
    """
    status = Status(FALLBACK)
    try:
        horizons_config = None
        if Path(config_path).is_file():
            horizons_config = (
                read_yaml(config_path, what="drift config")
                .get("drift", {})
                .get("forecast_horizons_h")
            )
        horizons = [float(h) for h in (horizons_config or DEFAULT_HORIZONS)]

        # The run must be long enough for the furthest horizon unless --hours caps it.
        prep = _prepare(
            slick_path, currents_path=currents_path, wind_path=wind_path,
            config_path=config_path,
            hours=hours if hours is not None else max(horizons),
            slick_id=slick_id, direction=FORWARD, status=status, engine=engine,
        )

        reachable = [h for h in horizons if h <= prep.run_hours + 1e-9]
        dropped = [h for h in horizons if h not in reachable]
        if dropped:
            status.warn(
                f"horizon(s) {', '.join(f'+{h:g} h' for h in dropped)} exceed the "
                f"{prep.run_hours:g} h run length and were skipped"
            )
        if not reachable:
            raise missing_input(
                f"no forecast horizon fits inside a {prep.run_hours:g} h run",
                horizons=horizons,
            )

        run = _integrate(prep, FORWARD, status)

        level = float(prep.config["confidence_level"])
        results, forecast_warnings = build_forecast(
            run, horizons=reachable, level=level,
            ratio=float(prep.config["concave_ratio"]),
        )
        for warning in forecast_warnings:
            status.warn(warning)
        if any(r.hull_method == "convex" for r in results):
            status.warn(
                "the concave hull degenerated at one or more horizons; a convex hull "
                "was used there, which overstates the extent"
            )

        document = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [round(float(x), 6), round(float(y), 6)]
                                for x, y in result.polygon.exterior.coords
                            ]
                        ],
                    },
                    "properties": {
                        "horizon_h": result.horizon_h,
                        "uncertainty_growth": round(result.uncertainty_growth, 3),
                        "level": level,
                        "time_utc": _utc(result.time_s),
                        "area_km2": round(result.area_km2, 3),
                        "ellipse_area_km2": round(result.ellipse_area_km2, 3),
                        "particles_used": result.particles_used,
                        "particles_total": result.particles_total,
                        "hull_method": result.hull_method,
                        "engine_used": run.engine,
                    },
                }
                for result in results
            ],
        }
        validate_forecast(document)
        status.add_output("forecast", str(write_json(out_path, document)))
        return status.to_dict()

    except EngineError as err:
        return status.fail(err).to_dict()
    except ValueError as exc:
        return status.fail(missing_input(str(exc))).to_dict()
