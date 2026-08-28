"""Translation layer between this module's drift contract and OpenDrift.

⚠️ **UNVERIFIED CODE.** OpenDrift is not installed on the development machine (there is
no conda), so nothing in this file has ever executed. It is written from the OpenDrift
API and reviewed, but until someone builds the environment described in
``engines/drift/README.md`` it must be treated as a draft. The tests that exercise it
skip themselves via ``pytest.importorskip("opendrift")`` and will start running the
moment the environment exists.

What this file has to reconcile:

* **Seeding.** OpenDrift would happily scatter its own particles from a cone or a
  polygon; we seed at the exact positions the Euler path uses, so the two engines are
  comparable on identical initial conditions.
* **Backward runs.** OpenDrift goes backward with a negative ``time_step`` and a positive
  duration - the same convention as the Euler integrator's negated timestep.
* **Diffusion.** ``drift:horizontal_diffusivity`` is set from the same config value the
  Euler path uses, so cloud spread is comparable rather than accidentally different.
* **Wind leeway.** The Euler path applies a flat 3% of the 10 m wind. OpenDrift models it
  as a per-element ``wind_drift_factor``, which is pinned to the same value here. OpenOil
  will still diverge by design - it also emulsifies, evaporates and submerges oil - and
  that difference is physics, not a bug.
* **Result layout.** OpenDrift ≥ 1.11 returns an xarray ``Dataset`` on ``.result``;
  older versions expose masked arrays on ``.history``. Both are handled, because which
  one the conda solve lands on is not knowable from here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..common.errors import bad_grid, missing_input
from .euler_fallback import BACKWARD, DriftRun

# Landmask is disabled deliberately: it pulls the `roaring-landmask` data dependency,
# one of the more fragile parts of the install, and the demo scenes are open ocean.
_LANDMASK = False


def model_available(model: str) -> tuple[bool, str]:
    """Can this OpenDrift model be imported right now?"""
    try:
        _import_model(model)
    except ImportError as exc:
        return False, f"OpenDrift not installed ({exc})"
    except Exception as exc:                      # noqa: BLE001 - a broken install
        return False, f"OpenDrift import failed ({exc})"
    return True, "importable"


def _import_model(model: str):
    if model == "OpenOil":
        from opendrift.models.openoil import OpenOil

        return OpenOil
    if model == "OceanDrift":
        from opendrift.models.oceandrift import OceanDrift

        return OceanDrift
    raise ValueError(f"unknown OpenDrift model {model!r}")


def _reader(path: str | Path):
    from opendrift.readers import reader_netCDF_CF_generic

    try:
        return reader_netCDF_CF_generic.Reader(str(path))
    except Exception as exc:                      # noqa: BLE001 - reader is strict
        raise bad_grid(
            f"OpenDrift could not read {Path(path).name}: {exc}. Its CF reader maps "
            "variables by standard_name, so the file needs CF names such as "
            "x_sea_water_velocity / y_sea_water_velocity and x_wind / y_wind.",
            path=str(path),
        )


def _to_datetime(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)


def _extract(simulation, particles: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pull (times_s, lons, lats) out of a finished simulation, in our layout.

    Ours is ``(steps + 1, particles)``; OpenDrift's is ``(particles, steps + 1)``.
    """
    lons = lats = None

    result = getattr(simulation, "result", None)
    if result is not None:
        try:                                       # OpenDrift >= 1.11: xarray Dataset
            lons = np.asarray(result["lon"].values, dtype=float)
            lats = np.asarray(result["lat"].values, dtype=float)
        except Exception:                          # noqa: BLE001
            lons = lats = None

    if lons is None:
        history = getattr(simulation, "history", None)
        if history is None:
            raise missing_input("the OpenDrift run produced no trajectory data")
        lons = np.ma.filled(history["lon"], np.nan).astype(float)
        lats = np.ma.filled(history["lat"], np.nan).astype(float)

    times = [
        t.replace(tzinfo=timezone.utc).timestamp()
        for t in simulation.get_time_array()[0]
    ]
    times_s = np.asarray(times, dtype=float)

    # OpenDrift indexes (particle, step); we want (step, particle).
    if lons.shape[0] == particles and lons.shape[0] != len(times_s):
        lons, lats = lons.T, lats.T
    elif lons.ndim == 2 and lons.shape[1] == particles:
        pass
    else:
        lons, lats = lons.T, lats.T

    steps = min(len(times_s), lons.shape[0])
    times_s, lons, lats = times_s[:steps], lons[:steps], lats[:steps]
    return times_s, _forward_fill(lons), _forward_fill(lats)


def _forward_fill(values: np.ndarray) -> np.ndarray:
    """Hold the last known position of a deactivated element.

    OpenDrift masks elements once they strand or leave the domain. Dropping them would
    change the particle count mid-run and break the cloud statistics, so their final
    position is carried forward and the caller sees a constant tail instead of a hole.
    """
    filled = np.array(values, dtype=float, copy=True)
    for step in range(1, filled.shape[0]):
        gaps = ~np.isfinite(filled[step])
        filled[step][gaps] = filled[step - 1][gaps]
    # Any element that never had a position at all is dropped by the caller's stats.
    return filled


def run_opendrift(request, model: str) -> DriftRun:
    """Run one OpenDrift model over the same request the Euler backend would take."""
    Model = _import_model(model)

    simulation = Model(loglevel=50)

    readers = []
    if request.currents_path:
        readers.append(_reader(request.currents_path))
    if request.wind_path:
        readers.append(_reader(request.wind_path))
    if not readers:
        raise missing_input("OpenDrift needs at least one met-ocean NetCDF")
    simulation.add_reader(readers)

    simulation.set_config("general:use_auto_landmask", _LANDMASK)
    simulation.set_config("drift:horizontal_diffusivity", float(request.diffusion_m2_s))
    if model == "OpenOil":
        # Keep the comparison with Euler about advection, not weathering.
        for option, value in (
            ("processes:dispersion", False),
            ("processes:evaporation", False),
            ("processes:emulsification", False),
        ):
            try:
                simulation.set_config(option, value)
            except Exception:                      # noqa: BLE001 - option renamed
                pass

    particles = int(np.size(request.seed_lons))
    simulation.seed_elements(
        lon=np.asarray(request.seed_lons, dtype=float),
        lat=np.asarray(request.seed_lats, dtype=float),
        time=_to_datetime(request.start_time_s),
        wind_drift_factor=float(request.leeway),
    )

    time_step = float(request.dt_seconds) * (1 if request.direction > 0 else -1)
    simulation.run(
        duration=timedelta(hours=float(request.hours)),
        time_step=time_step,
        time_step_output=time_step,
    )

    times_s, lons, lats = _extract(simulation, particles)
    if lons.size == 0:
        raise missing_input(f"the {model} run produced no usable positions")

    return DriftRun(
        times_s=times_s,
        lons=lons,
        lats=lats,
        direction=BACKWARD if request.direction < 0 else 1,
        engine=model.lower(),
    )
