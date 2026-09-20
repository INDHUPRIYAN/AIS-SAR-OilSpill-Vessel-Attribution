"""Engine 6 -- Forward Re-Verification (Stage 4).

Backward drift says where the oil COULD have come from. This stage asks the
converse of each promising (x, tau): released there and then, would the oil
make THIS slick? Each candidate is run forward to T and scored

    L_fwd = w1*IoU + w2*exp(-centroid_err/sigma) + w3*area_ratio + w4*orientation_match

Candidate selection is stratified by age. Picking the global top-N cells of
L_drift*L_shape*P_age would hand nearly every slot to the youngest ages, whose
particle clouds are still compact and therefore have the highest peak density
-- an artefact of normalisation, not evidence. Every age gets its best cells.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from backend.services.hindcast.engines.base import Engine, EngineError, EngineResult, PipelineContext
from backend.services.hindcast.engines.drift import read_l_drift
from backend.services.hindcast.forcing import DriftParams, ForcingField
from backend.services.hindcast.forward import Release, simulate_releases
from backend.services.hindcast.geo import (LocalFrame, RasterGrid, angle_diff_axis, hdr_mask, load_geometry, moments,
                     smooth_density)
from backend.services.hindcast.oil import HDR95


def select_candidates(l_drift: np.ndarray, tau_weight: np.ndarray, n_total: int) -> list[tuple[int, int, int]]:
    """(tau_index, row, col) triples: the best cells of each age, most plausible ages first."""
    n_tau = l_drift.shape[0]
    per_tau = max(1, n_total // n_tau)
    ages = np.argsort(tau_weight)[::-1][: max(1, n_total // per_tau)]
    picks: list[tuple[int, int, int]] = []
    for i in sorted(ages.tolist()):
        layer = l_drift[i]
        if layer.max() <= 0:
            continue
        flat = np.argpartition(layer.ravel(), -per_tau)[-per_tau:]
        for idx in flat:
            if layer.ravel()[idx] > 0:
                picks.append((i, int(idx // layer.shape[1]), int(idx % layer.shape[1])))
    return picks[:n_total]


class VerifyEngine(Engine):
    id = "verify_engine"
    name = "Forward Re-Verification"
    description = "Releases oil at each candidate origin and time, runs it forward, and scores it against the slick."
    stage = "Stage 4"
    order = 6
    icon = "Crosshair"
    metric_keys = [{"key": "candidates_verified", "label": "Verified"},
                   {"key": "best_iou", "label": "Best IoU"},
                   {"key": "best_age_h", "label": "Best age", "unit": "h"}]

    def run(self, ctx: PipelineContext) -> EngineResult:
        cfg = ctx.config
        w = cfg.forward_weights
        taus = np.asarray(ctx.require("tau_hours"), dtype=np.int64)
        grid = RasterGrid.from_dict(ctx.require("drift_grid"))
        slick = load_geometry(ctx.require("slick_geojson"))
        stats = ctx.require("slick_stats")
        oil_type = ctx.require("oil_type")
        field = ForcingField.load(ctx.require("forcing_path"))
        k_scene = field.index_of(ctx.scene_time)

        ctx.emit.step("ranking candidate (x, τ) cells", 5)
        l_drift = read_l_drift(ctx.require("l_drift_path"))
        tau_weight = (np.asarray(ctx.require("p_age")) * np.asarray(ctx.require("l_shape"))
                      * np.asarray(ctx.require("archive_mask")))
        picks = select_candidates(l_drift, tau_weight, cfg.verify_candidates)
        if not picks:
            raise EngineError("L_drift is empty: no candidate origins to verify")
        releases = []
        for i, row, col in picks:
            lon, lat = grid.center(row, col)
            releases.append(Release(float(lon), float(lat), int(taus[i])))

        ctx.emit.step(f"forward-simulating {len(releases)} candidate releases", 15)
        params = DriftParams(cfg.alpha.mid, cfg.theta_deg.mid, cfg.beta.mid, 1.0)
        rng = np.random.default_rng([cfg.seed, 4])
        lon, lat, mass = simulate_releases(field, releases, k_scene, cfg.verify_particles, params,
                                           cfg.k_diff.mid, cfg.spill_volume_m3, oil_type, rng, cfg.dt_s)

        ctx.emit.step("scoring simulated slicks against the observation", 70)
        west, south, east, north = slick.bounds
        pad_lon, pad_lat = 1.0 * (east - west) + 0.01, 1.0 * (north - south) + 0.01
        score_grid = RasterGrid.around(west - pad_lon, south - pad_lat, east + pad_lon, north + pad_lat,
                                       max_cells=96, min_cell_m=100.0)
        observed = score_grid.rasterize(slick)
        c = slick.centroid
        frame = LocalFrame(c.x, c.y)
        axis_weight = float(np.clip((float(stats["elongation"]) - 1.3) / 1.2, 0.0, 1.0))
        area_obs = float(stats["area_km2"])

        results: list[dict[str, Any]] = []
        for j, (rel, (i, row, col)) in enumerate(zip(releases, picks)):
            x, y = frame.to_xy(lon[j], lat[j])
            dens = smooth_density(score_grid.histogram(lon[j], lat[j]), 1.0)
            sim = hdr_mask(dens, 0.95) if dens.sum() > 0 else np.zeros_like(observed)
            union = (sim | observed).sum()
            iou = float((sim & observed).sum() / union) if union else 0.0
            centroid_err = float(math.hypot(x.mean(), y.mean()))
            cov = np.cov(np.vstack([x, y]))
            area_sim = math.pi * HDR95 * math.sqrt(max(float(np.linalg.det(cov)), 0.0)) / 1e6
            area_ratio = min(area_sim, area_obs) / max(area_sim, area_obs, 1e-9)
            sim_axis, *_ = moments(x, y)
            match = math.cos(math.radians(angle_diff_axis(sim_axis, float(stats["orientation_deg"])))) ** 2
            orientation = 1.0 - axis_weight * (1.0 - match)
            l_fwd = (w.iou * iou + w.centroid * math.exp(-centroid_err / w.centroid_sigma_m)
                     + w.area * area_ratio + w.orientation * orientation)
            results.append({"tau_index": i, "tau_h": rel.tau_h, "row": row, "col": col,
                            "lon": rel.lon, "lat": rel.lat, "l_fwd": float(l_fwd), "iou": iou,
                            "centroid_err_m": centroid_err, "area_ratio": float(area_ratio),
                            "orientation_match": float(orientation), "mass_remaining": float(mass[j])})
            if j % 20 == 0:
                ctx.emit.step(f"candidate {j + 1}/{len(releases)} scored", 70 + 28 * j / len(releases))
                ctx.emit.metric(candidates_verified=j + 1, best_iou=round(max(r["iou"] for r in results), 3))

        best = max(results, key=lambda r: r["l_fwd"])
        ctx.state["verify_candidates"] = results
        ctx.emit.log(f"best candidate: τ={best['tau_h']} h at ({best['lat']:.4f}, {best['lon']:.4f}), "
                     f"IoU {best['iou']:.2f}, centroid error {best['centroid_err_m'] / 1000:.2f} km")
        return EngineResult(
            metrics={"candidates_verified": len(results), "best_iou": round(max(r["iou"] for r in results), 3),
                     "best_age_h": best["tau_h"], "best_l_fwd": round(best["l_fwd"], 3)},
            summary=f"{len(results)} candidates verified; best L_fwd {best['l_fwd']:.3f} at τ={best['tau_h']} h")
