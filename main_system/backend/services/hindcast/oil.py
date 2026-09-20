"""Minimal oil physics shared by the shape and verification stages."""
from __future__ import annotations

import math

import numpy as np

G = 9.81
NU_WATER = 1.0e-6                       # m^2/s
# Relative density difference (rho_w - rho_o) / rho_w, and evaporation behaviour.
OIL = {
    "light": {"delta": 0.19, "evap_max": 0.55, "evap_tau_h": 12.0},
    "medium": {"delta": 0.12, "evap_max": 0.30, "evap_tau_h": 24.0},
    "heavy": {"delta": 0.05, "evap_max": 0.10, "evap_tau_h": 48.0},
}
K_FAY = 1.45                            # gravity-viscous regime constant
HDR95 = 5.99                            # chi-square(2) 95% quantile: area of a Gaussian's 95% ellipse / (pi sigma^2)


def fay_radius_m(volume_m3: float, t_s: np.ndarray | float, oil_type: str) -> np.ndarray:
    """Fay gravity-viscous spreading: R = k (delta g V^2 t^1.5 / nu^0.5)^(1/6)."""
    delta = OIL[oil_type]["delta"]
    t = np.maximum(np.asarray(t_s, dtype=np.float64), 1.0)
    return K_FAY * (delta * G * volume_m3 ** 2 * t ** 1.5 / math.sqrt(NU_WATER)) ** (1.0 / 6.0)


def fay_variance_m2(volume_m3: float, t_s: np.ndarray | float, oil_type: str) -> np.ndarray:
    """Per-axis positional variance of a Gaussian patch whose 95% disc has the Fay radius."""
    return fay_radius_m(volume_m3, t_s, oil_type) ** 2 / HDR95


def remaining_fraction(t_h: np.ndarray | float, oil_type: str) -> np.ndarray:
    """First-order evaporation: the light ends go, the rest persists."""
    o = OIL[oil_type]
    return 1.0 - o["evap_max"] * (1.0 - np.exp(-np.asarray(t_h, dtype=np.float64) / o["evap_tau_h"]))


def predicted_area_km2(volume_m3: float, t_h: np.ndarray | float, oil_type: str, k_diff: float) -> np.ndarray:
    """Area of the 95% patch: Fay spreading plus turbulent diffusion, variances added."""
    t_s = np.asarray(t_h, dtype=np.float64) * 3600.0
    variance = fay_variance_m2(volume_m3, t_s, oil_type) + 2.0 * k_diff * t_s
    return math.pi * HDR95 * variance / 1e6
