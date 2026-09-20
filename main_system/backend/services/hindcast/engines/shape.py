"""Engine 5 -- Shape-History Consistency (Stage 3).

A slick carries a record of the weather it has lived through. For each
candidate age tau the wind history over [T - tau, T] predicts what the slick
should look like now; L_shape(tau) scores that prediction against the observed
polygon on four counts:

  orientation   the long axis lies along the window-mean wind
  curvature     the centreline bends by (a damped share of) how far the wind veered
  area          Fay spreading + turbulent diffusion for that many hours
  fragmentation more pieces the longer and the rougher the sea

Orientation and curvature only mean something for an ELONGATED slick, so their
terms are weighted by how elongated the observation is; a round blob is scored
on area and fragmentation alone.
"""
from __future__ import annotations

import math

import numpy as np

from backend.services.hindcast.engines.base import Engine, EngineResult, PipelineContext
from backend.services.hindcast.forcing import ForcingField
from backend.services.hindcast.geo import LocalFrame, angle_diff_axis, load_geometry, moments, sample_in_polygon
from backend.services.hindcast.oil import predicted_area_km2

BEND_SHARE = 0.5          # fraction of the wind's veer that shows up as centreline bend
LINEAR_ELONGATION = 3.0
RECURRENCE_FOR_SEEP = 2


def wind_history(field: ForcingField, lon: float, lat: float, k_scene: float, tau_max: int) -> np.ndarray:
    """(tau_max + 1, 2) wind at the slick for hours-before-T = 0..tau_max."""
    return np.array([field.wind_at(lon, lat, k_scene - h) for h in range(tau_max + 1)])


def net_rotation_deg(winds: np.ndarray) -> float:
    """Signed veer (CCW positive) from the oldest sample to the newest."""
    ang = np.degrees(np.arctan2(winds[:, 1], winds[:, 0]))[::-1]   # oldest first
    steps = (np.diff(ang) + 180.0) % 360.0 - 180.0
    return float(steps.sum())


def classify(elongation: float, length_m: float, recurring: int) -> str:
    if recurring >= RECURRENCE_FOR_SEEP:
        return "recurring_seep"
    if elongation >= LINEAR_ELONGATION and length_m >= 2000.0:
        return "linear_discharge"
    return "blob"


class ShapeEngine(Engine):
    id = "shape_engine"
    name = "Shape-History Consistency"
    description = "Scores each candidate age by whether the wind history would have drawn the observed slick shape."
    stage = "Stage 3"
    order = 5
    icon = "Shapes"
    metric_keys = [{"key": "best_fit_age_h", "label": "Best-fit age", "unit": "h"},
                   {"key": "top_score", "label": "Top L_shape"},
                   {"key": "slick_type", "label": "Type"}]

    def run(self, ctx: PipelineContext) -> EngineResult:
        cfg = ctx.config
        taus = np.asarray(ctx.require("tau_hours"), dtype=np.int64)
        stats = ctx.require("slick_stats")
        oil_type = ctx.require("oil_type")
        slick = load_geometry(ctx.require("slick_geojson"))
        field = ForcingField.load(ctx.require("forcing_path"))
        k_scene = field.index_of(ctx.scene_time)
        c = slick.centroid

        ctx.emit.step("reading wind history at the slick", 10)
        winds = wind_history(field, c.x, c.y, k_scene, int(taus[-1]))
        frame = LocalFrame(c.x, c.y)
        px, py = frame.to_xy(*sample_in_polygon(slick, 4000, np.random.default_rng(cfg.seed)))

        elong = float(stats["elongation"])
        axis_weight = float(np.clip((elong - 1.3) / 1.2, 0.0, 1.0))
        area_pred = predicted_area_km2(cfg.spill_volume_m3, taus, oil_type, cfg.k_diff.mid)

        scores = np.zeros(taus.size)
        detail: list[dict[str, float]] = []
        for i, tau in enumerate(taus):
            window = winds[: int(tau) + 1]
            mean_u, mean_v = float(window[:, 0].mean()), float(window[:, 1].mean())
            pred_axis = math.degrees(math.atan2(mean_v, mean_u)) % 180.0
            obs_axis, _, _, obs_bend = moments(px, py, toward=(mean_u, mean_v))
            pred_bend = float(np.clip(BEND_SHARE * net_rotation_deg(window), -120.0, 120.0))

            d_axis = angle_diff_axis(obs_axis, pred_axis)
            s_axis = math.exp(-0.5 * (d_axis / cfg.shape_orientation_sigma_deg) ** 2) ** axis_weight
            s_bend = math.exp(-0.5 * ((obs_bend - pred_bend) / cfg.shape_curvature_sigma_deg) ** 2) ** axis_weight
            ratio = float(stats["area_km2"]) / float(area_pred[i])
            s_area = math.exp(-0.5 * (math.log(ratio) / cfg.shape_area_sigma_ln) ** 2)
            speed = float(np.hypot(window[:, 0], window[:, 1]).mean())
            expected_parts = 1.0 + (tau / 24.0) * (speed / 8.0) ** 2
            s_frag = math.exp(-abs(stats["fragments"] - expected_parts) / (1.0 + expected_parts))
            scores[i] = s_axis * s_bend * s_area * s_frag
            detail.append({"tau": int(tau), "axis_err_deg": round(d_axis, 1), "bend_pred_deg": round(pred_bend, 1),
                           "area_ratio": round(ratio, 3), "score": float(scores[i])})
            if i % 12 == 0:
                ctx.emit.step(f"scoring age {int(tau)} h of {int(taus[-1])} h", 15 + 80 * i / taus.size)

        if scores.max() <= 0:
            scores[:] = 1.0   # uninformative rather than fatal
        l_shape = scores / scores.max()
        best = int(np.argmax(l_shape))
        slick_type = classify(elong, float(stats["length_m"]), int(ctx.state.get("recurring_sightings", 0)))
        ctx.emit.log(f"slick classified as {slick_type} (elongation {elong:.2f}, axis weight {axis_weight:.2f})")

        ctx.state.update({"l_shape": l_shape.tolist(), "slick_type": slick_type,
                          "shape_detail_best": detail[best]})
        return EngineResult(
            metrics={"best_fit_age_h": int(taus[best]), "top_score": round(float(scores[best]), 3),
                     "slick_type": slick_type, "elongation": round(elong, 2)},
            summary=f"L_shape peaks at {int(taus[best])} h; slick type {slick_type}")
