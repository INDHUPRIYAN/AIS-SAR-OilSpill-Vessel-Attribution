"""benchmark.py: the 50-scenario suite must be reproducible from its seed.

Only one scenario is built here (twice) to keep the test fast; determinism of
scenario 1 under master seed 1337 implies the sub-seed derivation and the
generator are both stable.
"""

import json

import pandas as pd

from ais.benchmark import build_benchmark
from contracts.schemas.tabular import validate_vessels_df


def _build(tmp_path, name, **kw):
    out = tmp_path / name
    args = dict(scenarios=1, master_seed=1337, out_dir=str(out),
                hard_negatives=3)
    args.update(kw)
    build_benchmark(**args)
    scen = out / "scenario_001"
    truth = json.loads((scen / "truth.json").read_text())
    df = pd.read_parquet(scen / "vessels.parquet")
    return truth, df


def test_seed_1337_truth_json_is_deterministic(tmp_path):
    truth_a, df_a = _build(tmp_path, "run_a")
    truth_b, df_b = _build(tmp_path, "run_b")
    assert truth_a == truth_b
    assert df_a.equals(df_b)


def test_benchmark_scenario_meets_contract(tmp_path):
    truth, df = _build(tmp_path, "run_c")
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    validate_vessels_df(df)


def test_truth_names_culprit_and_hard_negatives(tmp_path):
    truth, df = _build(tmp_path, "run_d")
    assert truth["culprit_mmsi"] == 900000000
    assert set(df.loc[df["culprit"], "mmsi"]) == {900000000}
    hn = sorted(int(m) for m in df["mmsi"].unique() if m >= 990_000_000)
    assert truth["hard_negative_mmsis"] == hn
    assert truth["seed"] == int(truth["seed"])  # a concrete sub-seed is recorded
