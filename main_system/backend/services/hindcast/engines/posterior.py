"""Engine 7 -- Bayesian Fusion & Report (Stage 5).

    posterior(x, tau)  ∝  P_age(tau) · archive_mask(tau) · L_drift(x|tau) · L_shape(tau) · L_fwd(x, tau)

L_fwd was only computed at the verified candidates. Between them it is
extended, not invented -- see `extend_l_fwd`.

If the posterior has more than one mode, every mode is reported. They are never
averaged: the mean of two candidate origins is a place the oil did not come from.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

import numpy as np
import rasterio
from scipy import ndimage
from shapely.geometry import Point

from backend.services.hindcast.engines.base import Engine, EngineError, EngineResult, PipelineContext
from backend.services.hindcast.engines.drift import read_l_drift
from backend.services.hindcast.geo import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, RasterGrid, geojson_of, hdr_mask, mask_to_geometry


def extend_l_fwd(candidates: list[dict[str, Any]], l_drift: np.ndarray, grid: RasterGrid,
                 weights: Any, slick_scale_m: float) -> np.ndarray:
    """L_fwd on the whole (tau, y, x) grid, from the cells where it was computed.

    Moving a release by d moves the simulated slick by about d and changes
    nothing else, so only the two POSITION terms decay with distance from a
    verified cell -- overlap on the scale of the slick, the centroid term on its
    own sigma. Area and orientation depend on the age alone and carry over
    unchanged. (Decaying the whole score instead quietly favours young ages,
    whose clouds are compact enough to sit inside the decay radius.)
    """
    n_tau, ny, nx = l_drift.shape
    lat_mid = 0.5 * (grid.south + grid.north)
    cell_x = grid.dlon * M_PER_DEG_LON_EQ * math.cos(math.radians(lat_mid))
    cell_y = grid.dlat * M_PER_DEG_LAT
    rows, cols = np.mgrid[0:ny, 0:nx]
    sigma = weights.centroid_sigma_m

    by_tau: dict[int, list[dict[str, Any]]] = {}
    for cand in candidates:
        by_tau.setdefault(int(cand["tau_index"]), []).append(cand)
    known = sorted(by_tau)
    keys = ("iou", "centroid_term", "area_ratio", "orientation_match")
    for cands in by_tau.values():
        for c in cands:
            c["centroid_term"] = math.exp(-c["centroid_err_m"] / sigma)
    best = {i: max(by_tau[i], key=lambda c: c["l_fwd"]) for i in known}
    interp = {k: np.interp(np.arange(n_tau), known, [best[i][k] for i in known]) for k in keys}

    out = np.zeros_like(l_drift)
    for i in range(n_tau):
        if i in by_tau:
            anchors = [(c["row"], c["col"], c) for c in by_tau[i]]
        else:
            peak = np.unravel_index(int(np.argmax(l_drift[i])), (ny, nx))
            anchors = [(int(peak[0]), int(peak[1]), {k: float(interp[k][i]) for k in keys})]
        layer = np.zeros((ny, nx))
        for r, c, a in anchors:
            dist = np.hypot((cols - c) * cell_x, (rows - r) * cell_y)
            score = (weights.iou * a["iou"] * np.exp(-dist / slick_scale_m)
                     + weights.centroid * a["centroid_term"] * np.exp(-dist / sigma)
                     + weights.area * a["area_ratio"] + weights.orientation * a["orientation_match"])
            layer = np.maximum(layer, score)
        out[i] = layer
    return out


def hdr_interval(taus: np.ndarray, p: np.ndarray, mass: float) -> tuple[int, int]:
    keep = hdr_mask(p[None, :], mass)[0]
    idx = np.flatnonzero(keep)
    return int(taus[idx.min()]), int(taus[idx.max()])


def tau_modes(taus: np.ndarray, p: np.ndarray) -> list[dict[str, float]]:
    """Peaks of P(tau) separated by a valley below half the smaller peak."""
    smooth = ndimage.gaussian_filter1d(p, 1.0, mode="nearest")
    peaks = [i for i in range(len(smooth))
             if smooth[i] >= smooth[max(i - 1, 0)] and smooth[i] >= smooth[min(i + 1, len(smooth) - 1)]
             and smooth[i] > 0.15 * smooth.max()]
    modes: list[int] = []
    for i in peaks:
        if modes:
            j = modes[-1]
            valley = smooth[j:i + 1].min()
            if valley > 0.5 * min(smooth[i], smooth[j]):
                if smooth[i] > smooth[j]:
                    modes[-1] = i
                continue
        modes.append(i)
    return [{"tau_h": int(taus[i]), "density": float(p[i])} for i in modes]


class PosteriorEngine(Engine):
    id = "posterior_engine"
    name = "Bayesian Fusion & Report"
    description = "Fuses every likelihood into the posterior over origin and release time; reports MAP, credible regions and modes."
    stage = "Stage 5"
    order = 7
    icon = "Target"
    metric_keys = [{"key": "map_position", "label": "MAP origin"},
                   {"key": "release_window", "label": "Release"},
                   {"key": "hdr90_area_km2", "label": "90% region", "unit": "km²"},
                   {"key": "modes", "label": "Modes"}]

    def run(self, ctx: PipelineContext) -> EngineResult:
        cfg = ctx.config
        taus = np.asarray(ctx.require("tau_hours"), dtype=np.int64)
        grid = RasterGrid.from_dict(ctx.require("drift_grid"))
        scene_time = ctx.scene_time

        ctx.emit.step("assembling likelihood terms", 10)
        l_drift = read_l_drift(ctx.require("l_drift_path"))
        tau_term = (np.asarray(ctx.require("p_age")) * np.asarray(ctx.require("archive_mask"))
                    * np.asarray(ctx.require("l_shape")))
        slick_scale_m = math.sqrt(float(ctx.require("slick_stats")["area_km2"])) * 1000.0
        l_fwd = extend_l_fwd(ctx.require("verify_candidates"), l_drift, grid,
                             cfg.forward_weights, max(slick_scale_m, 500.0))

        ctx.emit.step("fusing and normalising the posterior", 40)
        post = tau_term[:, None, None] * l_drift * np.power(l_fwd, cfg.fwd_exponent)
        total = post.sum()
        if not np.isfinite(total) or total <= 0:
            raise EngineError("posterior has no mass: the likelihood terms do not overlap anywhere")
        post /= total

        spatial = post.sum(axis=0)
        p_tau = post.sum(axis=(1, 2))

        # MAP: most probable AGE first (from the marginal), then the most probable
        # place given that age. The raw argmax over (x, tau) cells is reported too,
        # but it leans young for a reason that is not evidence: a young cloud is
        # compact, so the same probability sits in fewer, taller cells.
        i_tau = int(np.argmax(p_tau))
        row, col = np.unravel_index(int(np.argmax(post[i_tau])), post[i_tau].shape)
        map_lon, map_lat = (float(v) for v in grid.center(row, col))
        map_tau = int(taus[i_tau])
        release_time = scene_time - timedelta(hours=map_tau)
        j_tau, j_row, j_col = np.unravel_index(int(np.argmax(post)), post.shape)
        j_lon, j_lat = (float(v) for v in grid.center(j_row, j_col))
        tau_lo, tau_hi = hdr_interval(taus, p_tau, cfg.credible_mass)

        ctx.emit.step("tracing 50% and 90% highest-density regions", 65)
        mask50, mask90 = hdr_mask(spatial, 0.50), hdr_mask(spatial, cfg.credible_mass)
        geom50, geom90 = mask_to_geometry(mask50, grid), mask_to_geometry(mask90, grid)
        area90 = float(mask90.sum() * grid.cell_area_km2)

        ctx.emit.step("checking for multiple modes", 80)
        labels, n_labels = ndimage.label(mask90, structure=np.ones((3, 3)))
        modes: list[dict[str, Any]] = []
        for lab in range(1, n_labels + 1):
            region = labels == lab
            mass = float(spatial[region].sum())
            if mass < cfg.mode_min_mass:
                continue
            sub = np.where(region[None, :, :], post, 0.0)
            it, r, c = np.unravel_index(int(np.argmax(sub)), sub.shape)
            lon, lat = (float(v) for v in grid.center(r, c))
            modes.append({"mass": round(mass, 4), "lon": lon, "lat": lat, "tau_h": int(taus[it]),
                          "release_time": (scene_time - timedelta(hours=int(taus[it]))).isoformat()})
        modes.sort(key=lambda m: m["mass"], reverse=True)
        age_modes = tau_modes(taus, p_tau)
        multi_modal = len(modes) > 1 or len(age_modes) > 1

        with rasterio.open(ctx.path("posterior_spatial.tif"), "w", driver="GTiff", height=grid.ny,
                           width=grid.nx, count=1, dtype="float32", crs="EPSG:4326",
                           transform=grid.transform, compress="deflate") as dst:
            dst.write(spatial.astype(np.float32), 1)

        result: dict[str, Any] = {
            "scene_time": scene_time.isoformat(),
            "map": {"lon": map_lon, "lat": map_lat, "tau_h": map_tau, "release_time": release_time.isoformat(),
                    "p_tau": float(p_tau[i_tau])},
            "joint_cell_map": {"lon": j_lon, "lat": j_lat, "tau_h": int(taus[j_tau])},
            "release_window": {"tau_lo_h": tau_lo, "tau_hi_h": tau_hi,
                               "earliest": (scene_time - timedelta(hours=tau_hi)).isoformat(),
                               "latest": (scene_time - timedelta(hours=tau_lo)).isoformat(),
                               "credible_mass": cfg.credible_mass},
            "hdr50": geojson_of(geom50) if geom50 is not None else None,
            "hdr90": geojson_of(geom90) if geom90 is not None else None,
            "hdr90_area_km2": round(area90, 2),
            "p_tau": {"tau_hours": taus.tolist(), "probability": [float(v) for v in p_tau]},
            "multi_modal": multi_modal,
            "modes": modes,
            "age_modes": age_modes,
            "slick_type": ctx.state.get("slick_type"),
            "slick": ctx.require("slick_geojson"),
            "forcing_source": ctx.state.get("forcing_source"),
        }

        truth = ctx.request.get("truth")
        if truth:   # demo / test jobs only: how close did the hindcast get?
            d_x = (truth["lon"] - map_lon) * M_PER_DEG_LON_EQ * math.cos(math.radians(map_lat))
            d_y = (truth["lat"] - map_lat) * M_PER_DEG_LAT
            inside = bool(geom90 is not None and geom90.covers(Point(truth["lon"], truth["lat"])))
            result["truth_check"] = {"lon": truth["lon"], "lat": truth["lat"], "tau_h": truth.get("tau_h"),
                                     "map_error_km": round(math.hypot(d_x, d_y) / 1000.0, 2),
                                     "inside_hdr90": inside,
                                     "tau_inside_window": (tau_lo <= truth["tau_h"] <= tau_hi)
                                     if truth.get("tau_h") is not None else None}
            ctx.emit.log(f"truth check: MAP is {result['truth_check']['map_error_km']} km from the true origin; "
                         f"true origin {'inside' if inside else 'OUTSIDE'} the 90% region")
        if multi_modal:
            ctx.emit.log(f"posterior is MULTI-MODAL: {len(modes)} spatial mode(s), {len(age_modes)} age mode(s) "
                         f"-- all reported, none averaged")

        ctx.state["result"] = result
        half = 0.5 * (tau_hi - tau_lo)
        metrics = {"map_position": f"{map_lat:.4f}, {map_lon:.4f}",
                   "release_window": f"{release_time:%m-%d %H:%MZ} ± {half:.0f} h",
                   "hdr90_area_km2": round(area90, 1), "modes": len(modes) if modes else 1,
                   "map_tau_h": map_tau, "multi_modal": multi_modal}
        if truth:
            metrics["truth_error_km"] = result["truth_check"]["map_error_km"]
            metrics["truth_inside_hdr90"] = result["truth_check"]["inside_hdr90"]
        return EngineResult(metrics=metrics,
                            summary=f"MAP origin ({map_lat:.4f}, {map_lon:.4f}), released {release_time.isoformat()}")
