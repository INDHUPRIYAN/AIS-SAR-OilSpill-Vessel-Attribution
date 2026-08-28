"""Engine A end-to-end: contract compliance, the confidence cascade, failure classes.

Handbook §8 must-pass items covered here: the output validates against the schema, an
empty mask returns EMPTY_MASK, and nothing crashes - every declared failure comes back
as a status object.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.characterise import characterise
from engines.characterise.__main__ import main as cli_main
from engines.schemas.slick import validate_slick
from tests.fixtures.make_mask import build_scene

# Anchored to this file so the suite passes from any working directory.
# These paths used to be CWD-relative, which meant the tests only ran
# when pytest happened to be invoked from analysis_engines/.
MODULE_ROOT = Path(__file__).resolve().parents[1]


CONFIG = str(MODULE_ROOT / "config" / "characterise.yaml")


@pytest.fixture(scope="module")
def scene(tmp_path_factory) -> dict:
    return build_scene(tmp_path_factory.mktemp("runner"))


@pytest.fixture(scope="module")
def empty_scene(tmp_path_factory) -> dict:
    return build_scene(tmp_path_factory.mktemp("runner_empty"), empty=True)


@pytest.fixture(scope="module")
def run(scene, tmp_path_factory) -> tuple[dict, dict]:
    """Run the engine once; return (status, parsed slick.geojson)."""
    out = tmp_path_factory.mktemp("out") / "slick.geojson"
    status = characterise(
        scene["mask_path"], scene["scene_meta_path"], out, config_path=CONFIG
    )
    return status, json.loads(Path(out).read_text(encoding="utf-8"))


# ------------------------------------------------------------------- contract ------
def test_run_succeeds_with_a_status_object(run):
    status, _ = run
    assert status["ok"] is True
    assert status["engine_used"] == "primary"
    assert "slick" in status["outputs"]
    assert isinstance(status["warnings"], list)


def test_output_validates_against_the_pydantic_contract(run):
    _, document = run
    validate_slick(document)          # raises if the contract is broken


def test_properties_carry_every_contract_field(run, scene):
    _, document = run
    assert len(document["features"]) == 2          # the speck is not a slick

    props = document["features"][0]["properties"]
    assert set(props) == {
        "slick_id", "scene_id", "detected_utc", "confidence",
        "area_km2", "perimeter_km", "centroid",
        "major_axis_km", "minor_axis_km", "orientation_deg",
        "damping_ratio_db", "age_hours_est", "age_method", "age_confidence",
    }
    drawn = scene["slicks"][0]
    assert props["area_km2"] == pytest.approx(drawn["area_km2"], rel=0.01)
    assert props["orientation_deg"] == pytest.approx(drawn["orientation_deg"], abs=1.0)
    assert props["damping_ratio_db"] == pytest.approx(scene["expected_damping_db"], abs=0.3)
    assert props["age_method"] == "damping+fay"
    assert props["age_confidence"] == "low"


def test_timestamps_are_utc_with_a_z_suffix(run, scene):
    _, document = run
    assert document["features"][0]["properties"]["detected_utc"] == scene["acquired_utc"]
    assert document["features"][0]["properties"]["detected_utc"].endswith("Z")


def test_slick_ids_are_stable_and_ordered(run):
    _, document = run
    ids = [f["properties"]["slick_id"] for f in document["features"]]
    assert ids == ["DEMO-A_slick_01", "DEMO-A_slick_02"]
    areas = [f["properties"]["area_km2"] for f in document["features"]]
    assert areas == sorted(areas, reverse=True)


def test_slick_id_prefix_can_be_overridden(scene, tmp_path):
    out = tmp_path / "slick.geojson"
    characterise(
        scene["mask_path"], scene["scene_meta_path"], out,
        config_path=CONFIG, slick_id_prefix="inv-001",
    )
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["features"][0]["properties"]["slick_id"] == "inv-001_slick_01"


# ---------------------------------------------------------- confidence cascade -----
def test_confidence_comes_from_the_scene_metadata(run, scene):
    _, document = run
    assert document["features"][0]["properties"]["confidence"] == scene["confidence"]


def test_confidence_falls_back_to_the_flag_then_to_null(scene, tmp_path):
    meta = json.loads(Path(scene["scene_meta_path"]).read_text(encoding="utf-8"))
    meta.pop("confidence")
    stripped = tmp_path / "scene_meta_noconf.json"
    stripped.write_text(json.dumps(meta), encoding="utf-8")

    # Rung 2: the --confidence flag.
    out = tmp_path / "flag.geojson"
    status = characterise(
        scene["mask_path"], stripped, out, config_path=CONFIG,
        scene_db_path=scene["db_path"], confidence=0.42,
    )
    assert status["ok"]
    assert json.loads(out.read_text())["features"][0]["properties"]["confidence"] == 0.42

    # Rung 3: null, with a warning explaining why.
    out2 = tmp_path / "null.geojson"
    status2 = characterise(
        scene["mask_path"], stripped, out2, config_path=CONFIG,
        scene_db_path=scene["db_path"],
    )
    assert status2["ok"]
    assert json.loads(out2.read_text())["features"][0]["properties"]["confidence"] is None
    assert any("confidence" in w for w in status2["warnings"])


def test_out_of_range_confidence_is_rejected(scene, tmp_path):
    # The metadata value wins the cascade, so strip it to force the flag to be used.
    meta = json.loads(Path(scene["scene_meta_path"]).read_text(encoding="utf-8"))
    meta.pop("confidence")
    stripped = tmp_path / "meta.json"
    stripped.write_text(json.dumps(meta), encoding="utf-8")
    status = characterise(
        scene["mask_path"], stripped, tmp_path / "y.geojson",
        config_path=CONFIG, confidence=1.5,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


# ------------------------------------------------------- degradation, no crash -----
def test_missing_db_band_degrades_to_area_only_fay(scene, tmp_path):
    meta = json.loads(Path(scene["scene_meta_path"]).read_text(encoding="utf-8"))
    meta.pop("file_path")
    stripped = tmp_path / "scene_meta_nodb.json"
    stripped.write_text(json.dumps(meta), encoding="utf-8")

    out = tmp_path / "slick.geojson"
    status = characterise(scene["mask_path"], stripped, out, config_path=CONFIG)
    assert status["ok"] is True
    assert any("damping ratio omitted" in w for w in status["warnings"])

    props = json.loads(out.read_text())["features"][0]["properties"]
    assert props["damping_ratio_db"] is None
    assert props["age_method"] == "fay"


# ---------------------------------------------------------------- error classes ----
def test_empty_mask_returns_EMPTY_MASK(empty_scene, tmp_path):
    out = tmp_path / "slick.geojson"
    status = characterise(
        empty_scene["mask_path"], empty_scene["scene_meta_path"], out, config_path=CONFIG
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "EMPTY_MASK"
    assert not out.exists()          # no half-written contract file


def test_missing_mask_returns_MISSING_INPUT(scene, tmp_path):
    status = characterise(
        tmp_path / "nope.tif", scene["scene_meta_path"], tmp_path / "o.geojson",
        config_path=CONFIG,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


def test_missing_scene_meta_returns_MISSING_INPUT(scene, tmp_path):
    status = characterise(
        scene["mask_path"], tmp_path / "nope.json", tmp_path / "o.geojson",
        config_path=CONFIG,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda m: m.pop("scene_id"), "no scene_id"),
        (lambda m: [m.pop(k) for k in ("acquisition_time",)], "no acquisition time"),
        (lambda m: m.update(acquisition_time="2017-02-02T06:09:42"), "naive timestamp"),
    ],
)
def test_bad_scene_metadata_returns_MISSING_INPUT(scene, tmp_path, mutate, reason):
    """Including the handbook's IST trap: a naive local time must be rejected, not
    silently treated as UTC."""
    meta = json.loads(Path(scene["scene_meta_path"]).read_text(encoding="utf-8"))
    mutate(meta)
    broken = tmp_path / f"meta_{abs(hash(reason))}.json"
    broken.write_text(json.dumps(meta), encoding="utf-8")

    status = characterise(
        scene["mask_path"], broken, tmp_path / "o.geojson", config_path=CONFIG
    )
    assert status["ok"] is False, reason
    assert status["error"]["error_class"] == "MISSING_INPUT", reason


def test_corrupt_raster_returns_MISSING_INPUT(scene, tmp_path):
    fake = tmp_path / "corrupt.tif"
    fake.write_bytes(b"this is not a GeoTIFF")
    status = characterise(
        fake, scene["scene_meta_path"], tmp_path / "o.geojson", config_path=CONFIG
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


# ------------------------------------------------------------------------ CLI ------
def test_cli_exit_codes(scene, tmp_path, capsys):
    out = tmp_path / "cli.geojson"
    code = cli_main([
        "--mask", str(scene["mask_path"]),
        "--scene-meta", str(scene["scene_meta_path"]),
        "--out", str(out),
        "--config", CONFIG,
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    validate_slick(json.loads(out.read_text(encoding="utf-8")))


def test_cli_reports_engine_errors_as_json_not_tracebacks(empty_scene, tmp_path, capsys):
    code = cli_main([
        "--mask", str(empty_scene["mask_path"]),
        "--scene-meta", str(empty_scene["scene_meta_path"]),
        "--out", str(tmp_path / "cli.geojson"),
        "--config", CONFIG,
    ])
    assert code == 2
    status = json.loads(capsys.readouterr().out)
    assert status["error"]["error_class"] == "EMPTY_MASK"
