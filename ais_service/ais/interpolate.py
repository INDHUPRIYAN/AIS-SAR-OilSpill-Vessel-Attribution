"""Resample each vessel's track to a regular grid and flag what was invented.

Contract semantics (contracts/schemas/tabular.py): ``interpolated`` is True on
every row that was filled in rather than transmitted. A resampled bin that
contains at least one real fix counts as transmitted (False); a bin whose
position was linearly interpolated or forward-filled counts as invented
(True). This replaces the old ``gap_flag`` column, which only marked rows
inside >15-minute gaps and therefore under-reported invention.
"""

import numpy as np
import pandas as pd

RESAMPLE_FREQ = "5min"

# Static columns carried through a resample unchanged (first ffill, then bfill
# for bins before the first fix of the vessel).
_STATIC_STRING_COLS = ["vessel_name", "vessel_type", "status", "source"]
_FFILL_NUMERIC_COLS = ["sog_kn", "cog_deg", "heading_deg", "imo",
                       "length_m", "width_m", "draft_m", "draught_m"]


def interpolate_trajectory(df, freq=RESAMPLE_FREQ):
    """Interpolate positions over temporal gaps onto a regular `freq` grid.

    Emits an ``interpolated: bool`` column per the vessels.parquet contract:
    True for every synthesized/resampled row that was not an original
    transmission, False for rows backed by at least one real fix.
    """
    if df.empty:
        return df

    df = df.copy()
    tcol = "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"
    df[tcol] = pd.to_datetime(df[tcol], utc=True)
    if "gap_flag" in df.columns:      # superseded by `interpolated`
        df = df.drop(columns=["gap_flag"])

    out_frames = []
    for mmsi, group in df.groupby("mmsi"):
        group = group.sort_values(tcol).set_index(tcol)

        # Which resample bins contain at least one real transmission?
        fix_count = group["lat"].resample(freq).count()

        numeric = group.select_dtypes(include=[np.number])
        resampled = numeric.resample(freq).mean()

        # Interpolate coordinates across empty bins
        resampled["lat"] = resampled["lat"].interpolate(method="linear")
        resampled["lon"] = resampled["lon"].interpolate(method="linear")

        # Forward fill other numeric columns
        for col in _FFILL_NUMERIC_COLS:
            if col in resampled.columns:
                resampled[col] = resampled[col].ffill().bfill()

        # Forward fill categorical/string columns using the original group
        for col in _STATIC_STRING_COLS:
            if col in group.columns:
                res = group[col].reindex(resampled.index)
                resampled[col] = res.ffill().bfill()

        # Booleans: culprit is a per-vessel constant
        if "culprit" in group.columns:
            resampled["culprit"] = bool(group["culprit"].iloc[0]) \
                if not group.empty else False

        resampled["mmsi"] = mmsi
        # THE contract flag: a bin with zero real fixes was filled in, not
        # transmitted. That covers small resample fills and long AIS gaps alike.
        resampled["interpolated"] = fix_count.reindex(resampled.index) \
            .fillna(0).eq(0).to_numpy()

        out_frames.append(resampled.reset_index().rename(columns={"index": tcol}))

    if not out_frames:
        return df

    final_df = pd.concat(out_frames, ignore_index=True)

    # Cast core dtypes; the contract projection (contract.to_contract) does the
    # final exact-dtype pass, this just keeps the frame sane for callers.
    final_df["mmsi"] = final_df["mmsi"].astype("int64")
    final_df["lat"] = final_df["lat"].astype("float64")
    final_df["lon"] = final_df["lon"].astype("float64")
    final_df["interpolated"] = final_df["interpolated"].astype(bool)
    if "culprit" in final_df.columns:
        final_df["culprit"] = final_df["culprit"].fillna(False).astype(bool)
    return final_df
