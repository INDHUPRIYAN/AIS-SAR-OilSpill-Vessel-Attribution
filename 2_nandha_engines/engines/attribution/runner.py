"""Engine C orchestration: origin cloud + vessels -> ``suspects.json``.

Contract: handbook §4.4 (output) and §7 (CLI).

``NO_VESSELS_IN_WINDOW`` is split deliberately, because the handbook calls it "a valid,
expected outcome" while also requiring that filtered vessels stay in the output:

* **nothing in range at all** - no vessels in the file, or none with a single fix
  anywhere near the window - is the error. There is nothing to write.
* **vessels present but all filtered** - is a success. The file is written with every
  vessel marked filtered and an empty ranked list, plus a warning, so the UI can show
  "0 suspects, N vessels filtered out" and explain each one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common.errors import EngineError, missing_input, no_vessels_in_window
from ..common.io import read_json, read_yaml, require_file, write_json
from ..common.status import PRIMARY, Status
from ..common.timeutil import now_utc_str
from ..schemas.suspects import validate_suspects
from .explain import explain, explain_filtered
from .gates import GateConfig, apply_gates, build_origin_context
from .scoring import ScoringConfig, build_density, score_vessel
from .tracks import load_vessels

# Anchored to this file, not the process CWD. These engines are launched as
# subprocesses by the orchestrator and directly by tests, from several
# different working directories; a CWD-relative default silently resolves to
# nothing outside the module directory.
MODULE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS_PATH = MODULE_ROOT / "config" / "attribution_weights.yaml"
DEFAULT_INVESTIGATION_ID = "inv-001"


def _anything_in_range(tracks, origin, gate_config: GateConfig) -> bool:
    """Is any vessel transmitting anywhere near the origin window at all?"""
    buffer_s = gate_config.temporal_buffer_min * 60.0
    lo, hi = origin.start_s - buffer_s, origin.end_s + buffer_s
    return any(track.end_s >= lo and track.start_s <= hi for track in tracks)


def attribute(
    origin_path: str | Path,
    vessels_path: str | Path,
    out_path: str | Path,
    *,
    weights_path: str | Path = DEFAULT_WEIGHTS_PATH,
    investigation_id: str = DEFAULT_INVESTIGATION_ID,
    slick_path: str | Path | None = None,
) -> dict[str, Any]:
    """Rank vessels against an origin cloud. Returns the §4.5 status object."""
    status = Status(PRIMARY)
    try:
        origin_file = require_file(origin_path, what="origin_cloud.geojson")
        document = read_json(origin_file, what="origin_cloud.geojson")

        config: dict[str, Any] = {}
        if Path(weights_path).is_file():
            config = read_yaml(weights_path, what="attribution weights")
        else:
            status.warn(f"weights file {weights_path} not found; using built-in defaults")

        gate_config = GateConfig.from_config(config.get("gates"))
        scoring_config = ScoringConfig.from_config(config)
        weights, weight_warnings = scoring_config.normalised_weights()
        for warning in weight_warnings:
            status.warn(warning)

        # Engine A's measured orientation when it is on hand; otherwise the axis is
        # derived from the seeded particles (see gates.py).
        slick_axis = None
        if slick_path:
            slick_document = read_json(
                require_file(slick_path, what="slick.geojson"), what="slick.geojson"
            )
            features = slick_document.get("features") or []
            if features:
                slick_axis = (features[0].get("properties") or {}).get("orientation_deg")

        origin, origin_warnings = build_origin_context(
            document, gate_config, slick_axis_deg=slick_axis
        )
        for warning in origin_warnings:
            status.warn(warning)
        if origin.axis_source == "origin_cloud":
            status.warn(
                "the slick axis was derived from the origin cloud's seeded particles; "
                "pass --slick to use Engine A's measured orientation instead"
            )

        tracks, track_warnings = load_vessels(vessels_path)
        for warning in track_warnings:
            status.warn(warning)

        if not tracks:
            raise no_vessels_in_window(
                "vessels.parquet contains no usable vessel tracks",
                path=str(vessels_path),
            )
        if not _anything_in_range(tracks, origin, gate_config):
            raise no_vessels_in_window(
                f"none of the {len(tracks)} vessels transmit anywhere near the origin "
                "window",
                vessels=len(tracks),
            )

        density = build_density(document, origin)
        if density.kde is None:
            status.warn(
                "the origin cloud is too sparse for a density estimate; the proximity "
                "factor scores 0 for every vessel"
            )

        ranked: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []

        for track in sorted(tracks, key=lambda t: t.mmsi):
            gates = apply_gates(track, origin, gate_config)
            if not gates.passed:
                filtered.append(
                    {
                        "mmsi": track.mmsi,
                        "name": track.name,
                        "vessel_type": track.vessel_type,
                        "filtered": True,
                        "filter_reason": gates.filter_reason,
                        "reason": explain_filtered(gates.filter_reason, gates.metrics),
                        "failed_gates": gates.failed,
                    }
                )
                continue

            scores = score_vessel(track, gates, origin, density, scoring_config)
            ranked.append(
                {
                    "mmsi": track.mmsi,
                    "name": track.name,
                    "vessel_type": track.vessel_type,
                    "score_total": scores.total(weights),
                    "scores": scores.as_dict(),
                    "filtered": False,
                    "reason": explain(track, scores, origin),
                    "source": track.source,
                }
            )

        ranked.sort(key=lambda v: (-v["score_total"], v["mmsi"]))
        for position, vessel in enumerate(ranked, start=1):
            vessel["rank"] = position

        if not ranked:
            status.warn(
                f"all {len(filtered)} vessels were filtered out; the investigation has "
                "no suspects, and each vessel records why it was excluded"
            )

        # Carry the origin window through so the UI can caption the suspect list
        # without having to re-open origin_cloud.geojson.
        window = next(
            f["properties"] for f in document["features"]
            if (f.get("properties") or {}).get("kind") == "origin_window"
        )
        payload = {
            "investigation_id": investigation_id,
            "generated_utc": now_utc_str(),
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "origin_window": {
                "start_utc": window["start_utc"],
                "end_utc": window["end_utc"],
                "peak_utc": window["peak_utc"],
                "engine_used": window.get("engine_used"),
            },
            "vessels": [*ranked, *filtered],
        }

        validate_suspects(payload)
        status.add_output("suspects", str(write_json(out_path, payload)))
        return status.to_dict()

    except EngineError as err:
        return status.fail(err).to_dict()
    except ValueError as exc:
        # A malformed input file, not an absence of vessels - do not mislabel it.
        return status.fail(missing_input(str(exc))).to_dict()
