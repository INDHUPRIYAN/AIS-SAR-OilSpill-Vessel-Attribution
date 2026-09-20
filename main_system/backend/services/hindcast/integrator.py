"""Particle integration behind one interface.

`DriftBackend.integrate` advances a particle set hour by hour, forward or
backward, and reports every step through a callback. Two implementations:

* `NumpyRK4Backend`  -- always available; classical RK4 on `ForcingField`
  plus a random-walk for horizontal diffusion.
* `OpenDriftBackend` -- wraps OpenDrift's OceanDrift when the package is
  installed. OpenDrift has no per-element wind deflection angle or Stokes
  scaling, so theta is applied by pre-rotating the wind and beta by pre-scaling
  the Stokes field, one run per ensemble member.

`get_backend("auto")` prefers OpenDrift when importable. The drift engine falls
back to numpy for the run if the OpenDrift path raises, and says so in its log.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

from backend.services.hindcast.forcing import US, UW, VS, VW, DriftParams, ForcingField, metres_to_degrees

StepCallback = Callable[[int, np.ndarray, np.ndarray], None]


@dataclass
class MemberParams:
    """One value per PARTICLE (members are concatenated along the particle axis)."""

    alpha: np.ndarray
    theta_deg: np.ndarray
    beta: np.ndarray
    current_scale: np.ndarray
    k_diff: np.ndarray

    def drift(self) -> DriftParams:
        return DriftParams(self.alpha, self.theta_deg, self.beta, self.current_scale)


def rk4_step(field: ForcingField, lon: np.ndarray, lat: np.ndarray, k: float,
             dk: float, dt_s: float, p: DriftParams) -> tuple[np.ndarray, np.ndarray]:
    """One RK4 step from time index k to k + dk. `dt_s` carries the sign."""

    def shifted(u: np.ndarray, v: np.ndarray, frac: float) -> tuple[np.ndarray, np.ndarray]:
        dlon, dlat = metres_to_degrees(u * dt_s * frac, v * dt_s * frac, lat)
        return lon + dlon, lat + dlat

    u1, v1 = field.velocity(lon, lat, k, p)
    u2, v2 = field.velocity(*shifted(u1, v1, 0.5), k + 0.5 * dk, p)
    u3, v3 = field.velocity(*shifted(u2, v2, 0.5), k + 0.5 * dk, p)
    u4, v4 = field.velocity(*shifted(u3, v3, 1.0), k + dk, p)
    u = (u1 + 2 * u2 + 2 * u3 + u4) / 6.0
    v = (v1 + 2 * v2 + 2 * v3 + v4) / 6.0
    dlon, dlat = metres_to_degrees(u * dt_s, v * dt_s, lat)
    return lon + dlon, lat + dlat


def random_walk(lon: np.ndarray, lat: np.ndarray, variance_m2: np.ndarray | float,
                rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Isotropic Gaussian displacement with the given per-axis variance (m^2)."""
    sigma = np.sqrt(np.maximum(variance_m2, 0.0))
    dlon, dlat = metres_to_degrees(rng.standard_normal(lon.size) * sigma,
                                   rng.standard_normal(lon.size) * sigma, lat)
    return lon + dlon, lat + dlat


class DriftBackend(Protocol):
    name: str

    def integrate(self, field: ForcingField, lon: np.ndarray, lat: np.ndarray,
                  params: MemberParams, k_start: float, n_steps: int, backward: bool,
                  rng: np.random.Generator, on_step: Optional[StepCallback] = None,
                  dt_s: float = 3600.0) -> tuple[np.ndarray, np.ndarray]: ...


