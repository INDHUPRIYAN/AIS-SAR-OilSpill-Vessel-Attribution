"""The six attribution factors (handbook §4.8), each normalised to [0, 1].

    proximity    depth of the track inside the origin cloud, weighted by cloud density
    temporal     alignment between presence and the estimated discharge window
    trajectory   course vs the slick major axis, plus path-overlap length
    anomaly      unusual slowdown, course change, loitering
    ais_gap      transmission blackout over the origin window
    prior        vessel type / draft prior

The total is a plain weighted sum with weights from ``config/attribution_weights.yaml``.

This is deliberately **not** a trained classifier (handbook pitfall #8): no ground truth
for attribution exists, and the use case requires that every number can be explained to
an investigator. Every factor here is a transparent, hand-checkable quantity, and each
one records the evidence behind it so the explanation generator can quote it.

Only vessels that passed the gates are scored. Filtered vessels carry their
``filter_reason`` instead, exactly as the §4.4 example shows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..common.geo import LocalFrame, covariance_ellipse
from .gates import axis_offset_deg

FACTORS = ("proximity", "temporal", "trajectory", "anomaly", "ais_gap", "prior")

DEFAULT_WEIGHTS = {
    "proximity": 0.30, "temporal": 0.20, "trajectory": 0.20,
    "anomaly": 0.10, "ais_gap": 0.15, "prior": 0.05,
}

# Type priors: how plausible a discharge is per vessel class, before any evidence.
DEFAULT_PRIORS = {
    "tanker": 1.00,
    "bulk carrier": 0.80,
    "cargo": 0.70,
    "tug": 0.45,
    "fishing": 0.40,
    "passenger": 0.20,
    "ferry": 0.20,
    "_default": 0.50,
}


@dataclass
class ScoringConfig:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    priors: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PRIORS))
    gap_min_minutes: float = 15.0          # below this an interval is normal reporting
    gap_saturation_min: float = 30.0       # a gap this long over the window scores 1.0
    loiter_speed_kn: float = 3.0
    slowdown_saturation: float = 0.5       # a 50% speed drop scores 1.0
    course_change_saturation_deg: float = 60.0
    draft_reference_m: float = 12.0        # draft that neither raises nor lowers a prior
    draft_influence: float = 0.15          # how far draft may move the type prior

    @classmethod
    def from_config(cls, cfg: dict | None) -> "ScoringConfig":
        cfg = dict(cfg or {})
        weights = {**DEFAULT_WEIGHTS, **(cfg.get("weights") or {})}
        priors = {**DEFAULT_PRIORS,
                  **{k.lower(): float(v) for k, v in (cfg.get("priors") or {}).items()}}
        scalars = {
            k: float(v) for k, v in (cfg.get("scoring") or {}).items()
            if k in cls.__dataclass_fields__ and k not in ("weights", "priors")
        }
        return cls(weights={k: float(v) for k, v in weights.items()},
                   priors=priors, **scalars)

    def normalised_weights(self) -> tuple[dict[str, float], list[str]]:
        """Weights restricted to the known factors and renormalised to sum to 1."""
        warnings: list[str] = []
        unknown = [k for k in self.weights if k not in FACTORS]
        if unknown:
            warnings.append(
                f"ignoring unknown scoring weight(s) {unknown}; the contract factors are "
                f"{list(FACTORS)}"
            )
        weights = {f: float(self.weights.get(f, 0.0)) for f in FACTORS}
        total = sum(weights.values())
        if total <= 0:
            warnings.append("all scoring weights are zero; falling back to the defaults")
            return dict(DEFAULT_WEIGHTS), warnings
        if abs(total - 1.0) > 1e-6:
            warnings.append(
                f"scoring weights sum to {total:.3f}, not 1.0; they were renormalised "
                "so the total score stays comparable across runs"
            )
            weights = {k: v / total for k, v in weights.items()}
        return weights, warnings


@dataclass
class OriginDensity:
    """Kernel density of the origin cloud within the discharge window."""

    frame: LocalFrame
    kde: Any = field(repr=False, default=None)
    peak: float = 1.0
    axis_deg: float | None = None
    axis_length_m: float = 0.0

    def at(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """Normalised density in [0, 1] at the given positions."""
        if self.kde is None or lons.size == 0:
            return np.zeros(np.size(lons))
        x, y = self.frame.to_metres(lons, lats)
        values = self.kde(np.vstack([np.atleast_1d(x), np.atleast_1d(y)]))
        return np.clip(values / self.peak, 0.0, 1.0) if self.peak > 0 else np.zeros_like(values)


def build_density(document: dict[str, Any], origin) -> OriginDensity:
    """Kernel density over the particles inside the origin window, weighted."""
    from ..common.timeutil import parse_utc

    lons, lats, weights = [], [], []
    seed_lons, seed_lats = [], []
    for feature in document.get("features", []):
        properties = feature.get("properties") or {}
        if properties.get("kind"):
            continue
        lon, lat = feature["geometry"]["coordinates"]
        if abs(float(properties.get("timestep_h", -1))) < 1e-9:
            seed_lons.append(lon)
            seed_lats.append(lat)
        stamp = properties.get("time_utc")
        if stamp and origin.start_s <= parse_utc(stamp).timestamp() <= origin.end_s:
            lons.append(lon)
            lats.append(lat)
            weights.append(float(properties.get("weight", 1.0)))

    density = OriginDensity(frame=origin.frame, axis_deg=origin.axis_deg)

    if seed_lons:
        sx, sy = origin.frame.to_metres(np.asarray(seed_lons), np.asarray(seed_lats))
        major, _, _ = covariance_ellipse(sx, sy)
        density.axis_length_m = major

    if len(lons) >= 3:
        try:
            from scipy.stats import gaussian_kde

            x, y = origin.frame.to_metres(np.asarray(lons), np.asarray(lats))
            sample = np.vstack([x, y])
            kde = gaussian_kde(sample, weights=np.asarray(weights, dtype=float))
            density.kde = kde
            density.peak = float(kde(sample).max())
        except Exception:                       # noqa: BLE001 - singular cloud
            density.kde = None
    return density


@dataclass
class FactorScores:
    proximity: float = 0.0
    temporal: float = 0.0
    trajectory: float = 0.0
    anomaly: float = 0.0
    ais_gap: float = 0.0
    prior: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, float]:
        return {f: round(float(getattr(self, f)), 3) for f in FACTORS}

    def total(self, weights: dict[str, float]) -> float:
        return round(sum(weights[f] * float(getattr(self, f)) for f in FACTORS), 4)


def _in_region_window(track, origin, gates) -> np.ndarray:
    """Indices of fixes that are both in the origin region and in the window."""
    from shapely import contains_xy

    x, y = origin.frame.to_metres(track.lons, track.lats)
    inside = np.asarray(contains_xy(origin.region_m, x, y), dtype=bool)
    in_window = (track.times_s >= origin.start_s) & (track.times_s <= origin.end_s)
    both = inside & in_window
    if both.any():
        return np.flatnonzero(both)
    return np.flatnonzero(inside) if inside.any() else np.zeros(0, dtype=int)


def window_path(track, origin, samples: int = 240) -> tuple[np.ndarray, np.ndarray]:
    """Interpolated vessel positions across the origin window.

    Sampling the *path* rather than the transmitted fixes matters more than it looks.
    A vessel that goes dark while crossing the origin has no fixes there - and that
    blackout is exactly the behaviour the ais_gap factor treats as incriminating. If
    proximity were measured only at transmitted positions, going dark over the origin
    would *lower* a vessel's score, rewarding the very evasion the engine exists to
    catch. Linear interpolation across the gap keeps the inferred track honest.
    """
    lo = max(origin.start_s, track.start_s)
    hi = min(origin.end_s, track.end_s)
    if hi <= lo or track.n_fixes == 0:
        return np.zeros(0), np.zeros(0)
    times = np.linspace(lo, hi, samples)
    return (
        np.interp(times, track.times_s, track.lons),
        np.interp(times, track.times_s, track.lats),
    )


def score_proximity(track, origin, density: OriginDensity, indices) -> tuple[float, dict]:
    """How deep into the probability cloud the vessel's path actually goes."""
    if density.kde is None:
        return 0.0, {"peak_density": 0.0}

    lons, lats = window_path(track, origin)
    if lons.size == 0:
        if indices.size == 0:
            return 0.0, {"peak_density": 0.0}
        lons, lats = track.lons[indices], track.lats[indices]

    values = density.at(lons, lats)
    peak = float(values.max()) if values.size else 0.0
    return peak, {"peak_density": round(peak, 3)}


