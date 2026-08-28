"""Engine selection: OpenOil -> OceanDrift -> Euler (handbook §6 Phase 3).

Split in two on purpose.

The first group runs **now, without OpenDrift installed**, and is the group that actually
protects the project: it pins the degradation path, which is what every run currently
takes and what the demo depends on.

The second group is marked ``requires_opendrift`` and skips itself until the conda
environment exists. Those tests are the only thing that will ever have executed
``opendrift_adapter.py``, so until they run, that file is unverified - see
``engines/drift/README.md``.
"""

from __future__ import annotations

import json

import numpy as np
from pathlib import Path
import pytest

from engines.characterise import characterise
from engines.common.errors import EngineError
from engines.drift import hindcast
from engines.drift.backends import (
    AUTO,
    BACKENDS,
    ENGINE_ORDER,
    DriftRequest,
    EulerBackend,
    OceanDriftBackend,
    OpenOilBackend,
    select_backend,
)
from engines.drift.grids import load_metocean
from engines.schemas.origin_cloud import validate_origin_cloud
from tests.fixtures.make_mask import build_scene
from tests.fixtures.make_metocean import build_metocean

# Anchored to this file so the suite passes from any working directory.
# These paths used to be CWD-relative, which meant the tests only ran
# when pytest happened to be invoked from analysis_engines/.
MODULE_ROOT = Path(__file__).resolve().parents[1]


requires_opendrift = pytest.mark.skipif(
    OpenOilBackend.is_available()[0] is False,
    reason="OpenDrift is not installed; build the conda env in engines/drift/README.md",
)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory) -> dict:
    work = tmp_path_factory.mktemp("backends")
    scene = build_scene(work / "scene")
    met = build_metocean(work / "met")
    slick = work / "slick.geojson"
    assert characterise(
        scene["mask_path"], scene["scene_meta_path"], slick,
        config_path=str(MODULE_ROOT / "config" / "characterise.yaml"),
    )["ok"]
    return {"slick": slick, "met": met, "work": work}


# ------------------------------------------------------------------ selection ------
def test_preference_order_matches_the_handbook():
    assert ENGINE_ORDER == ("openoil", "oceandrift", "euler")
    assert set(BACKENDS) == set(ENGINE_ORDER)


def test_euler_is_always_available():
    """The last fallback in every chain must be dependency-free (handbook §1)."""
    available, reason = EulerBackend.is_available()
    assert available is True
    assert reason


def test_auto_prefers_the_first_available_engine(monkeypatch):
    monkeypatch.setattr(OpenOilBackend, "is_available", classmethod(lambda cls: (True, "stub")))
    backend, warnings = select_backend(AUTO)
    assert backend is OpenOilBackend
    assert warnings == []


def test_auto_falls_through_to_oceandrift_then_euler(monkeypatch):
    monkeypatch.setattr(
        OpenOilBackend, "is_available", classmethod(lambda cls: (False, "no openoil"))
    )
    monkeypatch.setattr(
        OceanDriftBackend, "is_available", classmethod(lambda cls: (True, "stub"))
    )
    backend, warnings = select_backend(AUTO)
    assert backend is OceanDriftBackend
    assert any("openoil" in w for w in warnings)


def test_auto_lands_on_euler_and_says_why(monkeypatch):
    for backend in (OpenOilBackend, OceanDriftBackend):
        monkeypatch.setattr(
            backend, "is_available", classmethod(lambda cls: (False, "not installed"))
        )
    backend, warnings = select_backend(AUTO)
    assert backend is EulerBackend
    assert any("openoil" in w and "unavailable" in w for w in warnings)
    assert any("Euler" in w for w in warnings)


def test_engine_kinds_map_to_the_status_vocabulary():
    assert EulerBackend.kind == "fallback"
    assert OpenOilBackend.kind == OceanDriftBackend.kind == "primary"


