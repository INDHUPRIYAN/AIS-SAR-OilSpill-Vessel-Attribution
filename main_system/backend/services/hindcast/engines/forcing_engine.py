"""Engine 3 -- Forcing Data & Calibration (Stage 1)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from backend.services.hindcast import downloaders
from backend.services.hindcast.engines.base import Engine, EngineError, EngineResult, PipelineContext
from backend.services.hindcast.forcing import ForcingField, load_products, synthetic_field
from backend.services.hindcast.engines.ingest import parse_time
from backend.services.hindcast.geo import load_geometry

MAX_SPEED_SCALE = (0.5, 2.0)
MAX_ROTATION_DEG = 45.0


def met_direction_from(u: float, v: float) -> float:
    """Meteorological convention: the bearing the wind blows FROM, clockwise from north."""
    return (270.0 - math.degrees(math.atan2(v, u))) % 360.0


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def wind_bias(sar_speed: float, sar_dir_from: float, model_u: float, model_v: float) -> dict[str, float]:
    """Scale + rotation that carry the model wind at T onto the SAR-derived wind at T."""
    model_speed = math.hypot(model_u, model_v)
    if model_speed < 0.5:
        # Direction of a near-calm wind is noise; correcting with it would
        # rotate the whole history by an arbitrary angle.
        return {"scale": 1.0, "rotation_deg": 0.0, "d_speed": sar_speed - model_speed, "d_dir": 0.0}
    d_dir = wrap180(sar_dir_from - met_direction_from(model_u, model_v))      # clockwise-positive
    scale = float(np.clip(sar_speed / model_speed, *MAX_SPEED_SCALE))
    d_dir_applied = float(np.clip(d_dir, -MAX_ROTATION_DEG, MAX_ROTATION_DEG))
    return {"scale": scale, "rotation_deg": -d_dir_applied,                  # maths rotation is CCW
            "d_speed": sar_speed - model_speed, "d_dir": d_dir}


class ForcingEngine(Engine):
    id = "forcing_engine"
    name = "Forcing Data & Calibration"
    description = "Loads ERA5 wind, CMEMS currents and Stokes drift, then bias-corrects the wind against SAR."
    stage = "Stage 1"
    order = 3
    icon = "Wind"
    metric_keys = [{"key": "d_speed_ms", "label": "Δ speed", "unit": "m/s"},
                   {"key": "d_dir_deg", "label": "Δ dir", "unit": "°"},
                   {"key": "hours_loaded", "label": "Hours", "unit": "h"},
                   {"key": "window_h", "label": "Search window", "unit": "h"}]

    def run(self, ctx: PipelineContext) -> EngineResult:
        scene_time = ctx.scene_time
        tau_max = int(ctx.require("tau_max_h"))
        slick = load_geometry(ctx.require("slick_geojson"))
        c = slick.centroid
        t_start = scene_time - timedelta(hours=tau_max + 2)
        t_end = scene_time + timedelta(hours=2)
        spec: dict[str, Any] = dict(ctx.request.get("forcing") or {})

        ctx.emit.step("loading forcing fields", 10)
        if "synthetic" in spec:
            params = dict(spec["synthetic"])
            if "t_ref" in params:
                params["t_ref"] = parse_time(params["t_ref"])
            if "center" in params:
                params["center"] = tuple(params["center"])
            else:
                params["center"] = (c.x, c.y)
            for key in ("current", "wind"):
                if key in params:
                    params[key] = tuple(params[key])
            start_hour = t_start.replace(minute=0, second=0, microsecond=0)
            field = synthetic_field(start_hour, tau_max + 6, **params)
            source = "synthetic"
            ctx.emit.log("forcing: SYNTHETIC analytic field (demo / test); no provider was contacted")
        else:
            field, source = self._load_real(ctx, spec, (c.x, c.y), scene_time)
            tau_max = int(ctx.require("tau_max_h"))     # may have been clipped to what the files cover

        k_scene = field.index_of(scene_time)
        if k_scene - tau_max < 0 or k_scene > field.n_times - 1:
            raise EngineError("forcing does not span [T - tau_max, T]")

        ctx.emit.step("bias-correcting wind against the SAR-derived wind at T", 60)
        sar = ctx.state.get("sar_wind")
        mu, mv = field.wind_at(c.x, c.y, k_scene)
        if sar and sar.get("speed_ms") is not None and sar.get("dir_from_deg") is not None:
            bias = wind_bias(float(sar["speed_ms"]), float(sar["dir_from_deg"]), mu, mv)
            field = field.with_wind_correction(bias["scale"], bias["rotation_deg"])
            ctx.emit.log(f"wind bias at T: model {math.hypot(mu, mv):.2f} m/s from {met_direction_from(mu, mv):.0f}°, "
                         f"SAR {sar['speed_ms']:.2f} m/s from {sar['dir_from_deg']:.0f}° -> "
                         f"scale x{bias['scale']:.3f}, rotate {-bias['rotation_deg']:+.1f}° (applied to all history)")
        else:
            bias = {"scale": 1.0, "rotation_deg": 0.0, "d_speed": 0.0, "d_dir": 0.0}
            ctx.emit.log("no SAR-derived wind in scene_meta.sar_wind: model wind used uncorrected")

        ctx.emit.step("writing calibrated forcing", 85)
        field.save(ctx.path("forcing.npz"))
        ctx.state.update({"forcing_path": str(ctx.path("forcing.npz")), "forcing_source": source,
                          "wind_bias": bias})
        if ctx.state.get("tau_limited_by_forcing"):
            ctx.emit.metric(tau_clipped_to_h=int(ctx.require("tau_max_h")))
        metrics = {"d_speed_ms": round(bias["d_speed"], 2), "d_dir_deg": round(bias["d_dir"], 1),
                   "wind_scale": round(bias["scale"], 3), "hours_loaded": field.n_times,
                   # what the search window is AFTER this stage: forcing coverage may have cut it
                   "window_h": int(ctx.require("tau_max_h")),
                   "source": source, "grid": f"{field.lats.size}×{field.lons.size}"}
        return EngineResult(metrics=metrics, summary=f"{field.n_times} h of forcing from {source}")

    # ------------------------------------------------------------ real data ---
    def _load_real(self, ctx: PipelineContext, spec: dict[str, Any], centre: tuple[float, float],
                   scene_time: datetime) -> tuple[ForcingField, str]:
        from backend.api.routes import _pipeline_gate
        from backend.services.hindcast.forcing import product_extent

        wind_path, current_path, stokes_path = spec.get("wind_path"), spec.get("current_path"), spec.get("stokes_path")
        run_id = spec.get("oceantrace_run")
        origin = "local NetCDF"
        # Every NetCDF touch below goes through HDF5, which is not thread-safe in
        # this process: hold the gate the OceanTrace pipeline holds while it reads.
        ctx.emit.step("waiting for the NetCDF reader", 12)
        with _pipeline_gate:
            if run_id and not (wind_path or current_path):
                from backend.core.config import get_settings
                from backend.services.pipeline.run import resolve_metocean

                meta = dict(ctx.request.get("scene_meta") or {})
                meta.setdefault("bbox", list(load_geometry(ctx.require("aoi_geojson")).bounds))
                currents, wind = resolve_metocean(meta, get_settings().runs_root / run_id)
                current_path = str(currents) if currents else None
                wind_path = str(wind) if wind else None
                origin = f"OceanTrace metocean cache (run {run_id})"
                if not wind_path:
                    raise EngineError("the OceanTrace metocean cache holds no wind grid covering this scene")
            try:
                if not wind_path:
                    downloaders.download_era5_wind(scene_time, scene_time, (0, 0, 0, 0), ctx.path("era5.nc"))
                if not current_path and not run_id:
                    downloaders.download_cmems(scene_time, scene_time, (0, 0, 0, 0),
                                               ctx.path("cmems_cur.nc"), ctx.path("cmems_stokes.nc"))
            except NotImplementedError as exc:
                raise EngineError(str(exc)) from exc
            paths = {"wind": wind_path, "current": current_path, "stokes": stokes_path}
            for label, value in paths.items():
                if value and not Path(value).exists():
                    raise EngineError(f"{label} file not found: {value}")

            ctx.emit.step("reading the coverage of the forcing files", 20)
            extents = {k: product_extent(Path(v)) for k, v in paths.items() if v}
            half = float(spec.get("half_size_deg", 1.5))
            west = max([centre[0] - half] + [e["west"] for e in extents.values()])
            east = min([centre[0] + half] + [e["east"] for e in extents.values()])
            south = max([centre[1] - half] + [e["south"] for e in extents.values()])
            north = min([centre[1] + half] + [e["north"] for e in extents.values()])
            if not (west < centre[0] < east and south < centre[1] < north):
                raise EngineError("the forcing files do not cover the slick: " + "; ".join(
                    f"{k} {e['west']:.2f}..{e['east']:.2f}E {e['south']:.2f}..{e['north']:.2f}N"
                    for k, e in extents.items()))

            # The search window cannot reach further back than the forcing does.
            earliest = max(e["t0"] for e in extents.values())
            latest = min(e["t1"] for e in extents.values())
            t_scene = np.datetime64(scene_time.astimezone(timezone.utc).replace(tzinfo=None), "s")
            if latest < t_scene:
                raise EngineError(f"the forcing ends at {str(latest)[:16]}, before the scene time {t_scene}")
            reach_h = int((t_scene - earliest) / np.timedelta64(3600, "s")) - 1
            self._clip_window(ctx, reach_h, str(earliest)[:16])
            tau_max = int(ctx.require("tau_max_h"))

            ctx.emit.step("interpolating forcing onto the hindcast grid", 35)
            latest_dt = datetime.fromisoformat(str(latest.astype("datetime64[s]"))).replace(tzinfo=timezone.utc)
            try:
                field = load_products(Path(current_path) if current_path else None, Path(wind_path),
                                      Path(stokes_path) if stokes_path else None,
                                      scene_time - timedelta(hours=tau_max + 1),
                                      min(scene_time + timedelta(hours=2), latest_dt),
                                      (west, south, east, north), float(spec.get("grid_deg", 0.05)))
            except (KeyError, ValueError) as exc:
                raise EngineError(str(exc)) from exc

        if not current_path:
            ctx.emit.log("NO CURRENTS GRID for this scene: this is a WIND-ONLY hindcast (currents = 0)")
        if not stokes_path:
            ctx.emit.log("no Stokes drift file: beta has nothing to scale (Stokes = 0)")
        return field, f"{origin} · {'wind + currents' if current_path else 'WIND ONLY'}"

    @staticmethod
    def _clip_window(ctx: PipelineContext, reach_h: int, earliest: str) -> None:
        taus: list[int] = ctx.require("tau_hours")
        if reach_h >= taus[-1]:
            return
        keep = [i for i, t in enumerate(taus) if t <= reach_h]
        if len(keep) < 3:
            raise EngineError(f"the forcing starts at {earliest}, only {reach_h} h before the scene: "
                              f"too short to hindcast (the window starts at {taus[0]} h)")
        prior = np.asarray([ctx.state["p_age"][i] for i in keep], dtype=float)
        ctx.state.update({"tau_hours": [taus[i] for i in keep], "p_age": (prior / prior.sum()).tolist(),
                          "archive_mask": [ctx.state["archive_mask"][i] for i in keep],
                          "tau_max_h": taus[keep[-1]], "tau_limited_by_forcing": True})
        ctx.emit.log(f"SEARCH WINDOW CLIPPED from {taus[-1]} h to {taus[keep[-1]]} h: "
                     f"the forcing on this host only starts at {earliest}")
