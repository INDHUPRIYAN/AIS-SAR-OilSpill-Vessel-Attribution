"""generator.py: planted culprit, hard negatives, determinism."""

import numpy as np
import pandas as pd

from ais.generator import generate_synthetic_ais

from _testdata import CULPRIT_CONFIG, GEN_BBOX, GEN_END, GEN_START

O_LAT = CULPRIT_CONFIG["origin"]["lat"]
O_LON = CULPRIT_CONFIG["origin"]["lon"]
W_START = pd.Timestamp(CULPRIT_CONFIG["origin"]["window_start_utc"])
W_END = pd.Timestamp(CULPRIT_CONFIG["origin"]["window_end_utc"])


def _generate(**kw):
    args = dict(bbox=GEN_BBOX, start_time=GEN_START, end_time=GEN_END,
                n_vessels=15, culprit_config=CULPRIT_CONFIG, seed=11)
    args.update(kw)
    return generate_synthetic_ais(**args)


def _dist_deg(g):
    return np.hypot(g["lat"] - O_LAT,
                    (g["lon"] - O_LON) * np.cos(np.radians(O_LAT)))


def test_culprit_passes_through_origin_inside_window():
    df = _generate()
    culprit = df[df["culprit"]]
    assert culprit["mmsi"].nunique() == 1
    in_window = culprit[(culprit["timestamp_utc"] >= W_START)
                        & (culprit["timestamp_utc"] <= W_END)]
    assert not in_window.empty
    assert _dist_deg(in_window).min() < 0.05


def test_hard_negatives_generated_with_configured_count():
    df = _generate(n_hard_negatives=3)
    hn = df[df["mmsi"] >= 990_000_000]
    assert hn["mmsi"].nunique() == 3


def test_hard_negatives_are_never_culprits():
    df = _generate(n_hard_negatives=3)
    hn = df[df["mmsi"] >= 990_000_000]
    assert not hn["culprit"].any()
    # and there is still exactly one culprit vessel
    assert df.loc[df["culprit"], "mmsi"].nunique() == 1


def test_hard_negative_crosses_origin_but_outside_window():
    df = _generate(n_hard_negatives=3)
    g = df[df["mmsi"] == 990_000_000]
    assert _dist_deg(g).min() < 0.05                     # right place...
    in_window = g[(g["timestamp_utc"] >= W_START) & (g["timestamp_utc"] <= W_END)]
    if not in_window.empty:                              # ...wrong time
        assert _dist_deg(in_window).min() > 0.15


def test_hard_negative_in_window_never_enters_cloud():
    df = _generate(n_hard_negatives=3)
    g = df[df["mmsi"] == 990_000_001]
    in_window = g[(g["timestamp_utc"] >= W_START) & (g["timestamp_utc"] <= W_END)]
    assert not in_window.empty                           # right time...
    assert _dist_deg(g).min() > 0.15                     # ...never the place


def test_hard_negative_has_gap_far_from_slick():
    df = _generate(n_hard_negatives=3)
    g = df[df["mmsi"] == 990_000_002].sort_values("timestamp_utc")
    assert _dist_deg(g).min() > 0.3                      # nowhere near the slick
    max_gap_min = g["timestamp_utc"].diff().dt.total_seconds().max() / 60
    assert max_gap_min >= 30                             # but suspiciously silent


def test_hard_negatives_do_not_perturb_main_fleet():
    with_hn = _generate(n_hard_negatives=3)
    without = _generate(n_hard_negatives=0)
    main = with_hn[with_hn["mmsi"] < 990_000_000].reset_index(drop=True)
    assert without.equals(main)


def test_same_seed_same_output():
    a = _generate(n_hard_negatives=3)
    b = _generate(n_hard_negatives=3)
    assert a.equals(b)


def test_different_seed_different_tracks():
    a = _generate(seed=11)
    b = _generate(seed=12)
    assert not a.equals(b)


def test_culprit_ais_gap_removes_fixes():
    df = _generate()
    culprit = df[df["culprit"]].sort_values("timestamp_utc")
    max_gap_min = culprit["timestamp_utc"].diff().dt.total_seconds().max() / 60
    assert max_gap_min >= 40                             # 47-min dark period


def test_fleet_seed_changes_identity_but_stays_valid():
    from contracts.schemas.tabular import validate_vessels_df
    df = _generate(fleet_seed=123, n_hard_negatives=3)
    validate_vessels_df(df)
    assert 900000000 not in set(df["mmsi"])              # realistic MMSIs instead
