"""Plain-language explanation per vessel (handbook §4.4 / §6 Phase 6).

The target sentence, from the handbook's worked example:

    "Passed through the 90% origin region at 2017-02-01 16:04 UTC, slowed from 13.8 to
    5.9 kn, and had a 47-minute AIS gap overlapping the estimated discharge window."

Every clause is generated from the evidence the scoring factors recorded, never from the
score itself - an investigator reading the sentence must be able to check each claim
against the AIS data. Clauses appear only when their evidence exists, so a vessel with
nothing remarkable gets a short, honest sentence rather than padded prose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Above this a factor is worth mentioning in the sentence.
MENTION_THRESHOLD = 0.25


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _join(clauses: list[str]) -> str:
    """Join clauses as prose: 'a', 'a and b', 'a, b, and c'."""
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    if len(clauses) == 2:
        return f"{clauses[0]} and {clauses[1]}"
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


def explain(track, scores, origin, *, level: float = 0.9) -> str:
    """One sentence describing why this vessel scored as it did."""
    evidence: dict[str, Any] = scores.evidence
    percent = int(round(level * 100))

    # --- opening clause: where and when it was seen -----------------------------
    when = evidence.get("closest_utc_s")
    if when is not None:
        opening = (
            f"Passed through the {percent}% origin region at {_stamp(when)} UTC"
        )
    else:
        opening = f"Track intersects the {percent}% origin region"

    clauses: list[str] = []

    if scores.anomaly >= MENTION_THRESHOLD:
        cruise = evidence.get("cruise_kn")
        slowest = evidence.get("slowest_kn")
        if cruise is not None and slowest is not None and cruise > slowest:
            clauses.append(f"slowed from {cruise} to {slowest} kn")
        change = evidence.get("course_change_deg")
        if change and change >= 20:
            clauses.append(f"altered course by {int(round(change))}°")
        loiter = evidence.get("loiter_fraction")
        if loiter and loiter >= 0.25:
            clauses.append(f"loitered for {int(round(loiter * 100))}% of its time there")

    if scores.ais_gap >= MENTION_THRESHOLD:
        minutes = evidence.get("gap_minutes")
        if minutes:
            clauses.append(
                f"had a {int(minutes)}-minute AIS gap overlapping the estimated "
                "discharge window"
            )

    if scores.trajectory >= MENTION_THRESHOLD:
        offset = evidence.get("axis_offset_deg")
        if offset is not None and offset <= 20:
            clauses.append(f"ran within {int(round(offset))}° of the slick's axis")

    if not clauses:
        # Nothing remarkable: say so rather than dressing up a weak case.
        detail = "no unusual speed, course or transmission behaviour was recorded"
        return f"{opening}, but {detail}."

    return f"{opening}, {_join(clauses)}."


def explain_filtered(filter_reason: str, metrics: dict[str, Any]) -> str:
    """One line for a vessel the gates excluded, quoting the number behind it."""
    if filter_reason == "outside origin region":
        distance = metrics.get("distance_to_region_km")
        if distance:
            return f"Filtered out: closest approach was {distance} km from the origin region."
    elif filter_reason == "outside time window":
        hours = metrics.get("hours_outside_window")
        if hours:
            return (
                f"Filtered out: present in the origin region, but {hours} h outside the "
                "estimated discharge window."
            )
    elif filter_reason == "course incompatible with slick axis":
        offset = metrics.get("axis_offset_deg")
        if offset is not None:
            return (
                f"Filtered out: course ran {int(round(offset))}° off the slick's axis, "
                "so a discharge trailing behind it would not match."
            )
    return f"Filtered out: {filter_reason}."
