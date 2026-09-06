"""Projection of any internal AIS frame onto the frozen vessels.parquet contract.

The contract (contracts/schemas/tabular.py::VESSEL_COLUMNS) is 14 columns,
exactly, with exact names and dtypes:

    mmsi int64 (9-digit), timestamp_utc (tz-aware UTC), lat, lon,
    sog_kn [0,60], cog_deg [0,360], heading_deg (511 -> NaN),
    vessel_type in {tanker,cargo,bulk,fishing,passenger,tug,other},
    length_m, width_m, draught_m, source in {real,synthetic},
    interpolated bool, culprit bool

sorted by (mmsi, timestamp_utc). Every output path of this service funnels
through :func:`to_contract` so the service meets its own contract at source
instead of relying on downstream renaming.

Legacy names this module absorbs (old artefacts / internal names):
``timestamp`` -> ``timestamp_utc``, ``draft_m`` -> ``draught_m``,
``gap_flag`` -> ``interpolated``, Title-case vessel types -> lowercase.
"""

import numpy as np
import pandas as pd

# The frozen column set, in contract order.
CONTRACT_COLUMNS = [
    "mmsi", "timestamp_utc", "lat", "lon", "sog_kn", "cog_deg", "heading_deg",
    "vessel_type", "length_m", "width_m", "draught_m", "source",
    "interpolated", "culprit",
]

VESSEL_TYPES = {"tanker", "cargo", "bulk", "fishing", "passenger", "tug", "other"}

# Legacy / internal spellings the service used before contract compliance.
LEGACY_RENAMES = {
    "timestamp": "timestamp_utc",
    "draft_m": "draught_m",
    "gap_flag": "interpolated",
}

# String type names (DMA 'Ship type', generator Title-case) -> contract value.
_TYPE_STRINGS = {
    "tanker": "tanker",
    "cargo": "cargo",
    "bulk": "bulk",
    "bulk carrier": "bulk",
    "bulkcarrier": "bulk",
    "fishing": "fishing",
    "passenger": "passenger",
    "tug": "tug",
    "towing": "tug",
    "tug boat": "tug",
    "port tender": "tug",
}

MMSI_MIN, MMSI_MAX = 100_000_000, 999_999_999
AIS_HEADING_UNAVAILABLE = 511
AIS_SOG_UNAVAILABLE = 102.2   # 1023 in 0.1-kn units means "not available"


def _mc_code_to_type(code: float) -> str:
    """MarineCadastre numeric VesselType (AIS ship-type code) -> contract value."""
    if not np.isfinite(code):
        return "other"
    c = int(code)
    if c == 30:
        return "fishing"
    if c in (31, 32, 52):
        return "tug"
    if 60 <= c <= 69:
        return "passenger"
    if 70 <= c <= 79:
        return "cargo"
    if 80 <= c <= 89:
        return "tanker"
    return "other"


