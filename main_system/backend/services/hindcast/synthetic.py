"""A synthetic spill with a known answer.

Oil is released at a known point and time, run FORWARD through a forcing field
to the "satellite pass", and outlined. The hindcast is then handed only what a
real case would have -- the outline, the scene time, a SAR wind, the forcing --
and must find the release again. The truth rides along in the request so the
posterior engine can report how close it got.

The forcing the hindcast receives is deliberately NOT the forcing that moved
the oil: the truth wind is 8% stronger and veered 6 degrees from the "model"
wind. The SAR-derived wind at T is what lets Stage 1 find and remove that bias.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from shapely.geometry import box

from backend.services.hindcast.config import PipelineConfig
from backend.services.hindcast.engines.forcing_engine import met_direction_from
from backend.services.hindcast.forcing import DriftParams, synthetic_field
from backend.services.hindcast.forward import Release, simulate_releases
from backend.services.hindcast.geo import RasterGrid, geojson_of, hdr_mask, mask_to_geometry, smooth_density

TRUE_WIND_SCALE = 1.08
TRUE_WIND_VEER_DEG = 6.0        # clockwise


def build_demo_request(scene_time: Optional[datetime] = None, origin: tuple[float, float] = (88.20, 14.10),
                       tau_true_h: int = 18, clean_scene_h: int = 36, oil_type: str = "medium",
                       seed: int = 7, config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    scene_time = (scene_time or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    cfg = PipelineConfig(**(config or {}))
    t_ref = scene_time - timedelta(hours=240)
    spec = {"center": [origin[0] + 0.15, origin[1] + 0.08], "t_ref": t_ref.isoformat(),
            "half_size_deg": 1.5, "current": [0.14, 0.06], "wind": [6.5, 2.5],
            "wind_rotation_deg_per_h": 0.7}

    model = synthetic_field(scene_time - timedelta(hours=tau_true_h + 3), tau_true_h + 6,
                            center=tuple(spec["center"]), half_size_deg=spec["half_size_deg"],
                            current=tuple(spec["current"]), wind=tuple(spec["wind"]),
                            wind_rotation_deg_per_h=spec["wind_rotation_deg_per_h"], t_ref=t_ref)
    truth_field = model.with_wind_correction(TRUE_WIND_SCALE, -TRUE_WIND_VEER_DEG)
    k_end = truth_field.index_of(scene_time)

    rng = np.random.default_rng(seed)
    params = DriftParams(cfg.alpha.mid, cfg.theta_deg.mid, cfg.beta.mid, 1.0)
    lon, lat, _ = simulate_releases(truth_field, [Release(origin[0], origin[1], tau_true_h)], k_end, 6000,
                                    params, cfg.k_diff.mid, cfg.spill_volume_m3, oil_type, rng)
    lon, lat = lon[0], lat[0]
    pad = 0.03
    grid = RasterGrid.around(lon.min() - pad, lat.min() - pad, lon.max() + pad, lat.max() + pad,
                             max_cells=140, min_cell_m=60.0)
    slick = mask_to_geometry(hdr_mask(smooth_density(grid.histogram(lon, lat), 1.5), 0.95), grid)
    if slick is None:
        raise RuntimeError("synthetic spill produced no slick")
    if slick.geom_type == "MultiPolygon":   # keep the main sheet; specks are sampling noise
        slick = max(slick.geoms, key=lambda g: g.area)

    c = slick.centroid
    wu, wv = truth_field.wind_at(c.x, c.y, k_end)
    footprint = box(c.x - 1.0, c.y - 0.8, c.x + 1.0, c.y + 0.8)
    return {
        "label": f"Synthetic spill · release {tau_true_h} h before pass",
        "scene_meta": {"scene_id": f"SYNTH_S1_{scene_time:%Y%m%dT%H%M%S}", "acquired_utc": scene_time.isoformat(),
                       "incidence_angle_deg": 36.5, "footprint": geojson_of(footprint),
                       "sar_wind": {"speed_ms": round(math.hypot(wu, wv), 3),
                                    "dir_from_deg": round(met_direction_from(wu, wv), 2)}},
        "slick_polygon_geojson": geojson_of(slick),
        "oil_type": oil_type,
        "forcing": {"synthetic": spec},
        "config": config or {},
        "truth": {"lon": origin[0], "lat": origin[1], "tau_h": tau_true_h,
                  "release_time": (scene_time - timedelta(hours=tau_true_h)).isoformat()},
        "archive": [{"aoi": geojson_of(footprint), "slick_present": False,
                     "scene_time": (scene_time - timedelta(hours=clean_scene_h)).isoformat()}],
    }
