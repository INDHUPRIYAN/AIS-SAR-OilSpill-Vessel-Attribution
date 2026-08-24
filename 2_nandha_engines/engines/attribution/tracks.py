"""Read ``vessels.parquet`` and assemble per-MMSI tracks.

Handbook pitfall #7: "the parquet columns are fixed by contract - code against them, not
against a sample file's accidents". So the contract column set is declared here, and a
file missing one of the four load-bearing columns fails with MISSING_INPUT naming
exactly what is absent.

Timestamps get one deliberate concession. Engine A *rejects* a naive timestamp, because
a hand-written scene_meta.json genuinely might be local IST. A parquet column typed as
UTC by contract is a different risk: pandas and pyarrow routinely round-trip tz-aware
columns to naive, so rejecting would break on real files for no safety gain. Naive
values are therefore assumed UTC with a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

from ..common.errors import missing_input
from ..common.geo import LocalFrame, bearing_deg

# Without these four there is no track to speak of.
REQUIRED_COLUMNS = ("mmsi", "timestamp", "lat", "lon")

# The rest of the frozen contract set. Missing ones degrade with a warning rather than
# failing the run - a real AIS archive may genuinely lack IMO or dimensions.
CONTRACT_COLUMNS = (
    "sog_kn", "cog_deg", "heading_deg", "vessel_name", "imo", "vessel_type",
    "length_m", "width_m", "draft_m", "status", "source", "culprit",
)

_COLUMN_DEFAULTS: dict[str, Any] = {
    "sog_kn": np.nan, "cog_deg": np.nan, "heading_deg": np.nan,
    "vessel_name": None, "imo": None, "vessel_type": None,
    "length_m": np.nan, "width_m": np.nan, "draft_m": np.nan,
    "status": None, "source": "unknown", "culprit": False,
}


@dataclass
class VesselTrack:
    """One vessel's ordered fixes."""

    mmsi: int
    name: str | None
    vessel_type: str | None
    imo: str | None
    length_m: float
    width_m: float
    draft_m: float
    source: str
    culprit: bool
    times_s: np.ndarray
    lons: np.ndarray
    lats: np.ndarray
    sog_kn: np.ndarray
    cog_deg: np.ndarray

    @property
    def n_fixes(self) -> int:
        return self.times_s.size

    @property
    def start_s(self) -> float:
        return float(self.times_s[0])

    @property
    def end_s(self) -> float:
        return float(self.times_s[-1])

    def line(self) -> LineString | Point:
        """Track geometry in lon/lat; a single fix degrades to a Point."""
        if self.n_fixes < 2:
            return Point(self.lons[0], self.lats[0])
        return LineString(np.column_stack([self.lons, self.lats]))

    def course_at(self, index: int) -> float | None:
        """Course over ground at one fix, in compass degrees.

        Prefers the reported COG; falls back to the bearing between the neighbouring
        fixes, which is what a real archive with a blank COG column forces.
        """
        reported = self.cog_deg[index] if index < self.cog_deg.size else np.nan
        if np.isfinite(reported):
            speed = self.sog_kn[index] if index < self.sog_kn.size else np.nan
            # A moored vessel reports COG 0; that is not a course.
            if not (reported == 0.0 and np.isfinite(speed) and speed < 0.5):
                return float(reported) % 360.0

        if self.n_fixes < 2:
            return None
        lo = max(index - 1, 0)
        hi = min(index + 1, self.n_fixes - 1)
        if lo == hi:
            return None
        frame = LocalFrame(float(self.lats[index]), float(self.lons[index]))
        x, y = frame.to_metres(self.lons[[lo, hi]], self.lats[[lo, hi]])
        return bearing_deg(float(x[1] - x[0]), float(y[1] - y[0]))

    def gaps_s(self) -> np.ndarray:
        """Interval between consecutive fixes, in seconds."""
        return np.diff(self.times_s) if self.n_fixes > 1 else np.zeros(0)


