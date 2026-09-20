"""The wind and current field shown for a run must be the one its drift used.

`/runs/{id}/forcing_field` used to re-resolve forcing at request time. That
is the pipeline's rule for choosing a grid TODAY, not a record of the grid the
run integrated, and the two drift apart as the cache grows: measured over the
14 recorded grids in the live registry, one run whose drift used the
Open-Meteo fallback wind was being shown ERA5 wind instead.

The run records each grid's normalisation stamp (the NetCDF ``history``
attribute). Pinned here: the file with that stamp is served even when the
resolver would pick another; a resolver pick that differs is reported as
``matches_run: false``; and a run that recorded nothing says ``null``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

from backend.api import replay  # noqa: E402

xr = pytest.importorskip("xarray")


def _grid(path: Path, stamp: str, u: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = xr.Dataset(
        {"u10": (("time", "lat", "lon"), np.full((2, 3, 3), u, np.float32)),
         "v10": (("time", "lat", "lon"), np.zeros((2, 3, 3), np.float32))},
        coords={"time": np.array(["2023-01-08T00:00", "2023-01-08T01:00"],
                                 dtype="datetime64[ns]"),
                "lat": [27.0, 27.5, 28.0], "lon": [-91.0, -90.5, -90.0]},
        attrs={"history": stamp})
    ds.to_netcdf(path)
    return path


@pytest.fixture
def run(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    d = runs / "inv-forcing-test"
    d.mkdir(parents=True)
    (d / "scene_meta.json").write_text(json.dumps(
        {"scene_id": "S1A_TEST", "acquired_utc": "2023-01-08T00:10:08Z"}))
    monkeypatch.setattr(replay.settings, "runs_root", runs, raising=False)
    return d


def _record(d: Path, wind_stamp):
    forcing = {"wind": {"provider": "Open-Meteo API", "file": "wind.nc",
                        "normalised": wind_stamp}} if wind_stamp else {}
    (d / "origin_cloud.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [],
         "metadata": {"forcing": forcing}}))


def test_serves_the_recorded_grid_when_the_resolver_would_pick_another(run, monkeypatch):
    used = _grid(run / "wind_openmeteo.nc", "Normalized at T1", u=2.0)
    other = _grid(run / "other" / "wind.nc", "Normalized at T2", u=9.0)
    _record(run, "Normalized at T1")
    monkeypatch.setattr("backend.services.pipeline.run.resolve_metocean",
                        lambda meta, d: (None, other))
    out = replay.forcing_field("inv-forcing-test")
    assert out["wind"]["file"] == used.name
    assert out["wind"]["matches_run"] is True
    assert out["wind"]["mean_speed"] == pytest.approx(2.0)
    assert out["wind"]["provider"] == "Open-Meteo API"


def test_says_so_when_the_recorded_grid_is_gone(run, monkeypatch):
    other = _grid(run / "wind.nc", "Normalized at T2", u=9.0)
    _record(run, "Normalized at T1")
    monkeypatch.setattr("backend.services.pipeline.run.resolve_metocean",
                        lambda meta, d: (None, other))
    out = replay.forcing_field("inv-forcing-test")
    assert out["wind"]["file"] == "wind.nc"
    assert out["wind"]["matches_run"] is False


def test_null_when_the_run_recorded_nothing_to_compare(run, monkeypatch):
    other = _grid(run / "wind.nc", "Normalized at T2", u=9.0)
    _record(run, None)
    monkeypatch.setattr("backend.services.pipeline.run.resolve_metocean",
                        lambda meta, d: (None, other))
    out = replay.forcing_field("inv-forcing-test")
    assert out["wind"]["matches_run"] is None
    assert out["currents"] is None


def test_forcing_is_refused_while_a_pipeline_run_holds_the_grids(monkeypatch):
    """HDF5 is not thread-safe here: opening the grids while a run's drift
    stage integrates them segfaulted the server. The endpoint must refuse
    (503, retryable) rather than read, and must give the gate back."""
    from fastapi import HTTPException

    from backend.api import routes

    read = []
    monkeypatch.setattr(replay, "_forcing_field_now", lambda run_id: read.append(run_id) or {"run_id": run_id})

    assert routes._pipeline_gate.acquire(blocking=False), "test needs the gate free"
    try:
        with pytest.raises(HTTPException) as e:
            replay.forcing_field("any-run")
        assert e.value.status_code == 503
        assert e.value.headers["Retry-After"]
        assert read == [], "nothing was read while the run held the gate"
    finally:
        routes._pipeline_gate.release()

    assert replay.forcing_field("any-run") == {"run_id": "any-run"}
    assert routes._pipeline_gate.acquire(blocking=False), "the endpoint released the gate"
    routes._pipeline_gate.release()
