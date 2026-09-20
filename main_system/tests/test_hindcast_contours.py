"""The 50 % hindcast contour, from the engine to the published contract.

Engine B writes a nested 0.5 contour beside each step's 0.9 uncertainty
ellipse so the workspace can draw both (the contract's `confidence_level`
has always read "e.g. 0.5, 0.9"; only 0.9 was ever written). These tests hold
the two properties that make that safe:

* the published cloud carries BOTH levels and still validates against the
  frozen contract -- no field was added or renamed;
* the report's figures are still those of the uncertainty ellipse. A second,
  smaller ring per step must not shrink the "minimum semi-major axis" it states.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for root in (REPO, REPO / "main_system"):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from backend.services import report_compose  # noqa: E402
from backend.services.pipeline import normalise  # noqa: E402
from contracts.schemas.geo import OriginCloud  # noqa: E402

SCENE = {"scene_id": "S1A_TEST", "acquired_utc": "2023-01-08T00:10:00Z", "source": "real"}


def _ring(half: float) -> list:
    return [[[-90 - half, 28 - half], [-90 + half, 28 - half], [-90 + half, 28 + half],
             [-90 - half, 28 + half], [-90 - half, 28 - half]]]


def _ellipse(step_h: float, level: float, major: float, role: str | None) -> dict:
    props = {"kind": "confidence_ellipse", "level": level, "timestep_h": step_h,
             "time_utc": "2023-01-07T23:10:00Z", "semi_major_m": major,
             "semi_minor_m": major / 2, "orientation_deg": 40.0}
    if role:
        props["role"] = role
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": _ring(major / 1e5)},
            "properties": props}


def _native() -> dict:
    """An engine-native cloud: two steps, each with a 0.9 ellipse and a 0.5 contour."""
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-90.0, 28.0]},
         "properties": {"kind": "particle", "particle_id": i, "timestep_h": -float(h),
                        "time_utc": "2023-01-07T23:10:00Z", "weight": 0.5}}
        for h in (0, 1) for i in range(3)
    ]
    for h, major in ((0, 1000.0), (1, 3000.0)):
        features.append(_ellipse(-float(h), 0.9, major, None))
        features.append(_ellipse(-float(h), 0.5, major * 0.5487, "contour"))
    features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [-90.0, 28.0]},
                     "properties": {"kind": "origin_window", "start_utc": "2023-01-07T11:10:00Z",
                                    "end_utc": "2023-01-07T13:10:00Z", "peak_utc": "2023-01-07T12:10:00Z",
                                    "method": "cloud_convergence", "origin_uncertainty_km": 0.37}})
    return {"type": "FeatureCollection", "metadata": {}, "features": features}


def test_both_levels_are_published_and_the_contract_still_holds():
    published = normalise.normalise_origin_cloud(_native(), SCENE)
    OriginCloud.model_validate(published)          # frozen contract, unchanged

    ellipses = [f["properties"] for f in published["features"]
                if f["properties"]["feature_type"] == "ellipse"]
    assert sorted({e["confidence_level"] for e in ellipses}) == [0.5, 0.9]
    by_step: dict = {}
    for e in ellipses:
        by_step.setdefault(e["step_index"], {})[e["confidence_level"]] = e
    for step, levels in by_step.items():
        assert set(levels) == {0.5, 0.9}, step
        assert levels[0.5]["semi_major_m"] < levels[0.9]["semi_major_m"]
        # no property the contract does not define reached the published file
        assert set(levels[0.5]) == set(levels[0.9])
    # the run's own statement of uncertainty is untouched
    assert published["metadata"]["origin_uncertainty_km"] == 0.37


def test_the_report_still_states_the_uncertainty_ellipse():
    published = normalise.normalise_origin_cloud(_native(), SCENE)
    section = report_compose._origin(published)
    assert section["ellipse_semi_major_m"] == {"min": 1000.0, "max": 3000.0}

    # and a cloud from before the contour existed reads exactly as it did
    legacy = {**published, "features": [f for f in published["features"]
                                        if f["properties"].get("confidence_level") != 0.5]}
    assert report_compose._origin(legacy)["ellipse_semi_major_m"] == {"min": 1000.0, "max": 3000.0}