def _read_frame(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        raise missing_input(f"vessels.parquet not found: {p}", path=str(p))
    try:
        return pd.read_parquet(p)
    except Exception as exc:                      # noqa: BLE001 - any reader failure
        raise missing_input(f"{p.name} could not be read as parquet: {exc}", path=str(p))


def _normalise_timestamps(
    frame: pd.DataFrame, warnings: list[str]
) -> pd.DataFrame:
    times = pd.to_datetime(frame["timestamp"], errors="coerce", utc=False)
    if getattr(times.dtype, "tz", None) is None:
        warnings.append(
            "the 'timestamp' column carries no timezone; assuming UTC as the contract "
            "specifies (a local-time column would silently shift every result)"
        )
        times = times.dt.tz_localize("UTC")
    else:
        times = times.dt.tz_convert("UTC")

    unparsed = int(times.isna().sum())
    if unparsed:
        warnings.append(f"dropped {unparsed} row(s) with an unparseable timestamp")
    frame = frame.assign(timestamp=times)
    return frame[frame["timestamp"].notna()]


def load_vessels(path: str | Path) -> tuple[list[VesselTrack], list[str]]:
    """Load and assemble every vessel track in a parquet file.

    Returns ``(tracks, warnings)``; raises MISSING_INPUT if the file is unreadable or
    lacks a load-bearing column.
    """
    warnings: list[str] = []
    frame = _read_frame(path)

    absent = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if absent:
        raise missing_input(
            f"vessels.parquet is missing required column(s): {absent}",
            missing=absent, present=sorted(frame.columns),
        )

    optional_absent = [c for c in CONTRACT_COLUMNS if c not in frame.columns]
    if optional_absent:
        warnings.append(
            f"vessels.parquet lacks contract column(s) {optional_absent}; "
            "the dependent factors degrade rather than fail"
        )
        for column in optional_absent:
            frame[column] = _COLUMN_DEFAULTS[column]

    frame = _normalise_timestamps(frame, warnings)
    if frame.empty:
        return [], warnings

    out_of_range = (
        frame["lat"].abs().gt(90) | frame["lon"].abs().gt(180) | frame["lat"].isna()
        | frame["lon"].isna()
    )
    if out_of_range.any():
        warnings.append(
            f"dropped {int(out_of_range.sum())} row(s) whose lat/lon are not valid "
            "EPSG:4326 coordinates"
        )
        frame = frame[~out_of_range]

    before = len(frame)
    frame = frame.drop_duplicates(subset=["mmsi", "timestamp"], keep="first")
    if len(frame) < before:
        warnings.append(f"dropped {before - len(frame)} duplicate (mmsi, timestamp) row(s)")

    frame = frame.sort_values(["mmsi", "timestamp"])

    tracks: list[VesselTrack] = []
    for mmsi, group in frame.groupby("mmsi", sort=True):
        first = group.iloc[0]
        tracks.append(
            VesselTrack(
                mmsi=int(mmsi),
                name=first.get("vessel_name"),
                vessel_type=first.get("vessel_type"),
                imo=first.get("imo"),
                length_m=float(pd.to_numeric(first.get("length_m"), errors="coerce")),
                width_m=float(pd.to_numeric(first.get("width_m"), errors="coerce")),
                draft_m=float(pd.to_numeric(first.get("draft_m"), errors="coerce")),
                source=str(first.get("source") or "unknown"),
                culprit=bool(first.get("culprit")),
                # Convert through an explicit datetime64[ns] before taking the integer
                # view: pandas 2.x preserves the *source* unit, so a parquet file
                # written with microsecond stamps yields datetime64[us] and a naive
                # /1e9 would land 1000x off - in 1970 rather than 2017.
                times_s=group["timestamp"]
                .to_numpy(dtype="datetime64[ns]")
                .astype("int64")
                / 1e9,
                lons=group["lon"].astype(float).to_numpy(),
                lats=group["lat"].astype(float).to_numpy(),
                sog_kn=pd.to_numeric(group["sog_kn"], errors="coerce").to_numpy(float),
                cog_deg=pd.to_numeric(group["cog_deg"], errors="coerce").to_numpy(float),
            )
        )

    return tracks, warnings