# ------------------------------------------------------- pinning an engine ---------
def test_pinning_an_unavailable_engine_is_an_error_not_a_downgrade(monkeypatch):
    """Silently running a different model would change what the output means."""
    monkeypatch.setattr(
        OpenOilBackend, "is_available", classmethod(lambda cls: (False, "not installed"))
    )
    with pytest.raises(EngineError) as excinfo:
        select_backend("openoil")
    assert excinfo.value.error_class == "MISSING_INPUT"
    assert "not available" in excinfo.value.message


def test_pinning_an_unknown_engine_lists_the_valid_ones():
    with pytest.raises(EngineError) as excinfo:
        select_backend("openoil-turbo")
    assert excinfo.value.error_class == "MISSING_INPUT"
    assert "euler" in excinfo.value.message


def test_pinning_euler_always_works():
    backend, warnings = select_backend("euler")
    assert backend is EulerBackend and warnings == []


def test_cli_can_pin_the_engine(inputs, tmp_path):
    from engines.drift.__main__ import main as cli_main

    out = tmp_path / "pinned.geojson"
    code = cli_main([
        "--slick", str(inputs["slick"]),
        "--currents", inputs["met"]["currents_strain"],
        "--wind", inputs["met"]["wind_zero"],
        "--mode", "hindcast", "--engine", "euler",
        "--out", str(out), "--config", str(MODULE_ROOT / "config" / "drift.yaml"),
    ])
    assert code == 0
    validate_origin_cloud(json.loads(out.read_text(encoding="utf-8")))


