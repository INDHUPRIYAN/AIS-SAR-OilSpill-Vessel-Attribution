"""Slick age proxy: Fay spreading law, nudged by the backscatter damping ratio.

Handbook §6 Phase 1: "Fay age estimate from area (document assumptions; mark
``age_confidence: 'low'``)". This module is that estimate, and this docstring is that
documentation.

The physics
-----------
Fay's gravity-viscous spreading phase gives the area of a slick of volume V after time
t (dimensionally checked: the bracket is m^6 s^-3/2, its cube root m^2 s^-1/2, times
sqrt(t) gives m^2):

    A(t) = pi * k2^2 * (delta * g * V^2 / sqrt(nu))^(1/3) * sqrt(t)

The honest problem
------------------
A mask gives area. It does not give volume, and Fay needs volume. The two are closed
here with an assumed mean slick thickness, V = A * h, which collapses the law to a
direct inversion for time:

    A       = pi * k2^2 * (delta * g * h^2 / sqrt(nu))^(1/3) * A^(2/3) * sqrt(t)
    =>  t   = A^(2/3) / [ pi^2 * k2^4 * (delta * g * h^2 / sqrt(nu))^(2/3) ]

Two consequences follow, and both are the reason ``age_confidence`` is hard-wired to
``"low"`` rather than being computed:

1. **t scales as h^(-4/3).** Assuming a slick 10x thinner makes it ~21x older. The
   assumed thickness, not the measurement, dominates the answer.
2. **t scales as A^(2/3).** Area itself enters weakly, so even a badly wrong mask
   changes the estimate less than the thickness assumption does.

Treat the output as an order-of-magnitude bracket ("hours, not days" / "days, not
hours"), never as a timestamp. Engine B's hindcast window is the defensible number for
*when* a discharge happened; this is a sanity check on it.

The damping nudge
-----------------
Damping falls as a film thins and weathers, so a slick damping less than the reference
is treated as older than its area alone suggests, and vice versa. The adjustment is a
bounded linear factor - deliberately crude, because no better calibration exists
without ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

# Age is reported at this confidence unconditionally - see the module docstring.
AGE_CONFIDENCE = "low"

METHOD_FAY = "fay"
METHOD_DAMPING_FAY = "damping+fay"


@dataclass(frozen=True)
class FayParams:
    """Constants for the inversion. Defaults mirror ``config/characterise.yaml``."""

    k2: float = 1.45                      # Fay gravity-viscous constant
    delta: float = 0.15                   # (rho_water - rho_oil) / rho_water
    nu_water_m2_s: float = 1.0e-6         # kinematic viscosity of seawater
    gravity_m_s2: float = 9.81
    assumed_thickness_m: float = 1.0e-3   # sets the whole age scale; t ~ h^(-4/3)
    reference_damping_db: float = 7.0
    damping_age_factor: float = 0.06      # per dB away from the reference
    damping_factor_bounds: tuple[float, float] = (0.5, 1.6)
    max_age_hours: float = 168.0          # 7 days; beyond this the estimate is noise

    @classmethod
    def from_config(cls, cfg: dict | None) -> "FayParams":
        cfg = dict(cfg or {})
        bounds = cfg.pop("damping_factor_bounds", None)
        params = cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})
        if bounds is not None:
            params = FayParams(
                **{
                    **{f: getattr(params, f) for f in cls.__dataclass_fields__},
                    "damping_factor_bounds": (float(bounds[0]), float(bounds[1])),
                }
            )
        return params


@dataclass(frozen=True)
class AgeEstimate:
    age_hours: float
    method: str
    confidence: str = AGE_CONFIDENCE


def fay_age_hours(area_km2: float, params: FayParams) -> float:
    """Pure Fay inversion: area (km^2) -> age (hours), before any damping adjustment."""
    if area_km2 <= 0:
        return 0.0

    area_m2 = area_km2 * 1e6
    bracket = (
        params.delta
        * params.gravity_m_s2
        * params.assumed_thickness_m**2
        / params.nu_water_m2_s**0.5
    )
    denominator = 3.141592653589793**2 * params.k2**4 * bracket ** (2.0 / 3.0)
    seconds = area_m2 ** (2.0 / 3.0) / denominator
    return seconds / 3600.0


def damping_factor(damping_db: float | None, params: FayParams) -> float:
    """Multiplier applied to the Fay age. >1 means older than area alone suggests."""
    if damping_db is None:
        return 1.0
    raw = 1.0 + params.damping_age_factor * (params.reference_damping_db - damping_db)
    low, high = params.damping_factor_bounds
    return min(max(raw, low), high)


def estimate_age(
    area_km2: float,
    damping_db: float | None = None,
    params: FayParams | None = None,
) -> tuple[AgeEstimate, list[str]]:
    """Age proxy for one slick.

    ``method`` is ``"damping+fay"`` when a damping ratio was available and ``"fay"``
    when the dB band was missing. Confidence is always ``"low"``.
    """
    params = params or FayParams()
    warnings: list[str] = []

    hours = fay_age_hours(area_km2, params) * damping_factor(damping_db, params)
    method = METHOD_FAY if damping_db is None else METHOD_DAMPING_FAY

    if hours > params.max_age_hours:
        warnings.append(
            f"Fay age estimate {hours:.0f} h exceeds the {params.max_age_hours:.0f} h "
            "ceiling and was clamped; the assumed slick thickness is probably wrong "
            "for this scene"
        )
        hours = params.max_age_hours

    return AgeEstimate(age_hours=round(hours, 2), method=method), warnings