def score_temporal(track, origin, indices) -> tuple[float, dict]:
    """How well the vessel's presence lines up with the estimated discharge time."""
    if indices.size == 0:
        return 0.0, {}
    times = track.times_s[indices]
    closest = times[int(np.argmin(np.abs(times - origin.peak_s)))]
    half_width = max((origin.end_s - origin.start_s) / 2.0, 600.0)
    offset = abs(closest - origin.peak_s)
    # 1.0 at the peak, 0.5 at the window edge, decaying smoothly outside it.
    score = math.exp(-0.5 * (offset / half_width) ** 2) if half_width > 0 else 0.0
    return float(np.clip(score, 0.0, 1.0)), {
        "closest_utc_s": float(closest),
        "hours_from_peak": round(offset / 3600.0, 2),
    }


def score_trajectory(track, origin, density, gates, indices) -> tuple[float, dict]:
    """Course alignment with the slick axis, plus how far the track runs along it."""
    offset = gates.metrics.get("axis_offset_deg")
    if offset is None:
        course = track.course_at(indices[0]) if indices.size else None
        offset = (
            axis_offset_deg(course, origin.axis_deg)
            if course is not None and origin.axis_deg is not None
            else None
        )
    angle_score = 0.0 if offset is None else max(0.0, 1.0 - float(offset) / 90.0)

    overlap_m = 0.0
    if indices.size >= 2:
        x, y = origin.frame.to_metres(track.lons[indices], track.lats[indices])
        overlap_m = float(np.hypot(np.diff(x), np.diff(y)).sum())
    reference = density.axis_length_m or 1.0
    overlap_score = float(np.clip(overlap_m / reference, 0.0, 1.0))

    score = 0.6 * angle_score + 0.4 * overlap_score
    return score, {
        "axis_offset_deg": None if offset is None else round(float(offset), 1),
        "overlap_km": round(overlap_m / 1000.0, 2),
    }


