"""Per-job hindcast configuration, and where a job keeps its files.

`PipelineConfig` is science: ensemble size, parameter ranges, score weights.
Every job stores the config it ran with, so a result can always be traced to
the numbers that produced it. A job may override any field.

Deployment (database, data root) is OceanTrace's own `backend.core.config`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.core.config import get_settings


def job_dir(job_id: str) -> Path:
    path = get_settings().data_root / "hindcast" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def drift_backend_preference() -> str:
    """auto = OpenDrift when importable, else the numpy RK4 integrator."""
    value = os.getenv("HINDCAST_DRIFT_BACKEND", "auto").lower()
    return value if value in ("auto", "numpy", "opendrift") else "auto"


OilType = Literal["light", "medium", "heavy"]

# Stage 0(c): how long each oil class can persist as a SAR-visible slick.
PERSISTENCE_CEILING_H: dict[str, int] = {"light": 48, "medium": 120, "heavy": 336}


class ParamRange(BaseModel):
    lo: float
    hi: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.lo + self.hi)


class ForwardWeights(BaseModel):
    """L_fwd = w1*IoU + w2*exp(-centroid_err/sigma) + w3*area_ratio + w4*orientation."""

    iou: float = 0.4
    centroid: float = 0.3
    area: float = 0.2
    orientation: float = 0.1
    centroid_sigma_m: float = 3000.0


class PipelineConfig(BaseModel):
    seed: int = 42
    # --- Stage 2: ensemble backward drift --------------------------------
    members: int = Field(100, ge=1, le=1000)
    particles: int = Field(2000, ge=10, le=50000)
    member_chunk: int = Field(10, ge=1, description="members integrated together in one vectorised pass")
    dt_s: float = 3600.0
    alpha: ParamRange = ParamRange(lo=0.025, hi=0.045)          # wind drift factor
    theta_deg: ParamRange = ParamRange(lo=0.0, hi=20.0)          # wind deflection
    beta: ParamRange = ParamRange(lo=0.8, hi=1.2)                # Stokes scaling
    k_diff: ParamRange = ParamRange(lo=1.0, hi=30.0)             # m^2/s
    current_noise: float = 0.15                                  # +/- fraction
    snapshot_particles: int = Field(300, ge=1, description="particles per member saved to parquet")
    grid_max_cells: int = Field(160, ge=32, le=512, description="longest KDE raster side")
    kde_sigma_cells: float = 1.5
    # --- Stage 0 ---------------------------------------------------------
    tau_min_h: int = 1
    age_prior_sigma: float = 0.9                                 # lognormal width
    # --- Stage 3 ---------------------------------------------------------
    spill_volume_m3: float = 50.0
    shape_orientation_sigma_deg: float = 35.0
    shape_curvature_sigma_deg: float = 40.0
    shape_area_sigma_ln: float = 1.5
    # --- Stage 4 ---------------------------------------------------------
    verify_candidates: int = Field(200, ge=1, le=2000)
    verify_particles: int = Field(400, ge=50, le=5000)
    forward_weights: ForwardWeights = ForwardWeights()
    # --- Stage 5 ---------------------------------------------------------
    # L_fwd is a 0..1 score, not a likelihood: the gap between a good and a poor
    # candidate is small in absolute terms. Raising it to a power is what makes
    # the forward evidence count against the three cheaper terms.
    fwd_exponent: float = Field(3.0, ge=0.0, le=20.0)
    credible_mass: float = Field(0.90, gt=0.5, lt=1.0)
    mode_min_mass: float = 0.10
