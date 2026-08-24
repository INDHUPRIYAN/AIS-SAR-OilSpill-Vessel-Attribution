"""
Contract 6 — vessels.parquet      (Krishnan -> Engine C, UI)   column spec + validator
Contract 7 — suspects.json        (Engine C -> UI)
Contract 8 — provider_status.json (every API owner -> Monitoring page)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from .common import (
    ContractModel,
    ErrorClass,
    ProviderStatusValue,
    Score,
    SourceFlag,
    UTCDateTime,
)

# --------------------------------------------------------------------------
# vessels.parquet — column contract
# --------------------------------------------------------------------------

VESSEL_COLUMNS: Dict[str, str] = {
    "mmsi": "int64",                       # 9-digit MMSI
    "timestamp_utc": "datetime64[ns, UTC]",
    "lat": "float64",
    "lon": "float64",
    "sog_kn": "float64",                   # speed over ground, knots
    "cog_deg": "float64",                  # course over ground, 0-360
    "heading_deg": "float64",              # may be NaN (511 in raw AIS = unavailable)
    "vessel_type": "object",               # tanker | cargo | bulk | fishing | passenger | tug | other
    "length_m": "float64",
    "width_m": "float64",
    "draught_m": "float64",
    "source": "object",                    # 'real' | 'synthetic'
    "interpolated": "bool",                # True if the row was filled in, not transmitted
    "culprit": "bool",                     # synthetic ground truth only; False everywhere in real data
}

REQUIRED_VESSEL_COLUMNS = list(VESSEL_COLUMNS)

VESSEL_TYPES = {"tanker", "cargo", "bulk", "fishing", "passenger", "tug", "other"}


def validate_vessels_df(df) -> None:
    """Validate a vessels.parquet DataFrame. Raises ValueError with every problem found.

    Called by the main system at ingest, and by Krishnan's own tests before handover."""
    problems: List[str] = []

    missing = [c for c in REQUIRED_VESSEL_COLUMNS if c not in df.columns]
    if missing:
        problems.append(f"missing columns: {missing}")
    extra = [c for c in df.columns if c not in VESSEL_COLUMNS]
    if extra:
        problems.append(f"unexpected columns: {extra} (extend the contract, don't smuggle)")
    if problems:
        raise ValueError("; ".join(problems))

    if len(df) == 0:
        raise ValueError("vessels.parquet is empty")

    ts = df["timestamp_utc"]
    if getattr(ts.dtype, "tz", None) is None:
        problems.append("timestamp_utc must be timezone-aware UTC, not naive")
    if not df["timestamp_utc"].is_monotonic_increasing:
        # not fatal, but attribution assumes per-MMSI ordering
        if not df.sort_values(["mmsi", "timestamp_utc"]).equals(df):
            problems.append("rows must be sorted by (mmsi, timestamp_utc)")

    if not df["lat"].between(-90, 90).all():
        problems.append("lat outside [-90, 90]")
    if not df["lon"].between(-180, 180).all():
        problems.append("lon outside [-180, 180] (lat/lon swapped?)")
    if not df["sog_kn"].between(0, 60).all():
        problems.append("sog_kn outside [0, 60]")
    if not df["cog_deg"].between(0, 360).all():
        problems.append("cog_deg outside [0, 360]")
    if not df["mmsi"].between(100_000_000, 999_999_999).all():
        problems.append("mmsi must be a 9-digit number")

    bad_types = set(df["vessel_type"].unique()) - VESSEL_TYPES
    if bad_types:
        problems.append(f"unknown vessel_type values: {sorted(bad_types)}")
    bad_source = set(df["source"].unique()) - {"real", "synthetic"}
    if bad_source:
        problems.append(f"source must be 'real' or 'synthetic'; got {sorted(bad_source)}")

    if (df["source"] == "real").any() and df.loc[df["source"] == "real", "culprit"].any():
        problems.append("culprit=True on real AIS rows — ground truth exists only in synthetic data")

    if problems:
        raise ValueError("vessels.parquet contract violations: " + "; ".join(problems))


