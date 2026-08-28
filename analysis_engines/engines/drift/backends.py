"""Drift engine selection: OpenOil -> OceanDrift -> Euler (handbook §6 Phase 3).

All three backends implement one signature, ``run(DriftRequest) -> DriftRun``, so the
cloud builder, the writers and every test downstream are indifferent to which one ran.

The request carries both forms of met-ocean input on purpose. The Euler integrator
consumes the loaded ``Metocean`` object; OpenDrift insists on opening the NetCDFs itself
through its own CF readers. Rather than bend one to the other, the request holds the
loaded grids *and* the file paths, and each backend takes what it needs.

    engine_used  "primary"   OpenOil or OceanDrift
                 "fallback"  the in-house Euler integrator

Selection is by availability: OpenDrift is an optional dependency living in a separate
conda environment, and its absence is a warning, never a failure. Pinning an engine
explicitly and finding it missing *is* an error - a silent downgrade would make a run
mean something different from what was asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..common.errors import missing_input
from ..common.status import FALLBACK, PRIMARY
from .euler_fallback import DriftRun, run_euler

AUTO = "auto"
EULER = "euler"
OCEANDRIFT = "oceandrift"
OPENOIL = "openoil"

# Runtime preference order, per handbook §6 Phase 3.
ENGINE_ORDER = (OPENOIL, OCEANDRIFT, EULER)


@dataclass
class DriftRequest:
    """Everything any backend needs to integrate a particle cloud."""

    seed_lons: np.ndarray
    seed_lats: np.ndarray
    start_time_s: float
    hours: float
    dt_seconds: float
    direction: int
    diffusion_m2_s: float
    rng: Any = None
    metocean: Any = field(default=None, repr=False)
    currents_path: str | Path | None = None
    wind_path: str | Path | None = None
    leeway: float = 0.03


class DriftBackend:
    """Base class; subclasses set ``name``/``kind`` and implement ``run``."""

    name: str = "base"
    kind: str = FALLBACK
    description: str = ""

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return False, "not implemented"

    @classmethod
    def run(cls, request: DriftRequest) -> DriftRun:
        raise NotImplementedError


class EulerBackend(DriftBackend):
    """The guaranteed path: in-house, dependency-free, always available."""

    name = EULER
    kind = FALLBACK
    description = "in-house Euler integrator (current + 3% wind, Gaussian diffusion)"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "built in"

    @classmethod
    def run(cls, request: DriftRequest) -> DriftRun:
        if request.metocean is None:
            raise missing_input("the Euler backend needs loaded met-ocean grids")
        return run_euler(
            request.seed_lons, request.seed_lats, request.metocean, request.start_time_s,
            hours=request.hours,
            dt_seconds=request.dt_seconds,
            direction=request.direction,
            diffusion_m2_s=request.diffusion_m2_s,
            rng=request.rng,
        )


class _OpenDriftBackend(DriftBackend):
    """Shared plumbing for the two OpenDrift models."""

    model: str = ""
    kind = PRIMARY

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from .opendrift_adapter import model_available

        return model_available(cls.model)

    @classmethod
    def run(cls, request: DriftRequest) -> DriftRun:
        from .opendrift_adapter import run_opendrift

        return run_opendrift(request, cls.model)


class OpenOilBackend(_OpenDriftBackend):
    name = OPENOIL
    model = "OpenOil"
    description = "OpenDrift OpenOil (oil-specific weathering and drift)"


class OceanDriftBackend(_OpenDriftBackend):
    name = OCEANDRIFT
    model = "OceanDrift"
    description = "OpenDrift OceanDrift (generic Lagrangian drift, fewer dependencies)"


BACKENDS: dict[str, type[DriftBackend]] = {
    OPENOIL: OpenOilBackend,
    OCEANDRIFT: OceanDriftBackend,
    EULER: EulerBackend,
}


def select_backend(preference: str | None = AUTO) -> tuple[type[DriftBackend], list[str]]:
    """Pick a backend, returning it alongside any warnings about what was skipped.

    ``preference`` of ``"auto"`` walks :data:`ENGINE_ORDER` and takes the first available
    engine. A named preference is honoured exactly, or raises - because quietly running a
    different model than the one asked for changes what the output means.
    """
    warnings: list[str] = []
    preference = (preference or AUTO).strip().lower()

    if preference != AUTO:
        backend = BACKENDS.get(preference)
        if backend is None:
            raise missing_input(
                f"unknown drift engine {preference!r}; choose from "
                f"{[AUTO, *ENGINE_ORDER]}",
                requested=preference,
            )
        available, reason = backend.is_available()
        if not available:
            raise missing_input(
                f"drift engine {preference!r} was requested but is not available: "
                f"{reason}",
                requested=preference, reason=reason,
            )
        return backend, warnings

    for name in ENGINE_ORDER:
        backend = BACKENDS[name]
        available, reason = backend.is_available()
        if available:
            if name != ENGINE_ORDER[0]:
                warnings.append(
                    f"running on the {backend.description}; "
                    f"{ENGINE_ORDER[0]} was preferred but unavailable"
                )
            return backend, warnings
        warnings.append(f"drift engine '{name}' unavailable: {reason}")

    # Unreachable: Euler is always available. Kept so a future refactor cannot silently
    # leave the engine with nothing to run.
    raise missing_input("no drift engine is available")
