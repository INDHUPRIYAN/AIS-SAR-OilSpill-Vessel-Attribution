"""AIS cleaning: dedupe, ordering, bounds, field validation, jump rejection.

Everything here is defensive plumbing for real archives (MarineCadastre / DMA
bulk CSVs), which contain test MMSIs, sentinel values (heading 511, SOG 102.3)
and GPS glitches. Counts of everything dropped or repaired are recorded in
``df.attrs["clean_stats"]`` (and in the optional ``stats`` dict argument) so a
silent 90 % data loss can never hide.
"""

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

AIS_HEADING_UNAVAILABLE = 511
AIS_SOG_UNAVAILABLE = 102.2      # >= this means "not available" in raw AIS
MMSI_MIN, MMSI_MAX = 100_000_000, 999_999_999
MAX_PLAUSIBLE_SOG_KN = 60.0


def haversine_distance(lat1, lon1, lat2, lon2):
    # Radius of earth in km
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c


def _tcol(df):
    return "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"


def clean_ais_data(df, bbox=None, stats=None):
    """Deduplicate, order by (mmsi, time), and drop physically impossible rows.

    Hardening applied per row:
      * MMSI outside the 9-digit range [100000000, 999999999] -> row dropped
      * heading 511 (AIS "unavailable") or outside [0, 360]   -> NaN
      * SOG negative -> clamped to 0; SOG "unavailable" code   -> NaN;
        SOG in (60, unavailable) -> row dropped (impossible speed)
      * COG finite but outside [0, 360]                        -> wrapped mod 360
      * duplicate (mmsi, time) fixes                           -> dropped
      * positions off the planet / outside bbox                -> dropped
      * implied jump speed > 60 kn between fixes               -> row dropped
    """
    s = stats if stats is not None else {}
    s["rows_in"] = int(len(df))
    if df.empty:
        s["rows_out"] = 0
        return df

    df = df.copy()
    tcol = _tcol(df)

    # MMSI must be a 9-digit number; anything else is a base station, test
    # transmitter or corruption and can never match a real vessel.
    mmsi = pd.to_numeric(df["mmsi"], errors="coerce")
    ok_mmsi = mmsi.between(MMSI_MIN, MMSI_MAX)
    s["dropped_bad_mmsi"] = int((~ok_mmsi).sum())
    df = df[ok_mmsi.to_numpy()]
    df["mmsi"] = mmsi[ok_mmsi].astype("int64")

    # Deduplicate by mmsi and timestamp
    before = len(df)
    df = df.drop_duplicates(subset=["mmsi", tcol])
    s["dropped_duplicates"] = int(before - len(df))

    # Sort
    df = df.sort_values(["mmsi", tcol])

    # Valid coordinates
    valid_coords = (df["lat"] >= -90) & (df["lat"] <= 90) & \
                   (df["lon"] >= -180) & (df["lon"] <= 180)
    s["dropped_bad_coords"] = int((~valid_coords).sum())
    df = df[valid_coords]

    if bbox:
        lon_min, lat_min, lon_max, lat_max = bbox
        in_bbox = (df["lon"] >= lon_min) & (df["lon"] <= lon_max) & \
                  (df["lat"] >= lat_min) & (df["lat"] <= lat_max)
        s["dropped_outside_bbox"] = int((~in_bbox).sum())
        df = df[in_bbox]

    # Heading: 511 is the AIS "not available" sentinel, not a bearing.
    if "heading_deg" in df.columns:
        hdg = pd.to_numeric(df["heading_deg"], errors="coerce")
        bad_hdg = hdg.notna() & ((hdg == AIS_HEADING_UNAVAILABLE)
                                 | (hdg < 0) | (hdg > 360))
        s["heading_set_nan"] = int(bad_hdg.sum())
        df["heading_deg"] = hdg.mask(bad_hdg)

    # SOG: clamp small negatives, NaN the "unavailable" code, drop impossible.
    if "sog_kn" in df.columns:
        sog = pd.to_numeric(df["sog_kn"], errors="coerce")
        s["sog_clamped_negative"] = int((sog < 0).sum())
        sog = sog.clip(lower=0.0)
        unavailable = sog >= AIS_SOG_UNAVAILABLE
        s["sog_set_nan_unavailable"] = int(unavailable.sum())
        sog = sog.mask(unavailable)
        impossible = sog.notna() & (sog > MAX_PLAUSIBLE_SOG_KN)
        s["dropped_impossible_sog"] = int(impossible.sum())
        df["sog_kn"] = sog
        df = df[~impossible.to_numpy()]

    # COG: wrap finite out-of-range values into [0, 360).
    if "cog_deg" in df.columns:
        cog = pd.to_numeric(df["cog_deg"], errors="coerce")
        out_of_range = cog.notna() & ((cog < 0) | (cog > 360))
        s["cog_wrapped"] = int(out_of_range.sum())
        df["cog_deg"] = cog.where(~out_of_range, cog % 360.0)

    # Calculate implied speed (knots)
    # 1 km = 0.539957 nautical miles
    df["prev_lat"] = df.groupby("mmsi")["lat"].shift(1)
    df["prev_lon"] = df.groupby("mmsi")["lon"].shift(1)
    df["prev_time"] = df.groupby("mmsi")[tcol].shift(1)

    dist_km = haversine_distance(df["prev_lat"], df["prev_lon"], df["lat"], df["lon"])
    dist_nm = dist_km * 0.539957

    time_diff_hours = (df[tcol] - df["prev_time"]).dt.total_seconds() / 3600.0

    implied_speed_knots = np.where(time_diff_hours > 0, dist_nm / time_diff_hours, 0)

    # Mask out impossible jumps (> 60 knots)
    # The first point of each vessel has NaN prev_lat, so implied speed is NaN, we keep it.
    valid_jumps = (np.isnan(implied_speed_knots)) | \
                  (implied_speed_knots <= MAX_PLAUSIBLE_SOG_KN)
    s["dropped_impossible_jumps"] = int((~valid_jumps).sum())
    df = df[valid_jumps]

    df = df.drop(columns=["prev_lat", "prev_lon", "prev_time"])

    s["rows_out"] = int(len(df))
    dropped = s["rows_in"] - s["rows_out"]
    if dropped:
        log.info("clean_ais_data: %d/%d rows dropped (%s)", dropped,
                 s["rows_in"], {k: v for k, v in s.items() if v})
    df.attrs["clean_stats"] = dict(s)
    return df