class NumpyRK4Backend:
    name = "numpy-rk4"

    def integrate(self, field: ForcingField, lon: np.ndarray, lat: np.ndarray,
                  params: MemberParams, k_start: float, n_steps: int, backward: bool,
                  rng: np.random.Generator, on_step: Optional[StepCallback] = None,
                  dt_s: float = 3600.0) -> tuple[np.ndarray, np.ndarray]:
        sign = -1.0 if backward else 1.0
        dk = sign * dt_s / 3600.0
        drift = params.drift()
        # Diffusion has no arrow of time: run backward it still SPREADS the cloud,
        # which is exactly the growth of ignorance about where the oil came from.
        variance = 2.0 * params.k_diff * dt_s
        k = k_start
        for step in range(1, n_steps + 1):
            lon, lat = rk4_step(field, lon, lat, k, dk, sign * dt_s, drift)
            lon, lat = random_walk(lon, lat, variance, rng)
            k += dk
            if on_step is not None:
                on_step(step, lon, lat)
        return lon, lat


class OpenDriftBackend:
    """Best-effort OceanDrift adapter. One OpenDrift run per distinct member."""

    name = "opendrift"

    def integrate(self, field: ForcingField, lon: np.ndarray, lat: np.ndarray,
                  params: MemberParams, k_start: float, n_steps: int, backward: bool,
                  rng: np.random.Generator, on_step: Optional[StepCallback] = None,
                  dt_s: float = 3600.0) -> tuple[np.ndarray, np.ndarray]:
        from datetime import timedelta

        from opendrift.models.oceandrift import OceanDrift
        from opendrift.readers import reader_netCDF_CF_generic

        out_lon, out_lat = lon.copy(), lat.copy()
        history = np.full((n_steps, 2, lon.size), np.nan)
        # Members are contiguous blocks that share one parameter set.
        keys = np.stack([params.alpha, params.theta_deg, params.beta,
                         params.current_scale, params.k_diff], axis=1)
        _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        start = field.time_at(k_start)
        for m, idx0 in enumerate(first):
            sel = np.flatnonzero(inverse == m)
            alpha, theta, beta, cscale, kdiff = keys[idx0]
            member_field = field.with_wind_correction(1.0, -theta if np.mean(lat[sel]) >= 0 else theta)
            member_field.data[:, [US, VS]] *= beta
            member_field.data[:, [0, 1]] *= cscale
            model = OceanDrift(loglevel=50)
            model.add_reader(reader_netCDF_CF_generic.Reader(member_field.to_dataset()))
            model.set_config("general:coastline_action", "none")
            model.set_config("drift:horizontal_diffusivity", float(kdiff))
            model.set_config("drift:stokes_drift", True)
            model.seed_elements(lon=lon[sel], lat=lat[sel], time=start,
                                wind_drift_factor=float(alpha))
            signed = -dt_s if backward else dt_s
            model.run(time_step=timedelta(seconds=signed), time_step_output=timedelta(seconds=dt_s),
                      steps=n_steps)
            lons = np.asarray(model.get_property("lon")[0])      # (time, elements)
            lats = np.asarray(model.get_property("lat")[0])
            for step in range(1, n_steps + 1):
                row = min(step, lons.shape[0] - 1)
                history[step - 1, 0, sel], history[step - 1, 1, sel] = lons[row], lats[row]
            out_lon[sel], out_lat[sel] = lons[-1], lats[-1]
        if on_step is not None:
            for step in range(1, n_steps + 1):
                on_step(step, history[step - 1, 0], history[step - 1, 1])
        return out_lon, out_lat


def opendrift_available() -> bool:
    return importlib.util.find_spec("opendrift") is not None


def get_backend(preference: str = "auto") -> DriftBackend:
    if preference == "opendrift" or (preference == "auto" and opendrift_available()):
        if not opendrift_available():
            raise RuntimeError("DRIFT_BACKEND=opendrift but the opendrift package is not installed")
        return OpenDriftBackend()
    return NumpyRK4Backend()


__all__ = ["DriftBackend", "MemberParams", "NumpyRK4Backend", "OpenDriftBackend",
           "get_backend", "opendrift_available", "rk4_step", "random_walk", "UW", "VW"]
