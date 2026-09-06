"""Learned residual correction for the drift integrator (the ML half of the hindcast).

What this is, precisely
-----------------------
SIH 26143's Expected Solution asks for "an automated detection **and hindcasting**
machine learning model". OceanTrace's detection half is machine learning; the drift
half was pure physics. This module is the learned component in the hindcast path, and
it is deliberately scoped to something a model can actually learn well:

    **the error the operational integrator makes because its timestep is coarse.**

The drift engine advances particles with forward Euler at a 600 s timestep. Integrating
the *same* forcing at 60 s is strictly more accurate - forward Euler's local truncation
error is O(dt^2) and it accumulates wherever the flow is sheared or curved. The gap
between the two is a real, systematic, position-dependent quantity. It is not noise,
and it is not something a hand-tuned constant can remove, because it depends on the
local velocity gradients each particle happens to be sitting in.

So the learning task is honest and well-posed:

    input   local flow state around a particle (velocity, shear, curvature, wind, lat)
    target  displacement difference between a 600 s Euler step and ten 60 s Euler steps
    output  a metre-scale correction added to each operational step

This is a **discretisation-error correction**, not a claim that the model knows where
oil goes better than physics does. Physics still does the advection; the model removes
a known, measurable bias in how that physics is integrated. Anyone can check it: the
evaluation reports position RMSE against the fine-timestep reference, with and without
the correction, on particles the model never saw.

Why not learn "real drift" directly?
------------------------------------
Because there is no ground truth. No drifter buoys, no confirmed slick trajectories for
these scenes. Training a model against a target we do not have would be exactly the
fabrication this project refuses elsewhere. The one thing we *can* generate ground truth
for, cheaply and defensibly, is our own integration error - so that is what is learned.

Inference is pure NumPy
-----------------------
`analysis_engines` is dependency-free by design (no torch, no sklearn), so the trained
weights ship as a small ``.npz`` and inference is a hand-rolled MLP forward pass. That
keeps the engine deployable anywhere and makes inference bit-deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Model artefact shipped beside the engine.
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "weights" / "drift_residual.npz"

FEATURE_NAMES = (
    "u", "v", "speed",
    "du_dx", "du_dy", "dv_dx", "dv_dy",     # local velocity gradients (shear/divergence)
    "wind_u", "wind_v", "wind_speed",
    "lat_rad_cos",
    "dt_s_scaled",
    "direction",
)
N_FEATURES = len(FEATURE_NAMES)

# Finite-difference step for the velocity gradients, in metres.
_GRAD_EPS_M = 500.0


@dataclass(frozen=True)
class ResidualModel:
    """A trained MLP: features -> (dx_m, dy_m) correction for one operational step."""

    w1: np.ndarray
    b1: np.ndarray
    w2: np.ndarray
    b2: np.ndarray
    w3: np.ndarray
    b3: np.ndarray
    x_mean: np.ndarray
    x_std: np.ndarray
    y_scale: np.ndarray
    metadata: dict[str, Any]

    def predict(self, feats: np.ndarray) -> np.ndarray:
        """(n, N_FEATURES) -> (n, 2) correction in metres. Deterministic."""
        x = (np.asarray(feats, dtype=np.float64) - self.x_mean) / self.x_std
        h = np.tanh(x @ self.w1 + self.b1)
        h = np.tanh(h @ self.w2 + self.b2)
        return (h @ self.w3 + self.b3) * self.y_scale

    @property
    def version(self) -> str:
        return str(self.metadata.get("model_version", "unknown"))


def load_model(path: str | Path | None = None) -> ResidualModel | None:
    """Load the residual model, or None when it is not present.

    Returning None rather than raising is deliberate: the correction is an enhancement,
    and a missing artefact must degrade to pure physics with a warning, never break a run.
    """
    p = Path(path) if path is not None else DEFAULT_MODEL_PATH
    if not p.exists():
        return None
    with np.load(p, allow_pickle=False) as z:
        meta = json.loads(str(z["metadata_json"])) if "metadata_json" in z else {}
        return ResidualModel(
            w1=z["w1"], b1=z["b1"], w2=z["w2"], b2=z["b2"], w3=z["w3"], b3=z["b3"],
            x_mean=z["x_mean"], x_std=z["x_std"], y_scale=z["y_scale"], metadata=meta,
        )


def build_features(
    metocean,
    t_s: float,
    lons: np.ndarray,
    lats: np.ndarray,
    dt_s: float,
    direction: int,
) -> np.ndarray:
    """Local flow state for each particle, as the model expects it.

    Gradients are estimated by central differences in a local metric frame, which is
    what carries the shear information the correction depends on.
    """
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)
    n = lons.size

    u, v = metocean.drift_velocity(t_s, lons, lats)
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)

    # metre -> degree at each particle's own latitude
    m_per_deg_lat = 110574.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(lats))
    m_per_deg_lon = np.where(np.abs(m_per_deg_lon) < 1.0, 1.0, m_per_deg_lon)

    dlon = _GRAD_EPS_M / m_per_deg_lon
    dlat = _GRAD_EPS_M / m_per_deg_lat

    ue, ve = metocean.drift_velocity(t_s, lons + dlon, lats)
    uw, vw = metocean.drift_velocity(t_s, lons - dlon, lats)
    un, vn = metocean.drift_velocity(t_s, lons, lats + dlat)
    us, vs = metocean.drift_velocity(t_s, lons, lats - dlat)

    du_dx = (np.asarray(ue) - np.asarray(uw)) / (2.0 * _GRAD_EPS_M)
    dv_dx = (np.asarray(ve) - np.asarray(vw)) / (2.0 * _GRAD_EPS_M)
    du_dy = (np.asarray(un) - np.asarray(us)) / (2.0 * _GRAD_EPS_M)
    dv_dy = (np.asarray(vn) - np.asarray(vs)) / (2.0 * _GRAD_EPS_M)

    wind = getattr(metocean, "wind", None)
    if wind is not None:
        wu, wv = wind.sample(t_s, lons, lats)
        wu = np.asarray(wu, dtype=np.float64)
        wv = np.asarray(wv, dtype=np.float64)
    else:
        wu = np.zeros(n)
        wv = np.zeros(n)

    feats = np.empty((n, N_FEATURES), dtype=np.float64)
    feats[:, 0] = u
    feats[:, 1] = v
    feats[:, 2] = np.hypot(u, v)
    feats[:, 3] = du_dx
    feats[:, 4] = du_dy
    feats[:, 5] = dv_dx
    feats[:, 6] = dv_dy
    feats[:, 7] = wu
    feats[:, 8] = wv
    feats[:, 9] = np.hypot(wu, wv)
    feats[:, 10] = np.cos(np.radians(lats))
    feats[:, 11] = dt_s / 600.0
    feats[:, 12] = float(np.sign(direction) or 1.0)
    return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)


def apply_correction(
    model: ResidualModel | None,
    metocean,
    t_s: float,
    lons: np.ndarray,
    lats: np.ndarray,
    dt_s: float,
    direction: int,
    max_correction_m: float = 400.0,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (dlon_deg, dlat_deg, mean_correction_m) for one operational step.

    ``max_correction_m`` is a guard rail, not a tuning knob: a discretisation
    correction that exceeds a few hundred metres in one 10-minute step is out of
    distribution, and clipping keeps a bad extrapolation from steering the run.
    """
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)
    if model is None:
        z = np.zeros(lons.size)
        return z, z, 0.0

    feats = build_features(metocean, t_s, lons, lats, dt_s, direction)
    corr = model.predict(feats)

    mag = np.hypot(corr[:, 0], corr[:, 1])
    over = mag > max_correction_m
    if np.any(over):
        scale = np.ones_like(mag)
        scale[over] = max_correction_m / mag[over]
        corr = corr * scale[:, None]

    m_per_deg_lat = 110574.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(lats))
    m_per_deg_lon = np.where(np.abs(m_per_deg_lon) < 1.0, 1.0, m_per_deg_lon)

    dlon = corr[:, 0] / m_per_deg_lon
    dlat = corr[:, 1] / m_per_deg_lat
    return dlon, dlat, float(np.mean(np.hypot(corr[:, 0], corr[:, 1])))
