"""A stage that did not run has no output, and a scene's position states its basis.

Until 2026-09-20 an unavailable stage was served a static file from
contracts/mocks/, so a clean scene received a fabricated slick off Chennai plus
an origin cloud and a suspect list. These tests pin the replacement behaviour.
"""
import json
from pathlib import Path

from backend.services.pipeline import run as pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stage():
    return pipeline.Stage("characterise", "Nandha", "slick", "slick.geojson")


def test_an_unavailable_stage_writes_nothing(tmp_path):
    s = _stage()
    assert pipeline.stage_unavailable(s, tmp_path, "no oil region to characterise") is False
    assert not (tmp_path / "slick.geojson").exists()
    assert (s.status, s.source) == ("failed", "none")
    assert "no oil region" in s.detail


def test_a_partial_output_is_not_left_behind(tmp_path):
    (tmp_path / "slick.geojson").write_text("{}", encoding="utf-8")
    pipeline.stage_mocked(_stage(), tmp_path, "Engine A failed: EMPTY_MASK")
    assert not (tmp_path / "slick.geojson").exists()


def test_the_pipeline_no_longer_copies_from_the_contract_mocks():
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "def serve_mock" not in source
    assert "shutil.copy(src, out_dir / stage.output)" not in source


def test_position_basis_distinguishes_measured_from_synthetic():
    from backend.api.sar_database import basis_for_meta

    mock_meta = json.loads((REPO_ROOT / "contracts" / "mocks" / "scene_meta.json")
                           .read_text(encoding="utf-8"))
    assert basis_for_meta(mock_meta)["geo_basis"] == "synthetic"

    real = basis_for_meta({
        "scene_id": "S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1",
        "file_path": "data/scenes/x.tif"})
    assert real["geo_basis"] == "measured" and real["label"] == "REAL"

    unknown = basis_for_meta({"scene_id": "corpus-chip-0042", "file_path": "data/raw/trujillo/0042.tif"})
    assert unknown["geo_basis"] == "assigned"
    assert "ASSIGNED" in unknown["geo_basis_note"]
