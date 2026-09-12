"""Automatic incident creation, zone resolution and alert routing.

    sealed run -> validation gate -> Incident -> point-in-polygon -> Zone
              -> responsible officer -> routed Alert

WHERE THE THRESHOLDS COME FROM
------------------------------
Standing rule 11 forbids tuning a gate to manufacture a better demo, so this
module **introduces no new tuned constant**. Every number it compares against
already exists and was already measured:

  * **confidence** is compared against `tiling.detect_threshold` from
    `config/normalisation.yaml` -- 0.5, chosen from a recorded threshold sweep
    on the held-out split (the file carries the table). Inventing a second
    confidence threshold here would mean the pipeline called a pixel oil at
    one number and the case-opening logic disbelieved it at another.

  * **oil candidates** must be at least one, as classified by the deployed
    screen `yolo11n-screen-dartis-2026-08-24`. That is not a threshold, it is
    the question "did the model report any oil at all".

  * **area is NOT a gate.** `AUTO_INCIDENT_MIN_AREA_KM2` defaults to 0.0, off.
    Any figure would have been picked by me, and the only defensible small-area
    floor in the repo -- characterise's 0.05 km2 -- is about whether a region
    is representable, not about whether it is an incident. An operator can set
    it; the default adds nothing.

The gate that does real work is `engine == "ml"`. A run whose segmenter fell
back to threshold-morphology must not open a case: that engine's accuracy was
never measured on these scenes, and the fallback used to happen SILENTLY on any
full-size scene (a 2.24 GiB MemoryError, caught upstream). An automatic case
opened by an engine nobody evaluated is worse than no case.

WHAT COUNTS AS WHAT
-------------------
Three counts in a run's artefacts are different quantities and are never
conflated:

  * `detect_response.candidates` where `class == "oil"` -- what the SCREEN
    classified as oil. 31 on the flagship.
  * the same list where `class == "lookalike"` -- 361 on the flagship.
    Reported, never counted as oil.
  * `slick.geojson` features -- connected regions the SEGMENTER produced and
    characterise measured. **62** on the flagship, and they carry no class at
    all. So the region count is not the oil count, and the area total is the
    area of all segmented regions, not of confirmed oil.

`slick_regions` and `oil_candidates` are therefore separate fields with
separate labels, and the incident's area figure says which one it is.

THE HUMAN REMAINS THE AUTHORITY
-------------------------------
An auto-created incident opens at status `open`, not `attributed`. It is a
case FOR REVIEW raised by a model, and the officer confirms or dismisses it
(spec section 22). Nothing here concludes anything.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.db import (Alert, Incident, Investigation, Run, User,
                               Zone, get_db, utcnow)
from backend.services import audit as audit_service
from backend.services import zones as zsvc

log = logging.getLogger(__name__)
settings = get_settings()

# Engines whose output may open a case automatically. Deliberately a list of
# one: see the module docstring.
TRUSTED_ENGINES = ("ml",)

# Off by default, and that is the point -- see the module docstring.
AUTO_INCIDENT_MIN_AREA_KM2 = float(
    os.getenv("AUTO_INCIDENT_MIN_AREA_KM2", "0.0"))
AUTO_INCIDENT_MIN_OIL_CANDIDATES = int(
    os.getenv("AUTO_INCIDENT_MIN_OIL_CANDIDATES", "1"))

# Operational triage bands for total segmented area. These are a PRESENTATION
# choice, not a measurement, and are labelled as such wherever they surface:
# decade boundaries so nobody mistakes them for a derived quantity. Severity
# decides queue ordering and escalation, never whether a case is opened.
SEVERITY_BANDS = ((100.0, "critical"), (10.0, "high"), (1.0, "medium"))
SEVERITY_FLOOR = "low"

# Incident severity -> the Alert model's three-value vocabulary.
ALERT_SEVERITY = {"critical": "critical", "high": "critical",
                  "medium": "warning", "low": "info"}


@dataclass
class DetectionVerdict:
    """Whether a sealed run's output justifies opening a case, and why.

    `reasons` is a list of sentences, populated on BOTH outcomes. A gate that
    only explains itself when it fails is a gate nobody can audit when it
    passes.
    """

    run_id: str
    validated: bool = False
    reasons: list[str] = field(default_factory=list)

    engine: Optional[str] = None
    confidence: Optional[float] = None
    confidence_threshold: Optional[float] = None
    # From the SCREEN. Not the same as `slick_regions`.
    oil_candidates: int = 0
    lookalike_candidates: int = 0
    # From the SEGMENTER, via characterise. Carries no class.
    slick_regions: int = 0
    total_area_km2: float = 0.0
    largest_area_km2: float = 0.0
    centroid: Optional[list[float]] = None          # [lon, lat]
    acquired_utc: Optional[str] = None
    scene_id: Optional[str] = None
    model_version: Optional[str] = None
    severity: str = SEVERITY_FLOOR
    age_confidence_label: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "validated": self.validated,
            "reasons": self.reasons,
            "engine": self.engine,
            "confidence": self.confidence,
            "confidence_threshold": self.confidence_threshold,
            "oil_candidates": self.oil_candidates,
            "lookalike_candidates": self.lookalike_candidates,
            "slick_regions": self.slick_regions,
            "total_area_km2": round(self.total_area_km2, 4),
            "largest_area_km2": round(self.largest_area_km2, 4),
            "centroid": self.centroid,
            "acquired_utc": self.acquired_utc,
            "scene_id": self.scene_id,
            "model_version": self.model_version,
            "severity": self.severity,
            "age_confidence_label": self.age_confidence_label,
            # Said on every verdict, because the numbers above invite exactly
            # this misreading.
            "counting_note": (
                "oil_candidates and lookalike_candidates are the deployed "
                "SCREEN's classification of bounding-box candidates. "
                "slick_regions is the count of connected regions the "
                "SEGMENTER produced, which carry no class -- so the areas "
                "here are the area of all segmented regions, NOT of confirmed "
                "oil. The three are different quantities."),
        }


def _detect_threshold() -> float:
    """The deployed pixel threshold, read from the frozen normalisation file.

    Read rather than hardcoded so this gate cannot drift away from the value
    the segmenter was actually calibrated at. A missing or unreadable file
    falls back to 0.5, which is the documented value and the sigmoid midpoint,
    and the verdict records the threshold it used either way.
    """
    path = Path(__file__).resolve().parents[2] / "config" / "normalisation.yaml"
    try:
        import yaml

        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        value = (cfg.get("tiling") or {}).get("detect_threshold")
        if isinstance(value, (int, float)):
            return float(value)
    except Exception:                              # noqa: BLE001
        log.warning("[auto-incident] could not read detect_threshold from %s; "
                    "using the documented 0.5", path)
    return 0.5


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        return None


def validate_detection(run_id: str,
                       runs_root: Optional[Path] = None) -> DetectionVerdict:
    """Read a sealed run's artefacts and decide whether to open a case.

    Pure with respect to the database: it opens no session and creates
    nothing, so it can be called to PREVIEW the decision (and is, by
    `GET /api/incidents/auto/preview`).
    """
    verdict = DetectionVerdict(run_id=run_id)
    root = (runs_root or settings.runs_root) / run_id

    if not root.is_dir():
        verdict.reasons.append(
            f"no run directory at {root} -- nothing to validate")
        return verdict

    detect = _read_json(root / "detect_response.json")
    if detect is None:
        verdict.reasons.append(
            "detect_response.json is missing or unreadable, so there is no "
            "detection to validate")
        return verdict

    verdict.engine = detect.get("engine")
    verdict.confidence = detect.get("confidence")
    verdict.scene_id = detect.get("scene_id")
    verdict.model_version = detect.get("model_version")
    verdict.confidence_threshold = _detect_threshold()

    candidates = detect.get("candidates") or []
    verdict.oil_candidates = sum(1 for c in candidates
                                 if str(c.get("class", "")).lower() == "oil")
    verdict.lookalike_candidates = sum(
        1 for c in candidates
        if str(c.get("class", "")).lower() == "lookalike")

    slick = _read_json(root / "slick.geojson") or {}
    features = slick.get("features") or []
    verdict.slick_regions = len(features)
    areas = [float(f.get("properties", {}).get("area_km2") or 0.0)
             for f in features]
    verdict.total_area_km2 = sum(areas)
    verdict.largest_area_km2 = max(areas) if areas else 0.0
    verdict.acquired_utc = (slick.get("metadata") or {}).get("acquired_utc")

    # The centroid of the LARGEST region, because that is the one an officer
    # is being sent to look at. Averaging 62 region centroids would place the
    # incident in open water between them.
    if features:
        largest = max(features,
                      key=lambda f: float(f.get("properties", {})
                                          .get("area_km2") or 0.0))
        centroid = largest.get("properties", {}).get("centroid")
        if (isinstance(centroid, (list, tuple)) and len(centroid) >= 2):
            try:
                verdict.centroid = [float(centroid[0]), float(centroid[1])]
            except (TypeError, ValueError):
                verdict.centroid = None
        verdict.age_confidence_label = (largest.get("properties", {})
                                        .get("age_confidence_label"))

    verdict.severity = severity_for_area(verdict.total_area_km2)

    # --- the gate ------------------------------------------------------
    failures: list[str] = []

    if verdict.engine not in TRUSTED_ENGINES:
        failures.append(
            f"detection engine was {verdict.engine!r}, not one of "
            f"{list(TRUSTED_ENGINES)}. A fallback engine's accuracy has not "
            f"been measured on these scenes, so it does not open a case "
            f"automatically -- the run is still available for a human to "
            f"promote.")
    else:
        verdict.reasons.append(
            f"detection ran on the deployed ML engine ({verdict.model_version})")

    if verdict.oil_candidates < AUTO_INCIDENT_MIN_OIL_CANDIDATES:
        failures.append(
            f"the deployed screen classified {verdict.oil_candidates} "
            f"candidate(s) as oil ({verdict.lookalike_candidates} as "
            f"look-alike); at least {AUTO_INCIDENT_MIN_OIL_CANDIDATES} is "
            f"required")
    else:
        verdict.reasons.append(
            f"the deployed screen classified {verdict.oil_candidates} "
            f"candidate(s) as oil and {verdict.lookalike_candidates} as "
            f"look-alike")

    threshold = verdict.confidence_threshold or 0.5
    if verdict.confidence is None:
        failures.append("the detection reported no confidence value")
    elif float(verdict.confidence) < threshold:
        failures.append(
            f"segmenter confidence {verdict.confidence:.4f} is below the "
            f"deployed detection threshold {threshold} (from "
            f"config/normalisation.yaml)")
    else:
        verdict.reasons.append(
            f"segmenter confidence {verdict.confidence:.4f} meets the deployed "
            f"threshold {threshold}")

    if verdict.centroid is None:
        failures.append(
            "no region centroid could be read, so the incident could not be "
            "placed or routed to a zone")
    else:
        verdict.reasons.append(
            f"largest region centroid at "
            f"{verdict.centroid[1]:.5f}, {verdict.centroid[0]:.5f} "
            f"({verdict.largest_area_km2:.3f} km2)")

    if (AUTO_INCIDENT_MIN_AREA_KM2 > 0
            and verdict.largest_area_km2 < AUTO_INCIDENT_MIN_AREA_KM2):
        failures.append(
            f"largest region {verdict.largest_area_km2:.4f} km2 is below the "
            f"configured minimum {AUTO_INCIDENT_MIN_AREA_KM2} km2")

    verdict.validated = not failures
    verdict.reasons.extend(failures)
    return verdict


def severity_for_area(area_km2: float) -> str:
    """Operational triage band. A presentation choice, not a measurement."""
    for floor, label in SEVERITY_BANDS:
        if area_km2 >= floor:
            return label
    return SEVERITY_FLOOR


# --------------------------------------------------------------------------
# creation + routing
# --------------------------------------------------------------------------

def _next_incident_id(db: Session) -> str:
    """Shares `incidents._next_id` rather than reimplementing the sequence.

    Two id generators against one table eventually collide, and the collision
    surfaces as a primary-key error in the middle of an unattended pipeline.
    """
    from backend.api.incidents import _next_id

    return _next_id(db)


@dataclass
class RoutingDecision:
    """Which desk an incident landed on, and how it got there."""

    zone: Optional[Zone] = None
    zone_path: Optional[str] = None
    officer: Optional[User] = None
    routing: str = "unrouted"
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "zone_id": (self.zone.id if self.zone else None),
            "zone_name": (self.zone.name if self.zone else None),
            "zone_path": self.zone_path,
            "routing": self.routing,
            "detail": self.detail,
            "officer": ({"user_id": self.officer.id,
                         "email": self.officer.email,
                         "display_name": self.officer.display_name}
                        if self.officer else None),
        }


def route_point(db: Session, lon: float, lat: float) -> RoutingDecision:
    """Resolve a coordinate to a zone and a responsible officer.

    Four outcomes, each named rather than collapsed into "unrouted":

      zone        a zone owns the point and has its own primary officer
      escalated   no officer on the zone, but one above it took it
      unzoned     no declared zone covers the point at all
      unassigned  a zone owns it and nobody, anywhere above it, is assigned

    The last two both mean "nobody will see this", and separating them is the
    difference between "draw a zone here" and "assign somebody to Zone 03".
    Neither is silently handed to an administrator to keep the queue tidy.
    """
    chain = zsvc.zones_covering_point(db, lon, lat)
    zone = zsvc.zone_for_point(db, lon, lat)
    if zone is None and chain:
        zone = chain[-1]

    decision = RoutingDecision(
        zone=zone,
        zone_path=("/".join(z.id for z in chain) or None))

    if zone is None:
        decision.routing = "unzoned"
        decision.detail = (
            f"{lat:.5f}, {lon:.5f} falls outside every declared operational "
            f"zone. No officer is responsible for this water. Draw a zone "
            f"covering it to route future detections here.")
        return decision

    officer = zsvc.primary_officer(db, zone)
    decision.officer = officer
    if officer is None:
        decision.routing = "unassigned"
        decision.detail = (
            f"zone {zone.id} ({zone.name}) owns this position but has no "
            f"assigned officer, and none is assigned above it either. Assign "
            f"one; until then this alert is visible but unrouted.")
        return decision

    direct = any(o["user_id"] == officer.id and o["is_primary"]
                 for o in zsvc.zone_officers(db, zone.id))
    decision.routing = "zone" if direct else "escalated"
    decision.detail = (
        f"routed to {officer.email} as primary officer for {zone.id}"
        if direct else
        f"zone {zone.id} has no primary officer; escalated to "
        f"{officer.email}, assigned above it")
    return decision


def create_incident_from_run(db: Session, run_id: str, *,
                             request=None,
                             actor: Optional[User] = None,
                             runs_root: Optional[Path] = None,
                             force: bool = False) -> dict:
    """Open a case from a validated run, route it, and raise its alert.

    Returns a dict in every case, including refusal. A caller in the pipeline
    must be able to record WHY no incident was opened, and an exception would
    make "the detection did not clear the gate" indistinguishable from "the
    incident service crashed".
    """
    run = db.get(Run, run_id)
    verdict = validate_detection(run_id, runs_root=runs_root)

    if run is None:
        return {"created": False, "reason": f"no run row for {run_id}",
                "verdict": verdict.as_dict()}
    if run.incident_id:
        return {"created": False,
                "reason": f"run {run_id} already belongs to {run.incident_id}",
                "incident_id": run.incident_id,
                "verdict": verdict.as_dict()}
    if not verdict.validated and not force:
        return {"created": False,
                "reason": "the detection did not clear the validation gate",
                "verdict": verdict.as_dict()}

    lon, lat = (verdict.centroid or [None, None])
    decision = (route_point(db, lon, lat)
                if lon is not None and lat is not None else RoutingDecision())
    if lon is None:
        decision.routing = "unzoned"
        decision.detail = ("no centroid, so the incident could not be placed "
                           "or routed")

    detected = None
    if verdict.acquired_utc:
        try:
            detected = datetime.fromisoformat(
                str(verdict.acquired_utc).replace("Z", "+00:00"))
        except ValueError:
            detected = None
    if detected is None:
        detected = run.started_utc

    incident = Incident(
        id=_next_incident_id(db),
        # Names the scene, not the run: this string is read by people, and a
        # run id tells them nothing about what or where.
        title=(f"Automatic detection - {verdict.severity.upper()} - "
               f"{(decision.zone.name if decision.zone else 'outside all zones')}"),
        geometry_json=(json.dumps({"type": "Point", "coordinates": [lon, lat]})
                       if lon is not None else None),
        detected_utc=detected if isinstance(detected, datetime) else None,
        # `open`, never `attributed`. A model raised this; a human concludes it.
        status="open",
        region=(decision.zone.name if decision.zone else None),
        zone_id=(decision.zone.id if decision.zone else None),
        zone_path=decision.zone_path,
        origin="auto",
        detection_confidence=verdict.confidence,
        area_km2=round(verdict.total_area_km2, 4),
        severity=verdict.severity,
        source_run_id=run_id,
        scene_id=verdict.scene_id,
        # The officer the alert was routed to is also the case assignee, so the
        # two cannot disagree about whose work it is.
        assignee_id=(decision.officer.id if decision.officer else None),
        notes=_incident_notes(verdict, decision),
        created_by=(actor.id if actor else None),
    )
    db.add(incident)
    db.flush()

    run.incident_id = incident.id
    if run.investigation_id:
        inv = db.get(Investigation, run.investigation_id)
        if inv is not None and inv.incident_id is None:
            inv.incident_id = incident.id

    alert = _raise_routed_alert(db, incident, verdict, decision, run)

    audit_service.record(
        db, "incident.create", request=request, resource=incident.id,
        actor=(None if request is not None else "pipeline"),
        detail=json.dumps({"origin": "auto", "run_id": run_id,
                           "zone_id": incident.zone_id,
                           "routing": decision.routing,
                           "severity": verdict.severity,
                           "oil_candidates": verdict.oil_candidates,
                           "confidence": verdict.confidence}),
        commit=False)
    audit_service.record(
        db, "alert.route", request=request,
        resource=(alert.id if alert else f"incident:{incident.id}"),
        actor=(None if request is not None else "pipeline"),
        detail=json.dumps(decision.as_dict(), default=str), commit=False)
    db.commit()

    return {"created": True, "incident_id": incident.id,
            "alert_id": (alert.id if alert else None),
            "routing": decision.as_dict(),
            "verdict": verdict.as_dict(),
            "forced": bool(force and not verdict.validated)}


def _incident_notes(verdict: DetectionVerdict,
                    decision: RoutingDecision) -> str:
    """The case file's opening statement.

    Written into the incident because a case opened by a machine must carry
    its own provenance and its own limits -- an officer reading it a week
    later should not have to find this module to learn that the "oil" count
    is a model's classification and that the area is of all segmented regions.
    """
    lines = [
        "AUTOMATIC DETECTION - opened by the pipeline, awaiting officer review.",
        "",
        f"Scene: {verdict.scene_id or 'unknown'}",
        f"Model: {verdict.model_version or 'unknown'} (engine {verdict.engine})",
        f"Segmenter confidence: {verdict.confidence} "
        f"(deployed threshold {verdict.confidence_threshold})",
        f"Screen classification: {verdict.oil_candidates} oil, "
        f"{verdict.lookalike_candidates} look-alike",
        f"Segmented regions: {verdict.slick_regions}, "
        f"total {verdict.total_area_km2:.3f} km2, "
        f"largest {verdict.largest_area_km2:.3f} km2",
        f"Severity: {verdict.severity.upper()} (operational triage band from "
        f"total area; a presentation choice, not a measurement)",
        "",
        f"Routing: {decision.routing} - {decision.detail}",
        "",
        "LIMITS",
        "- The oil/look-alike split is the deployed SCREEN's classification of "
        "candidates. The segmented region count is a different quantity and "
        "carries no class, so the areas above are of ALL segmented regions, "
        "not of confirmed oil.",
        "- This detection is model output, not a confirmed spill. No ground "
        "truth exists for an operational acquisition.",
    ]
    if verdict.age_confidence_label:
        lines.append(
            f"- Slick age estimate carries {verdict.age_confidence_label.upper()} "
            f"confidence and must not be read as a discharge time.")
    return "\n".join(lines)


def _raise_routed_alert(db: Session, incident: Incident,
                        verdict: DetectionVerdict,
                        decision: RoutingDecision,
                        run: Run) -> Optional[Alert]:
    """The notification, addressed to the zone's officer.

    `routed_to` is distinct from `assigned_to`: routing is what the system
    decided, assignment is what a human then did. Collapsing them would erase
    the evidence that an alert reached the correct desk before being
    reassigned.
    """
    title = (f"Oil spill detected - {verdict.severity.upper()} - "
             f"{decision.zone.name if decision.zone else 'outside all zones'}")
    detail = "\n".join([
        f"Region: {decision.zone_path or 'outside all declared zones'}",
        f"Detected: {incident.detected_utc}",
        f"Area: {verdict.total_area_km2:.2f} km2 across "
        f"{verdict.slick_regions} segmented region(s)",
        f"Screen: {verdict.oil_candidates} oil / "
        f"{verdict.lookalike_candidates} look-alike candidates",
        f"Confidence: {verdict.confidence}",
        f"Severity: {verdict.severity.upper()}",
        f"Responsible officer: "
        f"{decision.officer.email if decision.officer else 'UNASSIGNED'}",
        "",
        decision.detail,
    ])

    alert = Alert(
        id=f"alert-{secrets.token_hex(5)}",
        kind="detection",
        severity=ALERT_SEVERITY.get(verdict.severity, "info"),
        status="open",
        title=title,
        detail=detail,
        scene_id=verdict.scene_id,
        run_id=run.id,
        investigation_id=run.investigation_id,
        incident_id=incident.id,
        zone_id=(decision.zone.id if decision.zone else None),
        routed_to=(decision.officer.id if decision.officer else None),
        routing=decision.routing,
        routing_detail=decision.detail,
    )
    db.add(alert)
    db.flush()
    return alert


def backfill_zone(db: Session, incident: Incident) -> Optional[str]:
    """Resolve the zone for an incident that has geometry but no zone.

    For incidents created before the zone model existed. It stamps `zone_id`
    from the CURRENT boundaries, which is why it is a separate, explicit
    action rather than something the read path does: a live join would
    re-attribute closed cases every time somebody moved a line on a map.
    """
    if incident.zone_id or not incident.geometry_json:
        return None
    try:
        geometry = json.loads(incident.geometry_json)
        coords = geometry.get("coordinates")
        if geometry.get("type") == "Point" and coords and len(coords) >= 2:
            lon, lat = float(coords[0]), float(coords[1])
        else:
            from shapely.geometry import shape

            point = shape(geometry).representative_point()
            lon, lat = point.x, point.y
    except Exception:                              # noqa: BLE001
        return None

    decision = route_point(db, lon, lat)
    if decision.zone is None:
        return None
    incident.zone_id = decision.zone.id
    incident.zone_path = decision.zone_path
    if incident.region is None:
        incident.region = decision.zone.name
    return decision.zone.id