def test_requesting_openoil_when_absent_fails_the_run(inputs, tmp_path):
    if OpenOilBackend.is_available()[0]:
        pytest.skip("OpenDrift is installed, so this cannot fail")
    status = hindcast(
        inputs["slick"], tmp_path / "x.geojson",
        currents_path=inputs["met"]["currents_strain"],
        config_path=str(MODULE_ROOT / "config" / "drift.yaml"), engine="openoil",
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


# ------------------------------------------------- the degradation path today ------
def test_runs_degrade_to_euler_and_report_it(inputs, tmp_path):
    """What every run currently does, and what the demo depends on."""
    out = tmp_path / "auto.geojson"
    status = hindcast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_strain"],
        wind_path=inputs["met"]["wind_uniform"],
        config_path=str(MODULE_ROOT / "config" / "drift.yaml"),
    )
    assert status["ok"] is True

    if OpenOilBackend.is_available()[0]:
        assert status["engine_used"] == "primary"
        return

    assert status["engine_used"] == "fallback"
    assert any("unavailable" in w for w in status["warnings"])
    window = next(
        f["properties"] for f in json.loads(out.read_text())["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    assert window["engine_used"] == "euler"


def test_euler_backend_matches_a_direct_call(inputs):
    """Routing through DriftRequest must not change the Euler result at all."""
    from engines.drift.euler_fallback import BACKWARD, run_euler

    metocean, _ = load_metocean(
        inputs["met"]["currents_strain"], inputs["met"]["wind_zero"]
    )
    seeds_lon = np.array([80.30, 80.31, 80.32])
    seeds_lat = np.array([13.04, 13.05, 13.06])
    common = dict(hours=6.0, dt_seconds=600.0, direction=BACKWARD, diffusion_m2_s=0.0)

    direct = run_euler(seeds_lon, seeds_lat, metocean, 1_485_909_582.0, **common)
    viaBackend = EulerBackend.run(
        DriftRequest(
            seed_lons=seeds_lon, seed_lats=seeds_lat, start_time_s=1_485_909_582.0,
            metocean=metocean, **common,
        )
    )
    assert np.allclose(direct.lons, viaBackend.lons)
    assert np.allclose(direct.lats, viaBackend.lats)


def test_euler_backend_without_grids_is_MISSING_INPUT():
    with pytest.raises(EngineError) as excinfo:
        EulerBackend.run(
            DriftRequest(
                seed_lons=np.array([80.3]), seed_lats=np.array([13.0]),
                start_time_s=0.0, hours=1.0, dt_seconds=600.0, direction=-1,
                diffusion_m2_s=0.0, metocean=None,
            )
        )
    assert excinfo.value.error_class == "MISSING_INPUT"


# ------------------------------------------------------------- CF conventions ------
def test_mock_netcdfs_carry_the_cf_names_opendrift_maps_by(inputs):
    """OpenDrift's reader maps variables by standard_name, not by variable name.

    Keerthana's real CMEMS/ERA5 files carry these names, so the mocks must too - or the
    OpenDrift path would fail on our own fixtures the first time it ran.
    """
    import xarray as xr

    with xr.open_dataset(inputs["met"]["currents_strain"]) as currents:
        assert currents["u"].attrs["standard_name"] == "x_sea_water_velocity"
        assert currents["v"].attrs["standard_name"] == "y_sea_water_velocity"
    with xr.open_dataset(inputs["met"]["wind_uniform"]) as wind:
        assert wind["u10"].attrs["standard_name"] == "x_wind"
        assert wind["v10"].attrs["standard_name"] == "y_wind"


# ------------------------------------------- only run once OpenDrift is installed ---
@requires_opendrift
@pytest.mark.parametrize("engine", ["oceandrift", "openoil"])
def test_opendrift_produces_a_valid_origin_cloud(inputs, tmp_path, engine):
    out = tmp_path / f"{engine}.geojson"
    status = hindcast(
        inputs["slick"], out,
        currents_path=inputs["met"]["currents_strain"],
        wind_path=inputs["met"]["wind_uniform"],
        config_path=str(MODULE_ROOT / "config" / "drift.yaml"), engine=engine,
    )
    assert status["ok"] is True, status
    assert status["engine_used"] == "primary"
    validate_origin_cloud(json.loads(out.read_text(encoding="utf-8")))


@requires_opendrift
def test_euler_matches_opendrift_direction(inputs, tmp_path):
    """Handbook §8: the fallback must agree with OpenDrift on the same field.

    Direction, not position: these are different physics, and a tight tolerance would
    only produce a flaky test. Bearings within 15 degrees and displacement within a
    factor of two is enough to catch a sign error or a broken unit conversion, which is
    what this test is really for.
    """
    import math

    from engines.common.geo import bearing_deg, m_per_deg_lat, m_per_deg_lon

    def displacement(engine: str) -> tuple[float, float]:
        out = tmp_path / f"{engine}_cmp.geojson"
        status = hindcast(
            inputs["slick"], out,
            currents_path=inputs["met"]["currents_uniform"],
            wind_path=inputs["met"]["wind_zero"],
            config_path=str(MODULE_ROOT / "config" / "drift.yaml"), engine=engine, hours=12.0,
        )
        assert status["ok"], status
        features = json.loads(out.read_text(encoding="utf-8"))["features"]
        start = [
            f["geometry"]["coordinates"] for f in features
            if not f["properties"].get("kind") and f["properties"]["timestep_h"] == 0.0
        ]
        end = [
            f["geometry"]["coordinates"] for f in features
            if not f["properties"].get("kind") and f["properties"]["timestep_h"] == -12.0
        ]
        lon0 = sum(c[0] for c in start) / len(start)
        lat0 = sum(c[1] for c in start) / len(start)
        lon1 = sum(c[0] for c in end) / len(end)
        lat1 = sum(c[1] for c in end) / len(end)
        east = (lon1 - lon0) * m_per_deg_lon(lat0)
        north = (lat1 - lat0) * m_per_deg_lat(lat0)
        return bearing_deg(east, north), math.hypot(east, north)

    euler_bearing, euler_distance = displacement("euler")
    other_bearing, other_distance = displacement("oceandrift")

    offset = abs((euler_bearing - other_bearing + 180.0) % 360.0 - 180.0)
    assert offset < 15.0, f"bearings disagree by {offset:.1f} deg"
    assert 0.5 < euler_distance / other_distance < 2.0