def normalize_vessel_type(value) -> str:
    """Map any raw vessel-type representation to the contract's lowercase set."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "other"
    if isinstance(value, (int, np.integer, float, np.floating)):
        return _mc_code_to_type(float(value))
    s = str(value).strip().lower()
    if not s:
        return "other"
    # numeric strings ("70", "80.0") are AIS codes
    try:
        return _mc_code_to_type(float(s))
    except ValueError:
        pass
    if s in VESSEL_TYPES:
        return s
    if s in _TYPE_STRINGS:
        return _TYPE_STRINGS[s]
    # DMA writes variants like "Cargo - Hazard A" -> first word wins
    head = s.split()[0].split("-")[0].strip()
    return _TYPE_STRINGS.get(head, head if head in VESSEL_TYPES else "other")


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """Column as a numeric-coercible Series; all-NaN if the column is absent."""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def to_contract(df: pd.DataFrame, source: str = None) -> pd.DataFrame:
    """Project a frame onto the 14 frozen contract columns.

    Tolerates legacy column names, fills missing optional columns, coerces
    dtypes, maps vessel types, drops everything else, and sorts by
    (mmsi, timestamp_utc). Rows that cannot be made contract-legal
    (bad MMSI, missing position/time) are dropped.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame({c: pd.Series(dtype=d) for c, d in _EMPTY_DTYPES.items()})

    df = df.copy()
    for old, new in LEGACY_RENAMES.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    # -- required identity / position / time ------------------------------
    df["mmsi"] = _col(df, "mmsi")
    if "timestamp_utc" not in df.columns:
        raise KeyError("to_contract: no timestamp_utc (or legacy timestamp) column")
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True,
                                         errors="coerce")
    for c in ("lat", "lon"):
        df[c] = _col(df, c)
    df = df[df["mmsi"].between(MMSI_MIN, MMSI_MAX)
            & df["timestamp_utc"].notna()
            & df["lat"].between(-90, 90)
            & df["lon"].between(-180, 180)]

    # -- kinematics --------------------------------------------------------
    sog = _col(df, "sog_kn")
    sog = sog.mask(sog >= AIS_SOG_UNAVAILABLE)          # "not available" code
    sog = sog.groupby(df["mmsi"]).ffill()               # carry last known speed
    df["sog_kn"] = sog.fillna(0.0).clip(0.0, 60.0)

    cog = _col(df, "cog_deg")
    cog = cog.where(~np.isfinite(cog) | ((cog >= 0) & (cog <= 360)), cog % 360.0)
    cog = cog.groupby(df["mmsi"]).ffill()
    df["cog_deg"] = cog.fillna(0.0)

    hdg = _col(df, "heading_deg")
    hdg = hdg.mask((hdg == AIS_HEADING_UNAVAILABLE) | (hdg < 0) | (hdg > 360))
    df["heading_deg"] = hdg

    # -- static data -------------------------------------------------------
    vt = df["vessel_type"] if "vessel_type" in df.columns \
        else pd.Series(None, index=df.index, dtype="object")
    df["vessel_type"] = vt.map(normalize_vessel_type)
    for c in ("length_m", "width_m", "draught_m"):
        df[c] = _col(df, c)

    # -- provenance flags --------------------------------------------------
    if source is not None:
        df["source"] = source
    elif "source" not in df.columns:
        df["source"] = "synthetic"
    df["source"] = df["source"].where(df["source"].isin(["real", "synthetic"]),
                                      "real")
    if "interpolated" not in df.columns:
        df["interpolated"] = False
    if "culprit" not in df.columns:
        df["culprit"] = False
    df["interpolated"] = df["interpolated"].fillna(False).astype(bool)
    df["culprit"] = df["culprit"].fillna(False).astype(bool)
    # ground truth exists only in synthetic data
    df.loc[df["source"] == "real", "culprit"] = False

    # -- exact columns, exact dtypes, contract ordering --------------------
    df = df[CONTRACT_COLUMNS]
    df = df.astype({
        "mmsi": "int64", "lat": "float64", "lon": "float64",
        "sog_kn": "float64", "cog_deg": "float64", "heading_deg": "float64",
        "length_m": "float64", "width_m": "float64", "draught_m": "float64",
        "interpolated": "bool", "culprit": "bool",
    })
    if df["timestamp_utc"].dt.tz is None:                 # pragma: no cover
        df["timestamp_utc"] = df["timestamp_utc"].dt.tz_localize("UTC")
    df = df.sort_values(["mmsi", "timestamp_utc"], kind="mergesort")
    return df.reset_index(drop=True)


_EMPTY_DTYPES = {
    "mmsi": "int64", "timestamp_utc": "datetime64[ns, UTC]",
    "lat": "float64", "lon": "float64", "sog_kn": "float64",
    "cog_deg": "float64", "heading_deg": "float64", "vessel_type": "object",
    "length_m": "float64", "width_m": "float64", "draught_m": "float64",
    "source": "object", "interpolated": "bool", "culprit": "bool",
}
