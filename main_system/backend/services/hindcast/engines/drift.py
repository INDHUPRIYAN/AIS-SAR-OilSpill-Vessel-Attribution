"""Engine 4 -- Ensemble Backward Drift (Stage 2).

Three phases, so that the member chunks stay independent of each other and
could be handed to separate workers without touching this file:

    prepare()      seeds, per-member parameters, and the output grid
    run_members()  integrates a contiguous chunk of members in one vectorised pass
    finalize()     sums the chunk histograms into L_drift(x|tau) and writes it

`run()` executes them in order in this process.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Optional

import numpy as np
import pandas as pd
import rasterio

from backend.services.hindcast.config import drift_backend_preference
from backend.services.hindcast.engines.base import Engine, EngineError, EngineResult, PipelineContext
from backend.services.hindcast.engines.ingest import damping_weight_fn
from backend.services.hindcast.forcing import ForcingField
from backend.services.hindcast.geo import M_PER_DEG_LAT, RasterGrid, load_geometry, sample_in_polygon, smooth_density
from backend.services.hindcast.integrator import MemberParams, NumpyRK4Backend, get_backend

SnapshotSink = Callable[[str, list[dict[str, Any]]], None]


def db_snapshot_sink(job_id: str, rows: list[dict[str, Any]]) -> None:
    from sqlalchemy import delete, insert

    from backend.models.hindcast import HindcastParticleSnapshot as ParticleSnapshot, session_scope

    with session_scope() as db:
        db.execute(delete(ParticleSnapshot).where(ParticleSnapshot.job_id == job_id))
        for i in range(0, len(rows), 5000):
            db.execute(insert(ParticleSnapshot), rows[i:i + 5000])


def chunk_ranges(members: int, chunk: int) -> list[tuple[int, int]]:
    return [(a, min(a + chunk, members)) for a in range(0, members, chunk)]


def sample_member_params(cfg: Any, rng: np.random.Generator) -> dict[str, list[float]]:
    m = cfg.members
    return {
        "alpha": rng.uniform(cfg.alpha.lo, cfg.alpha.hi, m).tolist(),
        "theta_deg": rng.uniform(cfg.theta_deg.lo, cfg.theta_deg.hi, m).tolist(),
        "beta": rng.uniform(cfg.beta.lo, cfg.beta.hi, m).tolist(),
        "k_diff": rng.uniform(cfg.k_diff.lo, cfg.k_diff.hi, m).tolist(),
        "current_scale": rng.uniform(1 - cfg.current_noise, 1 + cfg.current_noise, m).tolist(),
    }


def expand(params: dict[str, list[float]], start: int, stop: int, n: int) -> MemberParams:
    """Member values repeated per particle, members laid end to end."""
    def rep(key: str) -> np.ndarray:
        return np.repeat(np.asarray(params[key][start:stop], dtype=np.float64), n)

    return MemberParams(alpha=rep("alpha"), theta_deg=rep("theta_deg"), beta=rep("beta"),
                        current_scale=rep("current_scale"), k_diff=rep("k_diff"))


class DriftEngine(Engine):
    id = "drift_engine"
    name = "Ensemble Backward Drift"
    description = "Backtracks a perturbed-physics particle ensemble from the slick to map where it could have come from."
    stage = "Stage 2"
    order = 4
    icon = "Waves"
    metric_keys = [{"key": "members_done", "label": "Members"},
                   {"key": "particles_simulated", "label": "Particles"},
                   {"key": "snapshots_saved", "label": "Snapshots"}]

    def __init__(self, snapshot_sink: Optional[SnapshotSink] = None) -> None:
        self.snapshot_sink = snapshot_sink or db_snapshot_sink

    # ------------------------------------------------------------ phases ---
    def prepare(self, ctx: PipelineContext) -> None:
        cfg = ctx.config
        rng = np.random.default_rng(cfg.seed)
        slick = load_geometry(ctx.require("slick_geojson"))
        field = ForcingField.load(ctx.require("forcing_path"))
        taus: list[int] = ctx.require("tau_hours")
        tau_max = int(taus[-1])
        k_scene = field.index_of(ctx.scene_time)

        ctx.emit.step(f"seeding {cfg.particles} particles in the slick", 1)
        weight_fn = damping_weight_fn(ctx.state.get("damping_path"))
        lon, lat = sample_in_polygon(slick, cfg.particles, rng, weight_fn)
        np.save(ctx.path("seeds.npy"), np.stack([lon, lat]))
        ctx.emit.log("seeds weighted by the damping-ratio raster" if weight_fn else "seeds uniform in the polygon")

        params = sample_member_params(cfg, rng)
        ctx.path("members.json").write_text(json.dumps(params), encoding="utf-8")

        # Pilot: the extreme and central parameter sets, to size the output grid
        # BEFORE the ensemble runs (every chunk must bin onto the same raster).
        ctx.emit.step("pilot backtrack to size the output grid", 2)
        n_pilot = min(300, cfg.particles)
        corners = [(cfg.alpha.lo, cfg.theta_deg.lo, cfg.beta.lo, 1 - cfg.current_noise),
                   (cfg.alpha.mid, cfg.theta_deg.mid, cfg.beta.mid, 1.0),
                   (cfg.alpha.hi, cfg.theta_deg.hi, cfg.beta.hi, 1 + cfg.current_noise)]
        pilot = MemberParams(
            alpha=np.repeat([c[0] for c in corners], n_pilot), theta_deg=np.repeat([c[1] for c in corners], n_pilot),
            beta=np.repeat([c[2] for c in corners], n_pilot), current_scale=np.repeat([c[3] for c in corners], n_pilot),
            k_diff=np.zeros(3 * n_pilot))
        box = [lon.min(), lat.min(), lon.max(), lat.max()]

        def grow(_step: int, plon: np.ndarray, plat: np.ndarray) -> None:
            box[0], box[1] = min(box[0], plon.min()), min(box[1], plat.min())
            box[2], box[3] = max(box[2], plon.max()), max(box[3], plat.max())

        NumpyRK4Backend().integrate(field, np.tile(lon[:n_pilot], 3), np.tile(lat[:n_pilot], 3), pilot,
                                    k_scene, tau_max, True, rng, grow, cfg.dt_s)
        sigma_m = float(np.sqrt(2.0 * cfg.k_diff.hi * tau_max * cfg.dt_s))
        pad_lat = 3.0 * sigma_m / M_PER_DEG_LAT + 0.15 * (box[3] - box[1]) + 0.01
        pad_lon = pad_lat / max(np.cos(np.radians(0.5 * (box[1] + box[3]))), 0.05)
        grid = RasterGrid.around(box[0] - pad_lon, box[1] - pad_lat, box[2] + pad_lon, box[3] + pad_lat,
                                 cfg.grid_max_cells)
        ctx.state.update({"drift_grid": grid.to_dict(), "drift_chunks": chunk_ranges(cfg.members, cfg.member_chunk)})
        ctx.emit.metric(members_total=cfg.members, members_done=0, particles_simulated=0, snapshots_saved=0,
                        grid=f"{grid.nx}×{grid.ny}")
        ctx.emit.log(f"grid {grid.nx}×{grid.ny} cells, {grid.cell_area_km2 ** 0.5:.2f} km; "
                     f"{cfg.members} members × {cfg.particles} particles × {tau_max} h")

    def run_members(self, ctx: PipelineContext, start: int, stop: int) -> dict[str, Any]:
        cfg = ctx.config
        field = ForcingField.load(ctx.require("forcing_path"))
        grid = RasterGrid.from_dict(ctx.require("drift_grid"))
        taus: list[int] = ctx.require("tau_hours")
        tau_index = {int(t): i for i, t in enumerate(taus)}
        tau_max = int(taus[-1])
        k_scene = field.index_of(ctx.scene_time)
        seeds = np.load(ctx.path("seeds.npy"))
        params = json.loads(ctx.path("members.json").read_text(encoding="utf-8"))
        n, n_members = seeds.shape[1], stop - start
        member_params = expand(params, start, stop, n)
        rng = np.random.default_rng([cfg.seed, start])

        hist = np.zeros((len(taus), grid.ny, grid.nx), dtype=np.float32)
        keep = min(cfg.snapshot_particles, n)
        snap_lon = np.zeros((n_members, len(taus), keep), dtype=np.float32)
        snap_lat = np.zeros_like(snap_lon)
        label = f"member {start + 1}/{cfg.members}" if n_members == 1 else f"members {start + 1}–{stop}/{cfg.members}"

        def on_step(step: int, lon: np.ndarray, lat: np.ndarray) -> None:
            i = tau_index.get(step)
            if i is not None:
                hist[i] += grid.histogram(lon, lat)
                snap_lon[:, i, :] = lon.reshape(n_members, n)[:, :keep]
                snap_lat[:, i, :] = lat.reshape(n_members, n)[:, :keep]
            if step % 6 == 0 or step == tau_max:
                ctx.emit.step(f"{label} backtracking… hour {step}/{tau_max}")

        lon0, lat0 = np.tile(seeds[0], n_members), np.tile(seeds[1], n_members)
        backend = get_backend(drift_backend_preference())
        try:
            backend.integrate(field, lon0, lat0, member_params, k_scene, tau_max, True, rng, on_step, cfg.dt_s)
        except Exception as exc:  # noqa: BLE001 -- any OpenDrift failure must not lose the run
            if isinstance(backend, NumpyRK4Backend):
                raise
            ctx.emit.log(f"{backend.name} failed ({type(exc).__name__}: {exc}); falling back to numpy RK4")
            hist[:] = 0
            backend = NumpyRK4Backend()
            backend.integrate(field, lon0, lat0, member_params, k_scene, tau_max, True, rng, on_step, cfg.dt_s)

        (ctx.workdir / "members").mkdir(exist_ok=True)
        (ctx.workdir / "particles").mkdir(exist_ok=True)
        np.save(ctx.workdir / "members" / f"hist_{start:04d}_{stop:04d}.npy", hist)
        tau_col = np.repeat(np.asarray(taus, dtype=np.int16), keep)
        for j in range(n_members):
            pd.DataFrame({"tau_hours": tau_col, "lon": snap_lon[j].ravel(), "lat": snap_lat[j].ravel()}) \
                .to_parquet(ctx.workdir / "particles" / f"member_{start + j:04d}.parquet", index=False)
        ctx.emit.increment({"members_done": n_members, "particles_simulated": n_members * n,
                            "snapshots_saved": n_members * len(taus)},
                           progress=("members_done", "members_total"))
        if field.clamped:
            ctx.emit.log(f"{label}: {field.clamped} samples fell outside the forcing grid (clamped to its edge)")
        return {"start": start, "stop": stop, "backend": backend.name}

    def finalize(self, ctx: PipelineContext) -> EngineResult:
        cfg = ctx.config
        grid = RasterGrid.from_dict(ctx.require("drift_grid"))
        taus: list[int] = ctx.require("tau_hours")
        ctx.emit.step("summing member histograms", 96)
        files = sorted((ctx.workdir / "members").glob("hist_*.npy"))
        done = sum(int(f.stem.split("_")[2]) - int(f.stem.split("_")[1]) for f in files)
        if done != cfg.members:
            raise EngineError(f"only {done} of {cfg.members} ensemble members finished")
        total = np.zeros((len(taus), grid.ny, grid.nx), dtype=np.float64)
        for f in files:
            total += np.load(f)

        ctx.emit.step("kernel-smoothing L_drift(x|τ) and writing GeoTIFF", 98)
        inside = total.reshape(len(taus), -1).sum(axis=1) / float(cfg.members * cfg.particles)
        dens = np.stack([smooth_density(total[i], cfg.kde_sigma_cells) for i in range(len(taus))]).astype(np.float32)
        profile = {"driver": "GTiff", "height": grid.ny, "width": grid.nx, "count": len(taus), "dtype": "float32",
                   "crs": "EPSG:4326", "transform": grid.transform, "compress": "deflate", "predictor": 3}
        with rasterio.open(ctx.path("l_drift.tif"), "w", **profile) as dst:
            dst.write(dens)
            for i, tau in enumerate(taus):
                dst.set_band_description(i + 1, f"L_drift tau={tau}h")

        rows = [{"job_id": ctx.job_id, "member": m, "tau_hours": int(tau),
                 "parquet_path": str(ctx.workdir / "particles" / f"member_{m:04d}.parquet")}
                for m in range(cfg.members) for tau in taus]
        self.snapshot_sink(ctx.job_id, rows)
        for f in files:
            f.unlink()

        ctx.state.update({"l_drift_path": str(ctx.path("l_drift.tif")),
                          "drift_mass_inside_grid": float(inside.min())})
        if inside.min() < 0.98:
            ctx.emit.log(f"warning: {100 * (1 - inside.min()):.1f}% of particles left the output grid at the oldest age")
        return EngineResult(metrics={"members_done": cfg.members, "members_total": cfg.members,
                                     "particles_simulated": cfg.members * cfg.particles,
                                     "snapshots_saved": cfg.members * len(taus)},
                            summary=f"L_drift for {len(taus)} ages on a {grid.nx}×{grid.ny} grid")

    # ------------------------------------------------------------ inline ---
    def run(self, ctx: PipelineContext) -> EngineResult:
        self.prepare(ctx)
        chunks = ctx.require("drift_chunks")
        for i, (start, stop) in enumerate(chunks):
            self.run_members(ctx, start, stop)
            ctx.emit.step(f"member {stop}/{ctx.config.members} backtracked", 3 + 92 * (i + 1) / len(chunks))
        return self.finalize(ctx)


def read_l_drift(path: str) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read().astype(np.float64)
