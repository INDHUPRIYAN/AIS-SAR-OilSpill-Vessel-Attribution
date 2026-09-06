"""Learned origin-recovery correction — the ML component of the hindcast (v2).

Why this target, and not the previous one
-----------------------------------------
The first attempt (`ml_residual.py`) learned the integrator's per-step truncation error.
`docs/qa/evidence/ml_hindcast/root_cause_analysis.md` measured why that failed: the
target is 0.52 % of the advective step and 1/115th of the per-step diffusive noise, and a
per-step correction integrates any bias 144 times over a 24 h run (measured accumulated
bias: 161 m). It was disabled on that evidence.

This module targets the quantity SIH 26143 actually asks for — **the origin location** —
and applies **one** correction to the finished hindcast rather than a correction per step.
That satisfies the five constraints the root-cause analysis derived: real ground truth,
signal large enough to matter, survives diffusion, cannot accumulate, and is measured on
the operational quantity.

Ground truth (and its honest limit)
-----------------------------------
Ground truth is generated **closed-loop** on real CMEMS + ERA5 forcing:

1. seed a known release point ``O`` at time ``T0`` in a real forcing field;
2. integrate **forward** ``H`` hours with diffusion on -> a particle cloud, which is what
   a satellite would have seen at detection time;
3. run the ordinary OceanTrace **hindcast** on that cloud -> estimated origin ``O'``;
4. the label is the recovery error ``O - O'`` in metres.

The hindcast genuinely fails to recover ``O`` exactly, for reasons that are systematic
rather than random: **diffusion is irreversible** (a cloud that spread forward does not
re-converge when integrated backward), the origin-window selection is a minimum-spread
heuristic, and backward integration through a time-varying field is not the exact inverse
of the forward pass. Those biases are learnable.

**The limit, stated plainly:** because the label comes from a forward run using the *same*
forcing, this corrects **hindcast-process bias only**. It cannot correct error in the
forcing itself (CMEMS/ERA5 resolution, missing tides). A run whose forcing is wrong will
still have a wrong origin, and this model will not know. That is why the correction is
bounded and why its provenance is recorded in every output.

Inference is pure NumPy so `analysis_engines` stays dependency-free, and is deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "weights" / "origin_correction.npz"

FEATURE_NAMES = (
    "backtrack_hours",
    "cloud_sigma_x_km",          # spread of the detected cloud, local metric frame
    "cloud_sigma_y_km",
    "cloud_elongation",          # major/minor of the detected cloud
    "backtrack_dx_km",           # displacement the hindcast itself produced
    "backtrack_dy_km",
    "backtrack_distance_km",
    "mean_current_u",            # forcing actually sampled along the run
    "mean_current_v",
    "mean_current_speed",
    "mean_wind_u",
    "mean_wind_v",
    "mean_wind_speed",
    "cos_lat",
    "spread_growth_ratio",       # cloud sigma at origin / at detection
)
N_FEATURES = len(FEATURE_NAMES)

# A hindcast origin correction larger than this is out of distribution; clipping keeps a
# bad extrapolation from moving the origin somewhere an investigator would be misled by.
MAX_CORRECTION_KM = 5.0


@dataclass(frozen=True)
class OriginModel:
    """Ridge/MLP hybrid: features -> (dx_km, dy_km) correction of the origin estimate."""

    w1: np.ndarray
    b1: np.ndarray
    w2: np.ndarray
    b2: np.ndarray
    x_mean: np.ndarray
    x_std: np.ndarray
    y_mean: np.ndarray
    y_std: np.ndarray
    metadata: dict[str, Any]

    def predict(self, feats: np.ndarray) -> np.ndarray:
        x = (np.atleast_2d(np.asarray(feats, dtype=np.float64)) - self.x_mean) / self.x_std
        h = np.tanh(x @ self.w1 + self.b1)
        return (h @ self.w2 + self.b2) * self.y_std + self.y_mean

    @property
    def version(self) -> str:
        return str(self.metadata.get("model_version", "unknown"))

    @property
    def sha256(self) -> str:
        return str(self.metadata.get("checkpoint_sha256", ""))


def load_model(path: str | Path | None = None) -> OriginModel | None:
    """Load the origin-correction model, or None when absent.

    Absent means the hindcast runs pure physics and says so; it is never an error.
    """
    p = Path(path) if path is not None else DEFAULT_MODEL_PATH
    if not p.exists():
        return None
    with np.load(p, allow_pickle=False) as z:
        meta = json.loads(str(z["metadata_json"])) if "metadata_json" in z else {}
        return OriginModel(
            w1=z["w1"], b1=z["b1"], w2=z["w2"], b2=z["b2"],
            x_mean=z["x_mean"], x_std=z["x_std"],
            y_mean=z["y_mean"], y_std=z["y_std"], metadata=meta,
        )


def _sigma_km(lons: np.ndarray, lats: np.ndarray) -> tuple[float, float]:
    """Cloud spread in kilometres, in a local metric frame at its own centroid."""
    lat0 = float(np.mean(lats))
    x = (np.asarray(lons) - float(np.mean(lons))) * 111.320 * np.cos(np.radians(lat0))
    y = (np.asarray(lats) - lat0) * 110.574
    return float(np.std(x)), float(np.std(y))


def build_features(
    *,
    backtrack_hours: float,
    detect_lons: np.ndarray,
    detect_lats: np.ndarray,
    origin_lons: np.ndarray,
    origin_lats: np.ndarray,
    mean_current_uv: tuple[float, float],
    mean_wind_uv: tuple[float, float],
) -> np.ndarray:
    """One feature row describing a completed hindcast."""
    sx_d, sy_d = _sigma_km(detect_lons, detect_lats)
    sx_o, sy_o = _sigma_km(origin_lons, origin_lats)

    lat0 = float(np.mean(detect_lats))
    dx = (float(np.mean(origin_lons)) - float(np.mean(detect_lons))) * 111.320 * np.cos(
        np.radians(lat0))
    dy = (float(np.mean(origin_lats)) - lat0) * 110.574

    major = max(sx_d, sy_d)
    minor = max(min(sx_d, sy_d), 1e-6)
    spread_d = float(np.hypot(sx_d, sy_d))
    spread_o = float(np.hypot(sx_o, sy_o))

    cu, cv = mean_current_uv
    wu, wv = mean_wind_uv

    return np.array([[
        float(backtrack_hours),
        sx_d, sy_d,
        major / minor,
        dx, dy, float(np.hypot(dx, dy)),
        cu, cv, float(np.hypot(cu, cv)),
        wu, wv, float(np.hypot(wu, wv)),
        float(np.cos(np.radians(lat0))),
        spread_o / max(spread_d, 1e-6),
    ]], dtype=np.float64)


def correct_origin(
    model: OriginModel | None,
    origin_lon: float,
    origin_lat: float,
    features: np.ndarray,
    max_km: float = MAX_CORRECTION_KM,
) -> tuple[float, float, dict[str, Any]]:
    """Apply the learned correction to a hindcast origin estimate.

    Returns ``(lon, lat, provenance)``. With no model the origin is returned unchanged
    and the provenance says the correction was not applied.
    """
    if model is None:
        return origin_lon, origin_lat, {
            "applied": False, "model": None,
            "reason": "no origin-correction model present; pure physics hindcast",
        }

    corr = np.asarray(model.predict(features), dtype=np.float64).reshape(-1)[:2]
    if not np.all(np.isfinite(corr)):
        return origin_lon, origin_lat, {
            "applied": False, "model": model.version,
            "reason": "model produced a non-finite correction; physics origin kept",
        }

    dist = float(np.hypot(corr[0], corr[1]))
    clipped = False
    if dist > max_km:
        corr = corr * (max_km / dist)
        clipped = True

    lat_out = origin_lat + float(corr[1]) / 110.574
    lon_out = origin_lon + float(corr[0]) / (111.320 * np.cos(np.radians(origin_lat)))

    # Latitude/longitude must stay physical whatever the model says.
    lat_out = float(np.clip(lat_out, -90.0, 90.0))
    lon_out = float(((lon_out + 180.0) % 360.0) - 180.0)

    return lon_out, lat_out, {
        "applied": True,
        "model": model.version,
        "model_sha256": model.sha256,
        "correction_km": round(float(np.hypot(corr[0], corr[1])), 4),
        "correction_dx_km": round(float(corr[0]), 4),
        "correction_dy_km": round(float(corr[1]), 4),
        "clipped_at_max_km": clipped,
        "max_correction_km": max_km,
        "corrects": "systematic hindcast origin-recovery bias (diffusion irreversibility, "
                    "origin-window heuristic, backward-integration asymmetry)",
        "does_not_correct": "error in the forcing itself (CMEMS/ERA5 resolution, missing tides)",
    }
