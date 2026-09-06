"""interpolate.py: the contract `interpolated` flag must mark exactly the
rows that were filled in rather than transmitted."""

import pandas as pd

from ais.interpolate import interpolate_trajectory


def _track(mmsi, minutes, lat0=28.5):
    return pd.DataFrame({
        "mmsi": mmsi,
        "timestamp_utc": [pd.Timestamp("2023-01-01T00:00:00Z")
                          + pd.Timedelta(minutes=m) for m in minutes],
        "lat": [lat0 + 0.01 * i for i in range(len(minutes))],
        "lon": [-90.0 + 0.01 * i for i in range(len(minutes))],
        "sog_kn": 10.0,
        "cog_deg": 45.0,
        "heading_deg": 44.0,
        "vessel_type": "tanker",
        "source": "real",
        "culprit": False,
    })


def test_transmitted_rows_flagged_false():
    # fixes every 5 minutes: nothing needs inventing
    out = interpolate_trajectory(_track(367000001, [0, 5, 10, 15]))
    assert "interpolated" in out.columns
    assert not out["interpolated"].any()


def test_filled_rows_flagged_true_even_for_short_gaps():
    # a 10-minute gap creates ONE empty 5-min bin; the old gap_flag (>15 min
    # only) would have missed it -- the contract flag must not
    out = interpolate_trajectory(_track(367000001, [0, 10]))
    out = out.sort_values("timestamp_utc").reset_index(drop=True)
    assert len(out) == 3
    assert list(out["interpolated"]) == [False, True, False]


def test_long_gap_rows_all_flagged_true():
    # 40-minute gap -> 7 invented bins between the two transmissions
    out = interpolate_trajectory(_track(367000001, [0, 40]))
    out = out.sort_values("timestamp_utc").reset_index(drop=True)
    assert len(out) == 9
    assert not out["interpolated"].iloc[0]
    assert not out["interpolated"].iloc[-1]
    assert out["interpolated"].iloc[1:-1].all()


def test_interpolated_positions_are_linear():
    out = interpolate_trajectory(_track(367000001, [0, 10]))
    out = out.sort_values("timestamp_utc").reset_index(drop=True)
    mid = out.iloc[1]
    assert abs(mid["lat"] - 28.505) < 1e-9
    assert abs(mid["lon"] - -89.995) < 1e-9


def test_gap_flag_column_is_superseded():
    df = _track(367000001, [0, 5, 10])
    df["gap_flag"] = False
    out = interpolate_trajectory(df)
    assert "gap_flag" not in out.columns
    assert "interpolated" in out.columns


def test_per_vessel_isolation():
    # two vessels, one with a gap: flags must not leak across MMSIs
    a = _track(367000001, [0, 5, 10])
    b = _track(219000111, [0, 20], lat0=29.2)
    out = interpolate_trajectory(pd.concat([a, b], ignore_index=True))
    ga = out[out["mmsi"] == 367000001]
    gb = out[out["mmsi"] == 219000111].sort_values("timestamp_utc")
    assert not ga["interpolated"].any()
    assert gb["interpolated"].iloc[1:-1].all()
    assert len(gb) == 5


def test_culprit_flag_preserved():
    df = _track(367000001, [0, 10])
    df["culprit"] = True
    out = interpolate_trajectory(df)
    assert out["culprit"].all()
