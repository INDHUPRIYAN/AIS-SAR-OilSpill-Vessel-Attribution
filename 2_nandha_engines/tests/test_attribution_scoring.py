"""Engine C Phase 6: the six factors, ranking, explanations and suspects.json.

The headline test is the handbook §8 must-pass item - the planted culprit ranks top-1 -
run against output from real Engine A and Engine B runs, not a hand-written stand-in.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from engines.attribution import attribute, load_vessels
from engines.attribution.__main__ import main as cli_main
from engines.attribution.explain import explain_filtered
from engines.attribution.gates import GateConfig, apply_gates, build_origin_context
from engines.attribution.scoring import (
    DEFAULT_WEIGHTS,
    FACTORS,
    ScoringConfig,
    build_density,
    score_vessel,
    window_path,
)
from engines.characterise import characterise
from engines.drift import hindcast
from engines.schemas.suspects import validate_suspects
from tests.fixtures.make_mask import build_scene
from tests.fixtures.make_metocean import build_metocean
from tests.fixtures.make_vessels import build_vessels

CULPRIT_MMSI = 419001234
FERRY_MMSI = 419005555
WEIGHTS = "config/attribution_weights.yaml"


@pytest.fixture(scope="module")
def scenario(tmp_path_factory) -> dict:
    work = tmp_path_factory.mktemp("scoring")
    scene = build_scene(work / "scene")
    met = build_metocean(work / "met")

    slick = work / "slick.geojson"
    assert characterise(
        scene["mask_path"], scene["scene_meta_path"], slick,
        config_path="config/characterise.yaml",
    )["ok"]

    cloud = work / "origin_cloud.geojson"
    assert hindcast(
        slick, cloud, currents_path=met["currents_strain"],
        wind_path=met["wind_uniform"], config_path="config/drift.yaml",
    )["ok"]

    document = json.loads(cloud.read_text(encoding="utf-8"))
    window = next(
        f for f in document["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    lon, lat = window["geometry"]["coordinates"]
    truth = build_vessels(
        work / "ais", origin_lon=lon, origin_lat=lat,
        window_start=window["properties"]["start_utc"],
        window_end=window["properties"]["end_utc"],
    )
    return {
        "work": work, "slick": slick, "cloud": cloud,
        "document": document, "truth": truth,
    }


@pytest.fixture(scope="module")
def run(scenario) -> tuple[dict, dict]:
    out = scenario["work"] / "suspects.json"
    status = attribute(
        scenario["cloud"], scenario["truth"]["vessels_path"], out,
        weights_path=WEIGHTS, slick_path=scenario["slick"],
    )
    assert status["ok"], status
    return status, json.loads(out.read_text(encoding="utf-8"))


def _ranked(document: dict) -> list[dict]:
    return [v for v in document["vessels"] if not v.get("filtered")]


# ------------------------------------------------------- the must-pass test --------
def test_planted_culprit_ranks_top_1(run):
    """Handbook §8: the planted culprit must rank top-1 on the synthetic scenario."""
    _, document = run
    ranked = _ranked(document)
    assert ranked, "no vessel survived the gates"
    assert ranked[0]["mmsi"] == CULPRIT_MMSI, [
        (v["mmsi"], v["score_total"]) for v in ranked
    ]


def test_culprit_beats_the_innocent_ferry_by_a_clear_margin(run):
    """Both sit in the cloud; only one behaves like a discharge."""
    _, document = run
    scores = {v["mmsi"]: v["score_total"] for v in _ranked(document)}
    assert scores[CULPRIT_MMSI] > scores[FERRY_MMSI] + 0.15


def test_an_ais_gap_over_the_origin_does_not_suppress_proximity(scenario):
    """Regression: the blackout that incriminates a vessel must not lower its score.

    Sampling density only at transmitted fixes made the culprit's proximity 0.004 and
    dropped it to rank 2 - going dark over the origin was being rewarded. Proximity is
    measured along the interpolated path instead.
    """
    config = yaml.safe_load(Path(WEIGHTS).read_text())
    gate_config = GateConfig.from_config(config.get("gates"))
    origin, _ = build_origin_context(scenario["document"], gate_config)
    density = build_density(scenario["document"], origin)
    tracks = {t.mmsi: t for t in load_vessels(scenario["truth"]["vessels_path"])[0]}

    culprit = tracks[CULPRIT_MMSI]
    gates = apply_gates(culprit, origin, gate_config)
    scores = score_vessel(culprit, gates, origin, density, ScoringConfig.from_config(config))

    assert scores.proximity > 0.5, "the AIS gap is suppressing proximity again"
    assert scores.ais_gap > 0.5, "the gap should still register as suspicious"

    # The gap is real: consecutive fixes are far more than a reporting interval apart.
    assert culprit.gaps_s().max() > 30 * 60


def test_window_path_interpolates_across_a_gap(scenario):
    config = yaml.safe_load(Path(WEIGHTS).read_text())
    origin, _ = build_origin_context(
        scenario["document"], GateConfig.from_config(config.get("gates"))
    )
    tracks = {t.mmsi: t for t in load_vessels(scenario["truth"]["vessels_path"])[0]}
    lons, lats = window_path(tracks[CULPRIT_MMSI], origin)
    assert lons.size > 100
    assert np.isfinite(lons).all() and np.isfinite(lats).all()


# ------------------------------------------------------------------ factors --------
def test_every_factor_is_normalised(run):
    _, document = run
    for vessel in _ranked(document):
        assert set(vessel["scores"]) == set(FACTORS)
        for factor, value in vessel["scores"].items():
            assert 0.0 <= value <= 1.0, f"{factor}={value}"
        assert 0.0 <= vessel["score_total"] <= 1.0


def test_culprit_factors_reflect_its_designed_behaviour(run):
    _, document = run
    culprit = next(v for v in _ranked(document) if v["mmsi"] == CULPRIT_MMSI)
    scores = culprit["scores"]
    assert scores["ais_gap"] >= 0.9          # 47-minute blackout over the window
    assert scores["anomaly"] > 0.3           # 13.8 -> 5.9 kn
    assert scores["prior"] >= 0.9            # laden tanker
    assert scores["temporal"] > 0.8          # present at the peak
    assert scores["trajectory"] > 0.8        # running along the slick axis


def test_ferry_scores_a_low_prior(run):
    _, document = run
    ferry = next(v for v in _ranked(document) if v["mmsi"] == FERRY_MMSI)
    assert ferry["scores"]["prior"] < 0.3
    assert ferry["scores"]["ais_gap"] == 0.0
    assert ferry["scores"]["anomaly"] == 0.0


def test_total_is_the_weighted_sum_of_the_factors(run):
    _, document = run
    weights = document["weights"]
    for vessel in _ranked(document):
        expected = sum(weights[f] * vessel["scores"][f] for f in FACTORS)
        assert vessel["score_total"] == pytest.approx(expected, abs=0.002)


def test_weights_are_renormalised_when_they_do_not_sum_to_one(tmp_path):
    config = ScoringConfig(weights={f: 2.0 for f in FACTORS})
    weights, warnings = config.normalised_weights()
    assert sum(weights.values()) == pytest.approx(1.0)
    assert any("renormalised" in w for w in warnings)


def test_unknown_weight_keys_are_ignored_with_a_warning():
    config = ScoringConfig(weights={**DEFAULT_WEIGHTS, "vibes": 0.5})
    weights, warnings = config.normalised_weights()
    assert "vibes" not in weights
    assert any("unknown" in w for w in warnings)


# ------------------------------------------------------------- explanations --------
def test_culprit_explanation_quotes_its_evidence(run):
    """The sentence must be checkable against the AIS data, not a restatement of score."""
    _, document = run
    reason = next(v for v in _ranked(document) if v["mmsi"] == CULPRIT_MMSI)["reason"]
    assert "origin region" in reason
    assert "kn" in reason and "slowed" in reason
    assert "AIS gap" in reason
    assert reason.endswith(".")


def test_unremarkable_vessel_gets_an_honest_sentence(run):
    _, document = run
    reason = next(v for v in _ranked(document) if v["mmsi"] == FERRY_MMSI)["reason"]
    assert "origin region" in reason
    assert "AIS gap" not in reason and "slowed" not in reason


def test_filtered_vessels_explain_themselves_with_numbers(run):
    _, document = run
    filtered = [v for v in document["vessels"] if v.get("filtered")]
    assert len(filtered) == 4
    for vessel in filtered:
        assert vessel["reason"].startswith("Filtered out:")
        assert vessel["filter_reason"]
        assert "scores" not in vessel or vessel.get("scores") is None


def test_filtered_explanation_falls_back_cleanly():
    assert explain_filtered("outside time window", {}) == "Filtered out: outside time window."


# ---------------------------------------------------------------- contract ---------
def test_output_validates_against_the_contract(run):
    _, document = run
    validate_suspects(document)


def test_ranks_are_dense_ordered_and_only_on_unfiltered_vessels(run):
    _, document = run
    ranked = _ranked(document)
    assert [v["rank"] for v in ranked] == list(range(1, len(ranked) + 1))
    totals = [v["score_total"] for v in ranked]
    assert totals == sorted(totals, reverse=True)
    for vessel in document["vessels"]:
        if vessel.get("filtered"):
            assert "rank" not in vessel or vessel["rank"] is None


def test_document_carries_the_weights_and_the_origin_window(run):
    _, document = run
    assert set(document["weights"]) == set(FACTORS)
    assert sum(document["weights"].values()) == pytest.approx(1.0, abs=1e-3)
    assert document["origin_window"]["peak_utc"].endswith("Z")
    assert document["generated_utc"].endswith("Z")
    assert document["investigation_id"]


def test_schema_rejects_a_filtered_vessel_carrying_scores(run):
    _, document = run
    broken = json.loads(json.dumps(document))
    victim = next(v for v in broken["vessels"] if v.get("filtered"))
    victim["rank"] = 99
    with pytest.raises(Exception):
        validate_suspects(broken)


def test_schema_rejects_weights_that_do_not_sum_to_one(run):
    _, document = run
    broken = json.loads(json.dumps(document))
    broken["weights"]["proximity"] = 0.9
    with pytest.raises(Exception):
        validate_suspects(broken)


# ----------------------------------------------------- NO_VESSELS_IN_WINDOW --------
def test_empty_parquet_is_NO_VESSELS_IN_WINDOW(scenario, tmp_path):
    frame = pd.read_parquet(scenario["truth"]["vessels_path"]).iloc[0:0]
    empty = tmp_path / "empty.parquet"
    frame.to_parquet(empty, index=False)

    out = tmp_path / "suspects.json"
    status = attribute(scenario["cloud"], empty, out, weights_path=WEIGHTS)
    assert status["ok"] is False
    assert status["error"]["error_class"] == "NO_VESSELS_IN_WINDOW"
    assert not out.exists()


def test_traffic_far_from_the_window_is_NO_VESSELS_IN_WINDOW(scenario, tmp_path):
    """Nothing transmitting anywhere near the window: the declared error."""
    frame = pd.read_parquet(scenario["truth"]["vessels_path"])
    frame["timestamp"] = frame["timestamp"] + pd.Timedelta(days=400)
    shifted = tmp_path / "shifted.parquet"
    frame.to_parquet(shifted, index=False)

    status = attribute(
        scenario["cloud"], shifted, tmp_path / "s.json", weights_path=WEIGHTS
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "NO_VESSELS_IN_WINDOW"


def test_all_filtered_still_writes_a_file(scenario, tmp_path):
    """Vessels present but all excluded is a success, not an error.

    The UI needs to show "0 suspects, N filtered out" with a reason for each, which it
    cannot do from an error status alone.
    """
    strict = tmp_path / "strict.yaml"
    config = yaml.safe_load(Path(WEIGHTS).read_text())
    config["gates"] = {
        "spatial_buffer_km": 0.001,
        "temporal_buffer_min": 0.0,
        "max_axis_offset_deg": 0.01,
    }
    strict.write_text(yaml.safe_dump(config), encoding="utf-8")

    out = tmp_path / "suspects.json"
    status = attribute(
        scenario["cloud"], scenario["truth"]["vessels_path"], out, weights_path=strict
    )
    assert status["ok"] is True
    assert any("filtered out" in w for w in status["warnings"])

    document = json.loads(out.read_text(encoding="utf-8"))
    assert _ranked(document) == []
    assert all(v["filtered"] for v in document["vessels"])
    validate_suspects(document)


def test_missing_origin_cloud_is_MISSING_INPUT(scenario, tmp_path):
    status = attribute(
        tmp_path / "nope.geojson", scenario["truth"]["vessels_path"],
        tmp_path / "s.json", weights_path=WEIGHTS,
    )
    assert status["ok"] is False
    assert status["error"]["error_class"] == "MISSING_INPUT"


# --------------------------------------------------------------------- CLI ---------
def test_cli_runs_and_reports_its_output(scenario, tmp_path, capsys):
    out = tmp_path / "cli.json"
    code = cli_main([
        "--origin", str(scenario["cloud"]),
        "--vessels", str(scenario["truth"]["vessels_path"]),
        "--out", str(out), "--weights", WEIGHTS,
        "--slick", str(scenario["slick"]),
        "--investigation-id", "inv-042",
    ])
    assert code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["ok"] is True and "suspects" in status["outputs"]

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["investigation_id"] == "inv-042"
    validate_suspects(document)


def test_cli_reports_engine_errors_as_json(scenario, tmp_path, capsys):
    code = cli_main([
        "--origin", str(tmp_path / "nope.geojson"),
        "--vessels", str(scenario["truth"]["vessels_path"]),
        "--out", str(tmp_path / "x.json"), "--weights", WEIGHTS,
    ])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["error_class"] == "MISSING_INPUT"
