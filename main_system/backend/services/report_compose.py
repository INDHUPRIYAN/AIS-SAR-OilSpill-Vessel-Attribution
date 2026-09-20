"""Composing an investigation report from a sealed run's artefacts.

The report page used to assemble itself in the browser from six parallel fetches.
That works, and it means the report only exists while a tab is open: nothing can
be versioned, reviewed, approved, or quoted later with any confidence that it
says what it said. This builds the document server-side, once, from the run's
own files.

Three rules the composer follows without exception.

**Artefacts only.** Every value here is read from a file the pipeline sealed. If
a stage did not run, its section does not render -- it is not filled with a
placeholder, an average, or a hopeful default. A report with a missing section
is a true report about an incomplete run; a report with an invented section is
not a report.

**Neutral language.** The executive summary is templated, and the template has
no word in it that assigns responsibility. A ranked vessel is "Rank #1 · score
0.67", never a culprit, suspect-in-the-legal-sense, or perpetrator. The system
ranks proximity and behaviour; it does not determine who is at fault, and the
prose must not imply otherwise even by accident.

**Limitations come from a file, not from memory.** The methodology section is
sourced from ``docs/LIMITATIONS.md`` and the engines' own docstrings. Sections
without a real source do not render. A limitations list written by hand into a
composer drifts out of date the moment the system changes -- and a stale
limitation is worse than none, because it is a false statement about what the
system cannot do.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
LIMITATIONS_PATH = REPO_ROOT / "docs" / "LIMITATIONS.md"

# The vocabulary the summary is allowed to use about a ranked vessel. Anything
# stronger is a legal claim this system is not entitled to make.
NEUTRAL_RANK_PHRASE = "Rank #{rank} · score {score:.2f}"


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def _executive_summary(manifest: dict, suspects: Optional[dict],
                       slick: Optional[dict], cloud: Optional[dict]) -> dict:
    """Neutral prose. Nothing here names a responsible party."""
    stages = {s["stage"]: s for s in manifest.get("stages", [])}
    real = sum(1 for s in stages.values() if s.get("status") in ("ok", "fallback"))

    lines: List[str] = []
    scene_id = manifest.get("scene_id", "unknown scene")
    lines.append(
        f"Run {manifest.get('run_id')} processed Sentinel-1 scene {scene_id}. "
        f"{real} of {len(stages)} pipeline stages completed on real data.")

    if slick:
        props = (slick.get("features") or [{}])[0].get("properties", {})
        area = props.get("area_km2")
        if area is not None:
            lines.append(
                f"The dominant feature measures {area:.2f} km² with a "
                f"damping ratio of {props.get('damping_ratio', 'n/a')}.")

    if cloud:
        md = cloud.get("metadata", {})
        method = md.get("origin_window_method")
        if md.get("origin_window_start_utc"):
            sentence = (
                f"Backward drift places the origin between "
                f"{md['origin_window_start_utc']} and {md['origin_window_end_utc']}")
            if md.get("origin_uncertainty_km") is not None:
                sentence += f", within {md['origin_uncertainty_km']} km"
            if method and method != "cloud_convergence":
                # The honest caveat, stated in the summary rather than buried.
                sentence += (". The current field did not deform the particle "
                             "cloud, so this window is the whole run and "
                             "localises no particular release time")
            lines.append(sentence + ".")

    if suspects:
        ranked = suspects.get("suspects", [])
        considered = suspects.get("total_vessels_considered", 0)
        filtered = len(suspects.get("filtered_out", []))
        if ranked:
            top = ranked[0]
            lines.append(
                f"{considered} vessels were considered; {filtered} were excluded "
                f"by the gates and {len(ranked)} were ranked. The highest-ranked "
                f"is MMSI {top['mmsi']} at "
                + NEUTRAL_RANK_PHRASE.format(rank=top["rank"],
                                             score=top["total_score"]) + ".")
        else:
            lines.append(
                f"{considered} vessels were considered and none passed the "
                "gates. No ranking is offered for this run.")
        lines.append(
            "A rank is a position in a weighted, explainable ordering of "
            "vessels near the estimated origin. It is not a determination of "
            "responsibility.")

    return {"kind": "executive_summary", "title": "Executive summary",
            "paragraphs": lines}


def _detection(manifest: dict, detect: Optional[dict]) -> Optional[dict]:
    stage = next((s for s in manifest.get("stages", []) if s["stage"] == "detect"), None)
    if stage is None or detect is None:
        return None
    candidates = detect.get("candidates", [])
    classes: Dict[str, int] = {}
    for c in candidates:
        key = str(c.get("class", "")).lower() or "unlabelled"
        classes[key] = classes.get(key, 0) + 1
    return {
        "kind": "detection", "title": "Detection",
        "engine": detect.get("engine"),
        "model_version": detect.get("model_version"),
        "confidence": detect.get("confidence"),
        "candidates_total": len(candidates),
        "by_class": classes,
        "data_source": stage.get("data_source"),
        "warnings": stage.get("warnings", []),
        "note": ("`engine: threshold_fallback` means the deployed segmenter did "
                 "not run and these figures do not describe it."
                 if detect.get("engine") == "threshold_fallback" else None),
    }


def _characterisation(slick: Optional[dict]) -> Optional[dict]:
    if not slick:
        return None
    props = (slick.get("features") or [{}])[0].get("properties", {})
    if not props:
        return None
    return {
        "kind": "characterisation", "title": "Characterisation",
        "area_km2": props.get("area_km2"),
        "perimeter_km": props.get("perimeter_km"),
        "orientation_deg": props.get("orientation_deg"),
        "damping_ratio": props.get("damping_ratio"),
        "age_hours": props.get("age_hours"),
        "age_confidence": props.get("age_confidence"),
        "age_method": props.get("age_method"),
        # The age estimate is a Fay-regime inversion with wide error bars. The
        # label travels with the number so a reader never sees one without the
        # other.
        "age_label": "LOW confidence",
        "age_caveat": ("Slick age is inverted from spreading physics and is "
                       "low confidence by construction. It bounds the search "
                       "window; it does not date the discharge."),
    }


def _environment(cloud: Optional[dict]) -> Optional[dict]:
    if not cloud:
        return None
    forcing = (cloud.get("metadata") or {}).get("forcing") or {}
    if not forcing:
        return None
    out = {"kind": "environment", "title": "Environment", "engine": forcing.get("engine")}
    for field in ("currents", "wind"):
        block = forcing.get(field)
        if isinstance(block, dict):
            out[field] = {"provider": block.get("provider"),
                          "dataset": block.get("dataset"),
                          "variables": block.get("variables"),
                          "fallback": block.get("fallback")}
        elif block:
            out[field] = {"provider": str(block)}
    return out


def _origin(cloud: Optional[dict]) -> Optional[dict]:
    if not cloud:
        return None
    md = cloud.get("metadata") or {}
    if not md.get("origin_window_start_utc"):
        return None
    ellipse_props = [f["properties"] for f in cloud.get("features", [])
                     if f.get("properties", {}).get("feature_type") == "ellipse"]
    # A cloud may carry more than one contour per step (0.5 beside 0.9). The
    # report states the widest -- the uncertainty ellipse -- as it always has.
    top = max((p.get("confidence_level") or 0 for p in ellipse_props), default=0)
    ellipses = [p.get("semi_major_m") for p in ellipse_props
                if (p.get("confidence_level") or 0) == top]
    ellipses = [e for e in ellipses if e is not None]
    return {
        "kind": "origin", "title": "Hindcast and origin",
        "window_start_utc": md["origin_window_start_utc"],
        "window_end_utc": md["origin_window_end_utc"],
        "peak_utc": md.get("origin_peak_utc"),
        "method": md.get("origin_window_method"),
        "uncertainty_km": md.get("origin_uncertainty_km"),
        "uncertainty_coverage": md.get("origin_uncertainty_coverage"),
        "uncertainty_method": md.get("origin_uncertainty_method"),
        "backtrack_hours": md.get("backtrack_hours"),
        "n_particles": md.get("n_particles"),
        "ellipse_semi_major_m": {"min": min(ellipses), "max": max(ellipses)}
        if ellipses else None,
        "method_caveat": (
            None if md.get("origin_window_method") == "cloud_convergence" else
            "The current field did not deform the particle cloud, so the whole "
            "run is reported as the window and the peak carries no information."),
    }


def _forecast(forecast: Optional[dict]) -> Optional[dict]:
    if not forecast:
        return None
    md = forecast.get("metadata") or {}
    horizons = md.get("horizons_h") or md.get("forecast_horizons_h")
    if not horizons:
        return None
    return {"kind": "forecast", "title": "Forecast",
            "horizons_h": horizons,
            "engine": (md.get("forcing") or {}).get("engine"),
            "note": "Forecast extents are physics, not a prediction of "
                    "where oil will be found."}


def _ais(manifest: dict, suspects: Optional[dict], funnel: Optional[dict]) -> dict:
    ais = manifest.get("ais") or {}
    stage = next((s for s in manifest.get("stages", [])
                  if s["stage"] == "attribution"), {})
    return {
        "kind": "ais", "title": "AIS",
        "selection": ais.get("selection"),
        "data_source": ais.get("data_source") or stage.get("data_source"),
        "covers_origin": ais.get("covers_origin"),
        "considered": ais.get("considered", []),
        "source_badge": ("REAL" if (suspects or {}).get("source") == "real"
                         else "SYNTHETIC"),
        "funnel": funnel,
        "note": ("Synthetic AIS is generated around this run's own computed "
                 "origin and is labelled SYNTHETIC everywhere it appears."
                 if (suspects or {}).get("source") != "real" else None),
    }


def _attribution(suspects: Optional[dict]) -> Optional[dict]:
    if not suspects:
        return None
    return {
        "kind": "attribution", "title": "Attribution",
        "weights": suspects.get("weights"),
        "total_vessels_considered": suspects.get("total_vessels_considered"),
        "suspects": suspects.get("suspects", []),
        "filtered_out": suspects.get("filtered_out", []),
        "source": suspects.get("source"),
        "language_note": (
            "Scores rank how well a vessel's track matches the estimated "
            "origin in space, time and course. They are not probabilities of "
            "guilt, and the ordering is not a legal finding."),
    }


def _decisions(rows: List[dict]) -> Optional[dict]:
    if not rows:
        return None
    return {"kind": "decisions", "title": "Decisions", "entries": rows,
            "note": "A verdict records whether the ranking was sound and worth "
                    "pursuing -- never whether a named operator is at fault."}


def _limitations() -> Optional[dict]:
    """Parsed from docs/LIMITATIONS.md. Absent file means absent section."""
    if not LIMITATIONS_PATH.exists():
        return None
    text = LIMITATIONS_PATH.read_text(encoding="utf-8")
    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.*)", line)
        if heading:
            current = {"heading": heading.group(1).strip(), "items": []}
            sections.append(current)
            continue
        item = re.match(r"^\s*\d+\.\s+(.*)", line)
        if item and current is not None:
            current["items"].append(item.group(1).strip())
        elif current is not None and current["items"] and line.strip() \
                and not line.startswith("#"):
            current["items"][-1] += " " + line.strip()

    sections = [s for s in sections if s["items"]]
    if not sections:
        return None
    return {"kind": "limitations", "title": "Methodology and limitations",
            "source": "docs/LIMITATIONS.md", "sections": sections}


def _provenance_annex(run_dir: Path, manifest: dict) -> dict:
    """Per-artefact hashes, per-source provenance, code and model identity.

    The hashes are copied from the manifest rather than recomputed, so this
    table is exactly what `/verify` checks against. If the two ever disagreed
    the annex would be reassuring and wrong, which is the worst combination.
    """
    provider = _read_json(run_dir / "provider_status.json") or {}
    sources = []
    for name, block in (("scene", provider.get("scene")),
                        ("currents", provider.get("currents")),
                        ("wind", provider.get("wind")),
                        ("ais", provider.get("ais"))):
        if not block:
            continue
        sources.append({
            "layer": name,
            "provider": block.get("provider"),
            "data_source": block.get("data_source") or block.get("source"),
            "file": block.get("file"),
        })

    return {
        "kind": "provenance_annex", "title": "Data provenance annex",
        "run_id": manifest.get("run_id"),
        "artefact_digest": manifest.get("artefact_digest"),
        "code_git_sha": manifest.get("code_git_sha"),
        "generated_utc": manifest.get("generated_utc"),
        "artefacts": manifest.get("artefacts", []),
        "models": manifest.get("models", []),
        "attribution_profile": manifest.get("attribution_profile"),
        "sources": sources,
        "note": ("Hashes are copied from the sealed manifest, so this table is "
                 "what `/api/runs/{id}/verify` re-checks. A mismatch between "
                 "them would mean the annex was generated from something other "
                 "than the sealed run."),
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def compose(run_dir: Path, decisions: Optional[List[dict]] = None) -> Dict[str, Any]:
    """Build the report body for one sealed run.

    Raises FileNotFoundError when the run has no manifest: an unsealed run has
    no fixed content to report on, and composing one anyway would produce a
    document that changes under the reader.
    """
    run_dir = Path(run_dir)
    manifest = _read_json(run_dir / "manifest.json")
    if manifest is None:
        raise FileNotFoundError(
            f"{run_dir.name} has no manifest: the run is not sealed, so there "
            "is nothing fixed to report on")

    detect = _read_json(run_dir / "detect_response.json")
    slick = _read_json(run_dir / "slick.geojson")
    cloud = _read_json(run_dir / "origin_cloud.geojson")
    forecast = _read_json(run_dir / "forecast.geojson")
    suspects = _read_json(run_dir / "suspects.json")
    funnel = _read_json(run_dir / "funnel.json")

    sections = [
        _executive_summary(manifest, suspects, slick, cloud),
        _detection(manifest, detect),
        _characterisation(slick),
        _environment(cloud),
        _origin(cloud),
        _forecast(forecast),
        _ais(manifest, suspects, funnel),
        _attribution(suspects),
        _decisions(decisions or []),
        _limitations(),
        _provenance_annex(run_dir, manifest),
    ]
    rendered = [s for s in sections if s]

    return {
        "run_id": manifest.get("run_id"),
        "scene_id": manifest.get("scene_id"),
        "artefact_digest": manifest.get("artefact_digest"),
        "composed_utc": _utc_now(),
        "sections": rendered,
        # Stating what did NOT render is part of the report. A silently missing
        # section reads as "there was nothing to say" rather than "that stage
        # did not run".
        "omitted": [name for name, section in zip(
            ["executive_summary", "detection", "characterisation", "environment",
             "origin", "forecast", "ais", "attribution", "decisions",
             "limitations", "provenance_annex"], sections) if not section],
    }


def flatten_suspects_csv(body: Dict[str, Any]) -> List[List[Any]]:
    """Suspects and their evidence as CSV rows, header first."""
    header = ["run_id", "scene_id", "rank", "mmsi", "vessel_name", "vessel_type",
              "total_score", "source", "proximity", "temporal", "trajectory",
              "behaviour", "ais_gap", "vessel_prior", "closest_approach_km",
              "time_in_origin_window_min", "ais_gap_minutes", "course_delta_deg",
              "min_sog_kn", "track_points_in_cloud", "reason"]
    rows: List[List[Any]] = [header]

    attribution = next((s for s in body["sections"]
                        if s["kind"] == "attribution"), None)
    if attribution is None:
        return rows

    for suspect in attribution.get("suspects", []):
        scores = suspect.get("sub_scores", {})
        evidence = suspect.get("evidence", {})
        rows.append([
            body.get("run_id"), body.get("scene_id"),
            suspect.get("rank"), suspect.get("mmsi"),
            suspect.get("vessel_name"), suspect.get("vessel_type"),
            suspect.get("total_score"), suspect.get("source"),
            scores.get("proximity"), scores.get("temporal"),
            scores.get("trajectory"), scores.get("behaviour"),
            scores.get("ais_gap"), scores.get("vessel_prior"),
            evidence.get("closest_approach_km"),
            evidence.get("time_in_origin_window_min"),
            evidence.get("ais_gap_minutes"), evidence.get("course_delta_deg"),
            evidence.get("min_sog_kn"), evidence.get("track_points_in_cloud"),
            suspect.get("reason"),
        ])
    return rows
