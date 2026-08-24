"""The committed artifacts must stay valid, not just freshly-generated output.

Every engine validates what it writes, and the per-engine tests check that. This file
checks something different: that the files actually sitting in ``samples/`` - the ones
another developer clones and integrates against - still match the contracts. Without
this, a schema change would leave stale examples in the repository and the first person
to notice would be Indhu, mid-integration.

Handbook Part G items 4, 5 and 10: example inputs committed, example outputs committed,
and another developer able to consume them using only the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.schemas import (

    validate_forecast,
    validate_origin_cloud,
    validate_slick,
    validate_suspects,
)

# Anchored to this file so the suite passes from any working directory.
# These paths used to be CWD-relative, which meant the tests only ran
# when pytest happened to be invoked from 2_nandha_engines/.
MODULE_ROOT = Path(__file__).resolve().parents[1]

SAMPLES = (MODULE_ROOT / "samples")
INPUTS = SAMPLES / "inputs"

CONTRACTS = [
    ("slick.geojson", validate_slick),
    ("origin_cloud.geojson", validate_origin_cloud),
    ("forecast.geojson", validate_forecast),
    ("suspects.json", validate_suspects),
]


@pytest.mark.parametrize("filename, validator", CONTRACTS)
def test_committed_output_validates(filename, validator):
    path = SAMPLES / filename
    assert path.is_file(), f"{path} is missing; run `python scripts/run_all.py --out samples`"
    validator(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "filename",
    ["mask.tif", "scene_db.tif", "scene_meta.json", "currents.nc", "wind.nc",
     "vessels.parquet", "MANIFEST.json"],
)
def test_committed_input_exists(filename):
    """Part G: a clone must be runnable without regenerating anything first."""
    path = INPUTS / filename
    assert path.is_file(), f"{path} is missing; run `python scripts/make_samples.py`"
    assert path.stat().st_size > 0


def test_committed_inputs_stay_small_enough_for_git():
    """The full-resolution fixture set is 10 MB; the committed demo set must not be."""
    total = sum(f.stat().st_size for f in INPUTS.iterdir() if f.is_file())
    assert total < 4 * 1024 * 1024, f"samples/inputs is {total / 1e6:.1f} MB"


def test_sample_outputs_describe_the_same_investigation():
    """The four files are one pipeline run, not four unrelated examples."""
    slick = json.loads((SAMPLES / "slick.geojson").read_text(encoding="utf-8"))
    cloud = json.loads((SAMPLES / "origin_cloud.geojson").read_text(encoding="utf-8"))
    suspects = json.loads((SAMPLES / "suspects.json").read_text(encoding="utf-8"))

    detected = slick["features"][0]["properties"]["detected_utc"]
    window = next(
        f["properties"] for f in cloud["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    # The hindcast runs backward from detection, so the window precedes it.
    assert window["end_utc"] <= detected
    assert suspects["origin_window"]["peak_utc"] == window["peak_utc"]


def test_sample_suspects_name_a_top_ranked_vessel():
    suspects = json.loads((SAMPLES / "suspects.json").read_text(encoding="utf-8"))
    ranked = [v for v in suspects["vessels"] if not v.get("filtered")]
    assert ranked, "the committed sample should demonstrate a ranked suspect"
    assert ranked[0]["rank"] == 1
    assert ranked[0]["reason"]
    assert all(v.get("filter_reason") for v in suspects["vessels"] if v.get("filtered"))
