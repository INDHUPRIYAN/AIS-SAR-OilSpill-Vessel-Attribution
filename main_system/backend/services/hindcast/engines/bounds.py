"""Engine 2 -- Search Window Bounds (Stage 0).

Three independent limits on the slick's age tau, combined into an hourly grid
with prior weights:

  (a) archive bound   -- the latest earlier scene over this water WITHOUT a
                         slick is a hard tau_max: the oil was not there yet.
                         An earlier scene WITH a slick raises tau_min.
  (b) weathering prior -- P_age(tau), lognormal around an age read off the
                         slick's look: ragged edges, fragmentation and sheer
                         size all say "older".
  (c) persistence      -- no slick of this oil type stays SAR-visible longer.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from shapely.geometry.base import BaseGeometry

from backend.services.hindcast.config import PERSISTENCE_CEILING_H
from backend.services.hindcast.engines.base import Engine, EngineError, EngineResult, PipelineContext
from backend.services.hindcast.engines.ingest import parse_time
from backend.services.hindcast.geo import edge_roughness, load_geometry


@dataclass
class ArchiveHit:
    scene_time: datetime
    slick_present: bool


ArchiveProvider = Callable[[BaseGeometry, datetime, datetime], list[ArchiveHit]]


def db_archive_provider(aoi: BaseGeometry, t_from: datetime, t_to: datetime) -> list[ArchiveHit]:
    """Prior scenes whose footprint intersects the slick, within the time window.

    The time filter runs in SQL; the intersection runs in shapely, because this
    database is SQLite and has no spatial index to ask. The table is small (one
    row per acquisition over an AOI), so this is not the hot path.
    """
    from sqlalchemy import select

    from backend.models.hindcast import HindcastArchiveScene as ArchiveScene, session_scope

    with session_scope() as db:
        rows = db.execute(select(ArchiveScene).where(ArchiveScene.scene_time >= t_from,
                                                     ArchiveScene.scene_time < t_to)).scalars().all()
        # SQLite drops the offset on the way back; every stored time is UTC.
        hits = [ArchiveHit(parse_time(r.scene_time), r.slick_present) for r in rows
                if load_footprint(r.aoi).intersects(aoi)]
    return sorted(hits, key=lambda h: h.scene_time)


def load_footprint(value: str) -> BaseGeometry:
    from shapely.geometry import shape
    import json

    return shape(json.loads(value))


def estimate_age_hours(area_km2: float, fragments: int, roughness: float) -> float:
    """A deliberately coarse read of age from appearance (hours).

    Fresh discharges are compact, single-piece and clean-edged. Spreading grows
    the area, wave action breaks the sheet up and feathers its edges. The prior
    built on this is wide (see `age_prior_sigma`): it nudges, it does not decide.
    """
    base = 6.0
    size = math.sqrt(max(area_km2, 0.05) / 2.0)
    broken = 1.0 + 0.6 * max(0, fragments - 1)
    ragged = 1.0 + 2.5 * roughness
    return float(base * size * broken * ragged)


class BoundsEngine(Engine):
    id = "bounds_engine"
    name = "Search Window Bounds"
    description = "Bounds the slick's age from the scene archive, its weathering look, and oil persistence."
    stage = "Stage 0"
    order = 2
    icon = "Timer"
    metric_keys = [{"key": "tau_max_h", "label": "τ max", "unit": "h"},
                   {"key": "prior_peak_age_h", "label": "Prior peak age", "unit": "h"},
                   {"key": "limited_by", "label": "Limited by"}]

    def __init__(self, archive_provider: Optional[ArchiveProvider] = None) -> None:
        self.archive_provider = archive_provider or db_archive_provider

    def run(self, ctx: PipelineContext) -> EngineResult:
        cfg = ctx.config
        scene_time = ctx.scene_time
        slick = load_geometry(ctx.require("slick_geojson"))
        stats = ctx.require("slick_stats")

        oil_type = ctx.request.get("oil_type") or "heavy"
        if oil_type not in PERSISTENCE_CEILING_H:
            raise EngineError(f"unknown oil_type '{oil_type}'; expected one of {sorted(PERSISTENCE_CEILING_H)}")
        ceiling = PERSISTENCE_CEILING_H[oil_type]
        ctx.emit.step(f"persistence ceiling for {oil_type} oil: {ceiling} h", 15)

        ctx.emit.step("querying scene archive over the AOI", 35)
        hits = self.archive_provider(slick, scene_time - timedelta(hours=ceiling), scene_time)
        tau_max, tau_min, limited_by = float(ceiling), float(cfg.tau_min_h), f"{oil_type} persistence"
        absent = [h for h in hits if not h.slick_present]
        if absent:
            last_clean = max(h.scene_time for h in absent)
            archive_tau = (scene_time - last_clean).total_seconds() / 3600.0
            if archive_tau < tau_max:
                tau_max, limited_by = archive_tau, f"clean scene {last_clean:%Y-%m-%d %H:%MZ}"
            # Only sightings AFTER the last clean pass are this slick.
            present = [h for h in hits if h.slick_present and h.scene_time > last_clean]
        else:
            present = [h for h in hits if h.slick_present]
        if present:
            first_seen = min(h.scene_time for h in present)
            tau_min = max(tau_min, (scene_time - first_seen).total_seconds() / 3600.0)
        ctx.emit.log(f"archive: {len(hits)} prior scene(s), {len(absent)} clean, {len(present)} with this slick")

        tau_lo, tau_hi = int(math.ceil(tau_min)), int(math.floor(tau_max))
        if tau_hi < tau_lo:
            raise EngineError(f"empty search window: tau_min {tau_min:.1f} h exceeds tau_max {tau_max:.1f} h")
        taus = np.arange(tau_lo, tau_hi + 1, dtype=np.int64)

        ctx.emit.step("building weathering prior P_age(τ)", 70)
        roughness = edge_roughness(slick)
        age = float(np.clip(estimate_age_hours(stats["area_km2"], stats["fragments"], roughness),
                            taus[0], taus[-1]))
        log_pdf = -0.5 * ((np.log(taus) - math.log(age)) / cfg.age_prior_sigma) ** 2 - np.log(taus)
        prior = np.exp(log_pdf - log_pdf.max())
        prior /= prior.sum()
        peak = int(taus[int(np.argmax(prior))])

        ctx.state.update({
            "oil_type": oil_type,
            "tau_hours": taus.tolist(),
            "p_age": prior.tolist(),
            "tau_max_h": int(taus[-1]),
            "tau_min_h": int(taus[0]),
            "archive_mask": [1.0] * int(taus.size),   # the hard bound is already the grid's edge
            "recurring_sightings": len([h for h in hits if h.slick_present]),
            "weathering": {"edge_roughness": round(roughness, 4), "age_estimate_h": round(age, 2)},
        })
        metrics = {"tau_min_h": int(taus[0]), "tau_max_h": int(taus[-1]), "prior_peak_age_h": peak,
                   "limited_by": limited_by, "edge_roughness": round(roughness, 3),
                   "archive_scenes": len(hits)}
        return EngineResult(metrics=metrics, summary=f"tau in [{taus[0]}, {taus[-1]}] h, prior peaks at {peak} h")