# --------------------------------------------------------------------------
# suspects.json
# --------------------------------------------------------------------------


class SubScores(ContractModel):
    """The six explainable factors. Each is 0-1 BEFORE weighting; the UI draws these as bars."""

    proximity: Score
    temporal: Score
    trajectory: Score
    behaviour: Score
    ais_gap: Score
    vessel_prior: Score


class Evidence(ContractModel):
    """Raw numbers behind the score, so the reason string can be checked by a human."""

    closest_approach_km: Optional[float] = Field(default=None, ge=0)
    time_in_origin_window_min: Optional[float] = Field(default=None, ge=0)
    ais_gap_minutes: Optional[float] = Field(default=None, ge=0)
    course_delta_deg: Optional[float] = Field(default=None, ge=0, le=180)
    min_sog_kn: Optional[float] = Field(default=None, ge=0)
    track_points_in_cloud: Optional[int] = Field(default=None, ge=0)


class Suspect(ContractModel):
    rank: int = Field(ge=1)
    mmsi: int = Field(ge=100_000_000, le=999_999_999)
    vessel_name: Optional[str] = None
    vessel_type: str
    total_score: Score
    sub_scores: SubScores
    reason: str = Field(min_length=10, description="Plain-language explanation shown in the UI")
    evidence: Evidence = Field(default_factory=Evidence)
    source: SourceFlag = SourceFlag.REAL


class FilteredVessel(ContractModel):
    mmsi: int
    reason: str = Field(description="e.g. 'outside time window', 'never entered origin cloud'")


class SuspectsReport(ContractModel):
    scene_id: str
    run_id: str
    generated_utc: UTCDateTime
    weights: Dict[str, float] = Field(
        description="Factor weights actually used; shown in the UI so scoring is auditable"
    )
    suspects: List[Suspect]
    filtered_out: List[FilteredVessel] = Field(default_factory=list)
    total_vessels_considered: int = Field(ge=0)
    source: SourceFlag = SourceFlag.REAL

    @field_validator("weights")
    @classmethod
    def _weights_ok(cls, v: Dict[str, float]) -> Dict[str, float]:
        expected = {"proximity", "temporal", "trajectory", "behaviour", "ais_gap", "vessel_prior"}
        if set(v) != expected:
            raise ValueError(f"weights must cover exactly {sorted(expected)}")
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        return v

    @field_validator("suspects")
    @classmethod
    def _ranked(cls, v: List[Suspect]) -> List[Suspect]:
        if [s.rank for s in v] != list(range(1, len(v) + 1)):
            raise ValueError("suspects must be ranked 1..N with no gaps, in order")
        scores = [s.total_score for s in v]
        if scores != sorted(scores, reverse=True):
            raise ValueError("suspects must be sorted by descending total_score")
        return v


# --------------------------------------------------------------------------
# provider_status.json
# --------------------------------------------------------------------------


class ProviderStatus(ContractModel):
    provider: str
    purpose: str = Field(description="Human-readable: what this provider supplies")
    status: ProviderStatusValue
    last_code: Optional[int] = Field(default=None, description="HTTP status of the last call")
    last_latency_ms: Optional[int] = Field(default=None, ge=0)
    last_success_utc: Optional[UTCDateTime] = None
    last_failure_utc: Optional[UTCDateTime] = None
    last_error_class: ErrorClass = ErrorClass.NONE
    chain: List[str] = Field(min_length=1, description="Fallback chain, primary first")
    active_provider: str = Field(description="Which chain member is serving right now")

    @field_validator("active_provider")
    @classmethod
    def _in_chain(cls, v: str, info) -> str:
        chain = info.data.get("chain") or []
        if chain and v not in chain:
            raise ValueError(f"active_provider '{v}' is not in chain {chain}")
        return v


class ProviderStatusFile(ContractModel):
    generated_utc: UTCDateTime
    owner: str = Field(description="Which developer's service wrote this file")
    providers: List[ProviderStatus]
