"""Oil weathering: how much oil is left, and what state it is in, after N hours at sea.

Scope and honesty
-----------------
Drift answers *where* the oil went. Weathering answers *how much is still there and
what it has turned into*. Until now OceanTrace modelled neither evaporation nor
emulsification (LIMITATIONS item 8), so a 24 h forecast implicitly claimed the slick
was the same substance it was at t=0. It is not.

This module implements the two processes that dominate the first 24-48 h of a marine
spill, using published empirical models rather than invented formulas:

1. **Evaporation** - Fingas' empirical equations (Fingas 1997, 1999, 2004). Fingas
   showed that oil evaporation at sea is *not* air-boundary-layer regulated (unlike
   water), so it does not need wind speed or slick area: it is a function of time and
   temperature alone. Most crudes follow a logarithmic law, light/refined products a
   square-root law:

       logarithmic:  %Ev = (A + 0.045 * (T - 15)) * ln(t)
       square-root:  %Ev = (A + 0.01  * (T - 15)) * sqrt(t)

   with ``t`` in **minutes**, ``T`` in degrees C, and ``A`` an oil-specific constant.
   This is the single most-cited practical evaporation model for spill response.

2. **Emulsification** (water-in-oil uptake, "chocolate mousse") - Mackay's first-order
   approach to a maximum water content, driven by wind energy (Mackay et al. 1980):

       dY/dt = K * (1 + U10)^2 * (1 - Y / Ymax)

   Emulsification is what makes a slick persist: it multiplies the volume, raises the
   viscosity by orders of magnitude, and stops further evaporation. It is gated here
   on an evaporation threshold, because a fresh oil does not emulsify until it has
   lost its light ends.

Viscosity is then raised by the Mooney (1951) relation, which is the standard closure
for water-in-oil emulsions.

What this deliberately does NOT model
-------------------------------------
Dispersion, dissolution, photo-oxidation, biodegradation, sedimentation, and
spreading-driven thickness change. Those matter on multi-day timescales; OceanTrace
keeps drift horizons at <= 24 h partly for that reason. The module reports
``model: "fingas+mackay"`` and every assumption it made, so no downstream consumer can
mistake this for a full fate model such as ADIOS or OpenOil's internal weathering.

Accuracy claim: **none is made**. These are order-of-magnitude engineering estimates
against an *assumed* oil type. Without knowing the actual product spilled, the oil-type
choice dominates the answer - which is exactly why ``oil_type`` is reported in the
output and why ``confidence`` is fixed at "low".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Oil types
# --------------------------------------------------------------------------

# Fingas evaporation constants. `equation` selects ln(t) vs sqrt(t); `a` is the
# oil-specific coefficient reported by Fingas for that product.
# `max_water` is the maximum stable water fraction of the emulsion, and
# `evap_threshold_pct` the evaporative loss required before emulsification starts
# (a fresh oil is too fluid to hold water).
OIL_TYPES: dict[str, dict[str, Any]] = {
    "light_crude": {
        "equation": "log", "a": 2.11, "max_water": 0.80,
        "evap_threshold_pct": 5.0, "k_emul": 2.0e-6,
        "density_kg_m3": 855.0, "viscosity_cst": 10.0,
    },
    "medium_crude": {
        "equation": "log", "a": 1.58, "max_water": 0.70,
        "evap_threshold_pct": 8.0, "k_emul": 1.6e-6,
        "density_kg_m3": 880.0, "viscosity_cst": 50.0,
    },
    "heavy_crude": {
        "equation": "log", "a": 0.39, "max_water": 0.55,
        "evap_threshold_pct": 12.0, "k_emul": 1.0e-6,
        "density_kg_m3": 940.0, "viscosity_cst": 500.0,
    },
    "diesel": {
        "equation": "sqrt", "a": 0.31, "max_water": 0.30,
        "evap_threshold_pct": 20.0, "k_emul": 0.6e-6,
        "density_kg_m3": 840.0, "viscosity_cst": 4.0,
    },
    "fuel_oil_no6": {
        "equation": "sqrt", "a": 0.045, "max_water": 0.45,
        "evap_threshold_pct": 15.0, "k_emul": 0.8e-6,
        "density_kg_m3": 970.0, "viscosity_cst": 2000.0,
    },
}

# A discharge from a passing vessel is most often bunker/medium product; this is the
# default only because *something* must be assumed, and the assumption is reported.
DEFAULT_OIL_TYPE = "medium_crude"

# Mooney (1951) crowding constant for water-in-oil emulsions.
_MOONEY_K = 2.5
_MOONEY_C = 0.65


@dataclass(frozen=True)
class WeatheringState:
    """Oil state at one point in time."""

    hours: float
    evaporated_fraction: float          # of the ORIGINAL oil mass, 0..1
    water_fraction: float               # water content of the emulsion, 0..1
    oil_remaining_fraction: float       # of the ORIGINAL oil, 0..1
    emulsion_volume_factor: float       # emulsion volume / original oil volume
    viscosity_cst: float
    density_kg_m3: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "hours": round(self.hours, 3),
            "evaporated_fraction": round(self.evaporated_fraction, 4),
            "water_fraction": round(self.water_fraction, 4),
            "oil_remaining_fraction": round(self.oil_remaining_fraction, 4),
            "emulsion_volume_factor": round(self.emulsion_volume_factor, 4),
            "viscosity_cst": round(self.viscosity_cst, 1),
            "density_kg_m3": round(self.density_kg_m3, 1),
        }


def evaporated_fraction(
    hours: float, oil_type: str = DEFAULT_OIL_TYPE, temperature_c: float = 15.0
) -> float:
    """Fingas evaporative loss as a fraction (0..1) of the original oil.

    ``hours`` <= 0 returns 0. The logarithmic form is undefined below t = 1 minute,
    so the first minute is treated as zero loss rather than negative.
    """
    if hours <= 0:
        return 0.0
    spec = OIL_TYPES.get(oil_type) or OIL_TYPES[DEFAULT_OIL_TYPE]
    minutes = hours * 60.0
    if minutes < 1.0:
        return 0.0

    if spec["equation"] == "log":
        pct = (spec["a"] + 0.045 * (temperature_c - 15.0)) * math.log(minutes)
    else:
        pct = (spec["a"] + 0.010 * (temperature_c - 15.0)) * math.sqrt(minutes)

    # Physically bounded: an oil cannot lose more than its volatile fraction, and the
    # empirical fit is only calibrated over the first few days.
    return float(min(max(pct, 0.0), 100.0) / 100.0)


def water_fraction(
    hours: float,
    wind_speed_m_s: float,
    oil_type: str = DEFAULT_OIL_TYPE,
    temperature_c: float = 15.0,
) -> float:
    """Mackay first-order water uptake, gated on evaporative loss.

    Integrated analytically: with a constant wind the ODE
    ``dY/dt = K (1+U)^2 (1 - Y/Ymax)`` has the closed form
    ``Y(t) = Ymax * (1 - exp(-K (1+U)^2 t / Ymax))`` measured from the moment the
    evaporation threshold is crossed.
    """
    if hours <= 0:
        return 0.0
    spec = OIL_TYPES.get(oil_type) or OIL_TYPES[DEFAULT_OIL_TYPE]

    # Emulsification does not start until the oil has lost its light ends.
    evap_pct = evaporated_fraction(hours, oil_type, temperature_c) * 100.0
    if evap_pct < spec["evap_threshold_pct"]:
        return 0.0

    # Find when the threshold was crossed, so young oil is not credited with water.
    t_start = 0.0
    lo, hi = 0.0, hours
    for _ in range(40):                      # bisection; cheap and dependency-free
        mid = 0.5 * (lo + hi)
        if evaporated_fraction(mid, oil_type, temperature_c) * 100.0 < spec["evap_threshold_pct"]:
            lo = mid
        else:
            hi = mid
    t_start = hi

    elapsed_s = max(hours - t_start, 0.0) * 3600.0
    ymax = float(spec["max_water"])
    k = float(spec["k_emul"])
    rate = k * (1.0 + max(wind_speed_m_s, 0.0)) ** 2
    return float(ymax * (1.0 - math.exp(-rate * elapsed_s / ymax)))


def mooney_viscosity(base_cst: float, water_frac: float) -> float:
    """Mooney (1951) viscosity increase for a water-in-oil emulsion."""
    if water_frac <= 0:
        return float(base_cst)
    denom = 1.0 - _MOONEY_C * water_frac
    if denom <= 1e-6:
        denom = 1e-6
    return float(base_cst * math.exp(_MOONEY_K * water_frac / denom))


def weather_at(
    hours: float,
    *,
    wind_speed_m_s: float = 5.0,
    oil_type: str = DEFAULT_OIL_TYPE,
    temperature_c: float = 15.0,
) -> WeatheringState:
    """Full weathering state after ``hours`` at sea."""
    spec = OIL_TYPES.get(oil_type) or OIL_TYPES[DEFAULT_OIL_TYPE]
    ev = evaporated_fraction(hours, oil_type, temperature_c)
    yw = water_fraction(hours, wind_speed_m_s, oil_type, temperature_c)
    oil_left = max(1.0 - ev, 0.0)

    # The emulsion is the surviving oil plus the water it has taken up.
    volume_factor = oil_left / (1.0 - yw) if yw < 1.0 else oil_left
    visc = mooney_viscosity(float(spec["viscosity_cst"]), yw)
    rho_oil = float(spec["density_kg_m3"])
    rho = rho_oil * (1.0 - yw) + 1025.0 * yw          # seawater 1025 kg/m3

    return WeatheringState(
        hours=float(hours),
        evaporated_fraction=ev,
        water_fraction=yw,
        oil_remaining_fraction=oil_left,
        emulsion_volume_factor=volume_factor,
        viscosity_cst=visc,
        density_kg_m3=rho,
    )


def weathering_series(
    horizons_h,
    *,
    wind_speed_m_s: float = 5.0,
    oil_type: str = DEFAULT_OIL_TYPE,
    temperature_c: float = 15.0,
) -> dict[str, Any]:
    """Weathering state at each horizon, plus the assumptions that produced it.

    Returned verbatim in ``forecast.geojson`` metadata so an investigator can see the
    assumed oil type rather than having to guess it.
    """
    if oil_type not in OIL_TYPES:
        oil_type = DEFAULT_OIL_TYPE
    states = [
        weather_at(h, wind_speed_m_s=wind_speed_m_s, oil_type=oil_type,
                   temperature_c=temperature_c).to_dict()
        for h in horizons_h
    ]
    return {
        "model": "fingas+mackay",
        "references": [
            "Fingas, M. (2004) Modeling evaporation from oil spills. J. Hazard. Mater.",
            "Mackay, D. et al. (1980) Oil spill processes and models. Environment Canada.",
            "Mooney, M. (1951) The viscosity of a concentrated suspension of spheres.",
        ],
        "oil_type_assumed": oil_type,
        "temperature_c_assumed": temperature_c,
        "wind_speed_m_s_used": round(float(wind_speed_m_s), 3),
        "confidence": "low",
        "processes_modelled": ["evaporation", "emulsification", "viscosity", "density"],
        "processes_not_modelled": [
            "dispersion", "dissolution", "photo-oxidation", "biodegradation",
            "sedimentation", "spreading-driven thickness change", "coastline stranding",
        ],
        "honesty_note": (
            "Order-of-magnitude engineering estimate. The assumed oil type dominates the "
            "answer; no oil type is known for these scenes. Not a substitute for ADIOS or "
            "OpenOil's internal weathering."
        ),
        "states": states,
    }
