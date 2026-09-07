"""Real AIS wins when it covers the origin; synthesis is the labelled fallback.

Audit A-05/06. The pipeline always chose between real and synthetic AIS, but a
sealed run recorded only a filename -- so "vessels.parquet" could equally mean
a real MarineCadastre archive that happened to contain the origin window or a
fleet invented around it, and nothing in the manifest distinguished the two.

What is pinned here:

* a REAL file that covers the computed origin is used, and nothing is
  synthesised alongside it (no planted culprit);
* a REAL file for the WRONG DAY does not win merely for being real -- its
  vessels never enter this run's cloud, so it is rejected and the fallback runs;
* the fallback stays labelled `synthetic` exactly as before;
* the decision and every rejected candidate reach the manifest, so a reader
  never has to infer which path ran.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

ORIGIN = {"lat": 28.5, "lon": -90.5,
          "window_start_utc": "2023-01-07T12:00:00Z",
          "window_end_utc": "2023-01-08T00:10:00Z"}


def _write_vessels(path: Path, source: str, *, lat=28.5, lon=-90.5,
                   when="2023-01-07T18:00:00Z", rows=6) -> Path:
    import pandas as pd

    t0 = pd.Timestamp(when)
    df = pd.DataFrame({
        "mmsi": [366000000 + i for i in range(rows)],
        "timestamp_utc": [t0 + timedelta(minutes=5 * i) for i in range(rows)],
        "lat": [lat + 0.001 * i for i in range(rows)],
        "lon": [lon + 0.001 * i for i in range(rows)],
        "sog_kn": [8.0] * rows,
        "cog_deg": [90.0] * rows,
        "heading_deg": [90.0] * rows,
        "vessel_type": ["tanker"] * rows,
        "length_m": [180.0] * rows,
        "width_m": [30.0] * rows,
        "draught_m": [9.0] * rows,
        "source": [source] * rows,
        "interpolated": [False] * rows,
        "culprit": [False] * rows,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


class _Stage:
    def __init__(self):
        self.warnings = []


@pytest.fixture
def run_mod(monkeypatch, tmp_path):
    """The pipeline module with its search roots pointed at a scratch tree, so
    the repo's own AIS files cannot decide the outcome of a unit test."""
    from backend.services.pipeline import run as run_mod

    monkeypatch.setattr(run_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run_mod, "MOCKS", tmp_path / "contracts" / "mocks")
    monkeypatch.setattr(run_mod, "origin_summary", lambda _p: dict(ORIGIN))
    return run_mod


# --------------------------------------------------------------------------
# what a file says about itself
# --------------------------------------------------------------------------

def test_data_source_is_read_from_the_column_not_the_path(tmp_path):
    from backend.services.pipeline.run import file_data_source

    real = _write_vessels(tmp_path / "looks_synthetic.parquet", "real")
    fake = _write_vessels(tmp_path / "mc_gulf_real.parquet", "synthetic")
    assert file_data_source(real) == "real"
    assert file_data_source(fake) == "synthetic", "the filename must not decide this"


def test_a_file_without_a_source_column_is_unknown_not_synthetic(tmp_path):
    """'We were never told' and 'it is fabricated' are different claims."""
    import pandas as pd

    from backend.services.pipeline.run import file_data_source

    path = tmp_path / "legacy.parquet"
    pd.DataFrame({"mmsi": [1], "lat": [0.0], "lon": [0.0]}).to_parquet(path)
    assert file_data_source(path) == "unknown"
    assert file_data_source(None) == "none"


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def test_real_ais_covering_the_origin_is_used_and_nothing_is_synthesised(
        run_mod, tmp_path, monkeypatch):
    out_dir = tmp_path / "data" / "runs" / "inv-x"
    _write_vessels(out_dir / "vessels.parquet", "real")

    def _must_not_run(**_kw):
        raise AssertionError("synthesised a fleet while real AIS covered the origin")

    monkeypatch.setattr(run_mod.engines, "generate_ais", _must_not_run)

    stage = _Stage()
    choice = run_mod.ensure_vessels(out_dir, out_dir / "origin_cloud.geojson",
                                    {"scene_id": "S1"}, stage)

    assert choice.selection == "real"
    assert choice.data_source == "real"
    assert choice.covered is True
    assert choice.path.name == "vessels.parquet"
    assert not (run_mod.engine_dir(out_dir) / "culprit.json").exists(), \
        "a culprit was planted beside real data"


def test_real_ais_for_the_wrong_day_does_not_win_on_being_real(
        run_mod, tmp_path, monkeypatch):
    """The trap `run.py` documents: a real archive for another date contains no
    vessel in this cloud, so attribution returns NO_VESSELS_IN_WINDOW and the
    run reads as broken when it was merely handed the wrong day."""
    out_dir = tmp_path / "data" / "runs" / "inv-y"
    out_dir.mkdir(parents=True)
    _write_vessels(tmp_path / "data" / "ais" / "real" / "mc_gulf_2023_01_01.parquet",
                   "real", when="2023-01-01T06:00:00Z")

    generated = run_mod.engine_dir(out_dir) / "vessels_generated.parquet"

    class _Res:
        ok = True

    def _generate(**kw):
        _write_vessels(Path(kw["out"]), "synthetic")
        return _Res()

    monkeypatch.setattr(run_mod.engines, "generate_ais", _generate)

    stage = _Stage()
    choice = run_mod.ensure_vessels(out_dir, out_dir / "origin_cloud.geojson",
                                    {"scene_id": "S1"}, stage)

    assert choice.selection == "synthetic"
    assert choice.data_source == "synthetic"
    assert choice.path == generated
    rejected = {c["file"]: c for c in choice.considered}
    assert rejected["mc_gulf_2023_01_01.parquet"]["data_source"] == "real"
    assert rejected["mc_gulf_2023_01_01.parquet"]["covers_origin"] is False
    assert any("SYNTHETIC" in w for w in stage.warnings), \
        "the fallback must stay labelled in the run's own warnings"


def test_the_manifest_block_names_the_path_and_the_rejections(run_mod, tmp_path):
    out_dir = tmp_path / "data" / "runs" / "inv-z"
    _write_vessels(out_dir / "vessels.parquet", "real")
    _write_vessels(tmp_path / "data" / "ais" / "real" / "other_day.parquet",
                   "real", when="2022-05-01T06:00:00Z")

    choice = run_mod.ensure_vessels(out_dir, out_dir / "origin_cloud.geojson",
                                    None, _Stage())
    block = choice.to_dict()

    assert block["selection"] == "real"
    assert block["data_source"] == "real"
    assert block["covers_origin"] is True
    assert block["file"] == "vessels.parquet"
    # The ledger is the point: without it "we used real AIS" is unfalsifiable.
    files = {c["file"] for c in block["considered"]}
    assert {"vessels.parquet", "other_day.parquet"} <= files


def test_no_origin_window_means_no_file_is_judged(run_mod, tmp_path, monkeypatch):
    """Reporting `covers_origin: false` when nothing was ever tested would be a
    measurement we did not make."""
    monkeypatch.setattr(run_mod, "origin_summary", lambda _p: None)
    out_dir = tmp_path / "data" / "runs" / "inv-w"
    _write_vessels(out_dir / "vessels.parquet", "real")

    choice = run_mod.ensure_vessels(out_dir, out_dir / "origin_cloud.geojson",
                                    None, _Stage())
    assert choice.selection == "unjudged"
    assert choice.considered == []