def score_anomaly(track, config: ScoringConfig, indices) -> tuple[float, dict]:
    """Slowdown, course change and loitering around the origin."""
    evidence: dict[str, Any] = {}
    if indices.size == 0:
        return 0.0, evidence

    speeds = track.sog_kn
    inside = speeds[indices]
    mask = np.ones(speeds.size, dtype=bool)
    mask[indices] = False
    outside = speeds[mask]

    slowdown = 0.0
    if np.isfinite(inside).any() and np.isfinite(outside).any():
        cruise = float(np.nanmedian(outside))
        slowest = float(np.nanmin(inside))
        if cruise > 0.5:
            drop = max(0.0, (cruise - slowest) / cruise)
            slowdown = float(np.clip(drop / config.slowdown_saturation, 0.0, 1.0))
            evidence["cruise_kn"] = round(cruise, 1)
            evidence["slowest_kn"] = round(slowest, 1)

    courses = [track.course_at(int(i)) for i in indices]
    courses = [c for c in courses if c is not None]
    course_change = 0.0
    if len(courses) >= 2:
        spread = max(
            abs((a - b + 180.0) % 360.0 - 180.0) for a in courses for b in courses
        )
        course_change = float(
            np.clip(spread / config.course_change_saturation_deg, 0.0, 1.0)
        )
        evidence["course_change_deg"] = round(spread, 1)

    loiter = 0.0
    if np.isfinite(inside).any():
        loiter = float(np.mean(inside < config.loiter_speed_kn))
        if loiter > 0:
            evidence["loiter_fraction"] = round(loiter, 2)

    score = 0.5 * slowdown + 0.3 * course_change + 0.2 * loiter
    evidence["slowdown_score"] = round(slowdown, 3)
    return float(np.clip(score, 0.0, 1.0)), evidence


def score_ais_gap(track, origin, config: ScoringConfig) -> tuple[float, dict]:
    """A transmission blackout overlapping the origin window - a strong signal."""
    if track.n_fixes < 2:
        return 0.0, {}

    starts = track.times_s[:-1]
    ends = track.times_s[1:]
    durations = ends - starts
    threshold = config.gap_min_minutes * 60.0

    best = 0.0
    for start, end, duration in zip(starts, ends, durations):
        if duration <= threshold:
            continue
        overlap = min(end, origin.end_s) - max(start, origin.start_s)
        if overlap > best:
            best = overlap

    if best <= 0:
        return 0.0, {}
    minutes = best / 60.0
    score = float(np.clip(minutes / config.gap_saturation_min, 0.0, 1.0))
    return score, {"gap_minutes": round(minutes)}


def score_prior(track, config: ScoringConfig) -> tuple[float, dict]:
    """Type and draft prior: a laden tanker is a likelier source than a ferry."""
    kind = (track.vessel_type or "").strip().lower()
    base = config.priors.get(kind, config.priors.get("_default", 0.5))

    evidence: dict[str, Any] = {"vessel_type": track.vessel_type}
    draft = track.draft_m
    if np.isfinite(draft) and config.draft_reference_m > 0:
        nudge = config.draft_influence * np.clip(
            (draft - config.draft_reference_m) / config.draft_reference_m, -1.0, 1.0
        )
        base = float(np.clip(base + nudge, 0.0, 1.0))
        evidence["draft_m"] = round(float(draft), 1)
    return float(base), evidence


def score_vessel(track, gates, origin, density, config: ScoringConfig) -> FactorScores:
    """All six factors for one vessel that passed the gates."""
    indices = _in_region_window(track, origin, gates)

    proximity, e_prox = score_proximity(track, origin, density, indices)
    temporal, e_time = score_temporal(track, origin, indices)
    trajectory, e_traj = score_trajectory(track, origin, density, gates, indices)
    anomaly, e_anom = score_anomaly(track, config, indices)
    ais_gap, e_gap = score_ais_gap(track, origin, config)
    prior, e_prior = score_prior(track, config)

    return FactorScores(
        proximity=proximity,
        temporal=temporal,
        trajectory=trajectory,
        anomaly=anomaly,
        ais_gap=ais_gap,
        prior=prior,
        evidence={**e_prox, **e_time, **e_traj, **e_anom, **e_gap, **e_prior,
                  "fixes_scored": int(indices.size)},
    )
