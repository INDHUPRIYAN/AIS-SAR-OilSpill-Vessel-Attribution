"""The Phase 7 benchmark harness: reproducibility, and honesty about its own numbers.

These tests do not assert a hit rate - that would just pin whatever the engine happens
to score today. They check that the harness measures what it claims to: that scenarios
regenerate identically from their seed, that the difficulty tiers really are harder, and
that a miss is recorded as a miss rather than quietly dropped.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from benchmark.run import evaluate, prepare_origin, summarise
from benchmark.scenarios import TIER_MIX, TIERS, build_scenario


@pytest.fixture(scope="module")
def origin(tmp_path_factory) -> dict:
    return prepare_origin(tmp_path_factory.mktemp("bench_origin"))


@pytest.fixture(scope="module")
def scenarios(origin, tmp_path_factory) -> list:
    out = tmp_path_factory.mktemp("bench_scenarios")
    return [
        build_scenario(
            i, out,
            origin_lon=origin["origin_lon"], origin_lat=origin["origin_lat"],
            window_start=origin["window_start"], window_end=origin["window_end"],
            slick_axis_deg=origin["slick_axis_deg"],
        )
        for i in range(6)
    ]


# ------------------------------------------------------------ the scenario set -----
def test_tier_mix_matches_the_documented_proportions():
    counts = Counter(TIER_MIX)
    assert len(TIER_MIX) == 50
    assert counts == {"easy": 20, "medium": 17, "hard": 13}
    assert set(counts) == set(TIERS)


def test_a_short_run_still_samples_every_tier():
    """A blocked list would make `--count 6` report a flattering 100%."""
    assert len(set(TIER_MIX[:8])) == 3


def test_scenarios_regenerate_identically_from_their_seed(origin, tmp_path):
    """Handbook: the benchmark set must regenerate reproducibly from its seed."""
    kwargs = dict(
        origin_lon=origin["origin_lon"], origin_lat=origin["origin_lat"],
        window_start=origin["window_start"], window_end=origin["window_end"],
        slick_axis_deg=origin["slick_axis_deg"],
    )
    first = build_scenario(3, tmp_path / "a", **kwargs)
    second = build_scenario(3, tmp_path / "b", **kwargs)

    assert first.as_dict()["culprit_mmsi"] == second.as_dict()["culprit_mmsi"]
    frame_a = pd.read_parquet(first.vessels_path)
    frame_b = pd.read_parquet(second.vessels_path)
    pd.testing.assert_frame_equal(frame_a, frame_b)


def test_every_scenario_has_exactly_one_culprit(scenarios):
    for scenario in scenarios:
        frame = pd.read_parquet(scenario.vessels_path)
        culprits = frame[frame["culprit"]]["mmsi"].unique()
        assert list(culprits) == [scenario.culprit_mmsi]


def test_harder_tiers_give_the_culprit_less_to_go_on(scenarios, origin, tmp_path):
    """The tiers must actually differ, or the breakdown means nothing."""
    kwargs = dict(
        origin_lon=origin["origin_lon"], origin_lat=origin["origin_lat"],
        window_start=origin["window_start"], window_end=origin["window_end"],
        slick_axis_deg=origin["slick_axis_deg"],
    )
    built = [build_scenario(i, tmp_path / "tiers", **kwargs) for i in range(50)]
    by_tier = {tier: [s for s in built if s.tier == tier] for tier in TIERS}

    # easy culprits always leave behavioural evidence; hard ones never do.
    assert all(s.culprit_gap and s.culprit_slowdown for s in by_tier["easy"])
    assert all(not s.culprit_gap and not s.culprit_slowdown for s in by_tier["hard"])
    assert all(s.culprit_gap != s.culprit_slowdown for s in by_tier["medium"])

    # and hard scenarios carry more competing traffic.
    easy_fleet = sum(s.n_vessels for s in by_tier["easy"]) / len(by_tier["easy"])
    hard_fleet = sum(s.n_vessels for s in by_tier["hard"]) / len(by_tier["hard"])
    assert hard_fleet > easy_fleet


# ---------------------------------------------------------------- evaluation -------
def test_evaluate_locates_the_culprit_in_the_ranking(scenarios, origin, tmp_path):
    record = evaluate(scenarios[0], origin, tmp_path)
    assert record["ok"] is True
    assert record["outcome"] in {"ranked", "culprit_filtered"}
    if record["outcome"] == "ranked":
        assert record["rank"] >= 1
        assert 0.0 <= record["score_total"] <= 1.0


def test_summary_counts_misses_as_misses():
    """A culprit the gates threw away must not silently vanish from the denominator."""
    records = [
        {"tier": "easy", "rank": 1, "outcome": "ranked",
         "culprit_gap": True, "culprit_slowdown": True},
        {"tier": "hard", "rank": 4, "outcome": "ranked",
         "culprit_gap": False, "culprit_slowdown": False},
        {"tier": "hard", "rank": None, "outcome": "culprit_filtered",
         "culprit_gap": False, "culprit_slowdown": False},
        {"tier": "medium", "rank": None, "outcome": "engine_error",
         "culprit_gap": True, "culprit_slowdown": False},
    ]
    summary = summarise(records)

    assert summary["scenarios"] == 4
    assert summary["top1"] == 1 and summary["top1_rate"] == 0.25
    assert summary["top3"] == 1 and summary["top3_rate"] == 0.25
    assert summary["culprit_filtered_by_gates"] == 1
    assert summary["engine_errors"] == 1
    assert summary["mean_rank_when_placed"] == pytest.approx(2.5)
    assert summary["by_tier"]["hard"]["scenarios"] == 2


def test_summary_reports_behaviour_breakdown():
    records = [
        {"tier": "easy", "rank": 1, "outcome": "ranked",
         "culprit_gap": True, "culprit_slowdown": True},
        {"tier": "hard", "rank": 3, "outcome": "ranked",
         "culprit_gap": False, "culprit_slowdown": False},
    ]
    summary = summarise(records)
    assert summary["by_behaviour"]["with_ais_gap"]["top1_rate"] == 1.0
    assert summary["by_behaviour"]["no_behavioural_evidence"]["top1_rate"] == 0.0


def test_committed_results_are_present_and_self_consistent():
    """The number on the metrics slide has to come from a file anyone can re-derive."""
    results = Path("benchmark/results.json")
    assert results.is_file(), "run `python -m benchmark.run` and commit the results"

    payload = json.loads(results.read_text(encoding="utf-8"))
    summary, records = payload["summary"], payload["scenarios"]
    assert summary["scenarios"] == len(records)
    assert summary["top1"] == sum(1 for r in records if r["rank"] == 1)
    assert summary["top3"] == sum(
        1 for r in records if r["rank"] is not None and r["rank"] <= 3
    )
    assert summary["top1_rate"] <= summary["top3_rate"]
    assert Path("benchmark/RESULTS.md").is_file()
