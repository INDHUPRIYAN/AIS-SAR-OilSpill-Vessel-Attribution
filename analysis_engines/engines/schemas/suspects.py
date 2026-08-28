"""Pydantic contract for ``suspects.json`` (Engine C output).

Frozen by handbook §4.4. Two kinds of entry share the ``vessels`` list, exactly as the
handbook example shows:

* **ranked** - passed the gates: ``rank``, ``score_total``, per-factor ``scores``,
  ``reason``
* **filtered** - excluded by a gate: ``filtered: true`` and ``filter_reason``, no scores
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..common.timeutil import parse_utc

FACTORS = ("proximity", "temporal", "trajectory", "anomaly", "ais_gap", "prior")


class FactorScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proximity: float = Field(ge=0.0, le=1.0)
    temporal: float = Field(ge=0.0, le=1.0)
    trajectory: float = Field(ge=0.0, le=1.0)
    anomaly: float = Field(ge=0.0, le=1.0)
    ais_gap: float = Field(ge=0.0, le=1.0)
    prior: float = Field(ge=0.0, le=1.0)


class SuspectVessel(BaseModel):
    model_config = ConfigDict(extra="allow")

    mmsi: int
    name: str | None = None
    vessel_type: str | None = None
    filtered: bool = False

    # Ranked entries only.
    rank: int | None = Field(default=None, ge=1)
    score_total: float | None = Field(default=None, ge=0.0, le=1.0)
    scores: FactorScores | None = None
    reason: str | None = None

    # Filtered entries only.
    filter_reason: str | None = None

    @model_validator(mode="after")
    def _shape_matches_state(self) -> "SuspectVessel":
        if self.filtered:
            if not self.filter_reason:
                raise ValueError(
                    f"vessel {self.mmsi} is filtered but records no filter_reason; the "
                    "UI must be able to say why it was excluded"
                )
            if self.rank is not None or self.scores is not None:
                raise ValueError(
                    f"vessel {self.mmsi} is filtered and must not carry a rank or scores"
                )
        else:
            missing = [
                field for field in ("rank", "score_total", "scores", "reason")
                if getattr(self, field) is None
            ]
            if missing:
                raise ValueError(f"ranked vessel {self.mmsi} is missing {missing}")
        return self


class SuspectsDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    investigation_id: str
    generated_utc: str
    weights: dict[str, float]
    vessels: list[SuspectVessel]

    @field_validator("generated_utc")
    @classmethod
    def _utc(cls, v: str) -> str:
        parse_utc(v, field="generated_utc")
        return v

    @field_validator("weights")
    @classmethod
    def _weights(cls, weights: dict[str, float]) -> dict[str, float]:
        absent = [f for f in FACTORS if f not in weights]
        if absent:
            raise ValueError(f"weights are missing the contract factor(s) {absent}")
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"weights must sum to 1.0, got {total:.4f}")
        return weights

    @model_validator(mode="after")
    def _ranks_are_dense_and_ordered(self) -> "SuspectsDocument":
        ranked = [v for v in self.vessels if not v.filtered]
        ranks = [v.rank for v in ranked]
        if ranks != list(range(1, len(ranked) + 1)):
            raise ValueError(f"ranks must run 1..N in order, got {ranks}")
        totals = [v.score_total for v in ranked]
        if totals != sorted(totals, reverse=True):
            raise ValueError("ranked vessels must be ordered by descending score_total")
        return self


def validate_suspects(document: dict[str, Any]) -> SuspectsDocument:
    """Validate a suspects.json payload; raises ValidationError if broken."""
    return SuspectsDocument.model_validate(document)
