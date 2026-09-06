"""clean.py hardening: MMSI range, heading 511, SOG/COG ranges, dedupe, jumps."""

import numpy as np
import pandas as pd

from ais.clean import clean_ais_data


def _frame(rows):
    df = pd.DataFrame(rows)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    return df


def _base_row(**kw):
    row = {"mmsi": 367000001, "timestamp_utc": "2023-01-01T00:00:00Z",
           "lat": 28.5, "lon": -90.0, "sog_kn": 10.0, "cog_deg": 45.0,
           "heading_deg": 44.0}
    row.update(kw)
    return row


def test_bad_mmsi_rows_dropped_and_counted():
    stats = {}
    df = _frame([
        _base_row(),
        _base_row(mmsi=1234, timestamp_utc="2023-01-01T00:05:00Z"),        # too short
        _base_row(mmsi=99999999, timestamp_utc="2023-01-01T00:10:00Z"),    # 8 digits
        _base_row(mmsi=1000000000, timestamp_utc="2023-01-01T00:15:00Z"),  # 10 digits
    ])
    out = clean_ais_data(df, stats=stats)
    assert set(out["mmsi"]) == {367000001}
    assert stats["dropped_bad_mmsi"] == 3


def test_heading_511_becomes_nan():
    df = _frame([_base_row(heading_deg=511),
                 _base_row(timestamp_utc="2023-01-01T00:05:00Z",
                           lat=28.51, heading_deg=90.0)])
    out = clean_ais_data(df)
    assert np.isnan(out["heading_deg"].iloc[0])
    assert out["heading_deg"].iloc[1] == 90.0


def test_heading_out_of_range_becomes_nan():
    df = _frame([_base_row(heading_deg=-5.0),
                 _base_row(timestamp_utc="2023-01-01T00:05:00Z",
                           lat=28.51, heading_deg=400.0)])
    out = clean_ais_data(df)
    assert out["heading_deg"].isna().all()


def test_sog_negative_clamped_to_zero():
    df = _frame([_base_row(sog_kn=-0.3)])
    out = clean_ais_data(df)
    assert out["sog_kn"].iloc[0] == 0.0


def test_sog_unavailable_code_becomes_nan():
    df = _frame([_base_row(sog_kn=102.3)])
    out = clean_ais_data(df)
    assert np.isnan(out["sog_kn"].iloc[0])


def test_sog_impossible_row_dropped():
    stats = {}
    df = _frame([_base_row(),
                 _base_row(timestamp_utc="2023-01-01T00:05:00Z",
                           lat=28.51, sog_kn=85.0)])
    out = clean_ais_data(df, stats=stats)
    assert len(out) == 1
    assert stats["dropped_impossible_sog"] == 1


def test_cog_out_of_range_wrapped():
    df = _frame([_base_row(cog_deg=370.0),
                 _base_row(timestamp_utc="2023-01-01T00:05:00Z",
                           lat=28.51, cog_deg=-10.0)])
    out = clean_ais_data(df)
    assert np.isclose(out["cog_deg"].iloc[0], 10.0)
    assert np.isclose(out["cog_deg"].iloc[1], 350.0)


def test_duplicates_removed_and_sorted():
    df = _frame([
        _base_row(timestamp_utc="2023-01-01T00:10:00Z", lat=28.52),
        _base_row(),
        _base_row(),                                       # exact duplicate key
        _base_row(mmsi=219000111, timestamp_utc="2023-01-01T00:00:00Z", lat=29.0),
    ])
    out = clean_ais_data(df)
    assert len(out) == 3
    assert out.sort_values(["mmsi", "timestamp_utc"]).equals(out)


def test_impossible_jump_rejected():
    stats = {}
    df = _frame([
        _base_row(),
        _base_row(timestamp_utc="2023-01-01T00:05:00Z", lat=28.51, lon=-90.0),
        _base_row(timestamp_utc="2023-01-01T00:10:00Z", lat=26.0, lon=-85.0),  # teleport
    ])
    out = clean_ais_data(df, stats=stats)
    assert len(out) == 2
    assert stats["dropped_impossible_jumps"] == 1


def test_bbox_filter_still_applies():
    df = _frame([_base_row(),
                 _base_row(timestamp_utc="2023-01-01T00:05:00Z", lat=45.0)])
    out = clean_ais_data(df, bbox=[-91.0, 28.0, -89.0, 30.0])
    assert len(out) == 1


def test_empty_frame_is_returned_unchanged():
    df = pd.DataFrame()
    out = clean_ais_data(df)
    assert out.empty
