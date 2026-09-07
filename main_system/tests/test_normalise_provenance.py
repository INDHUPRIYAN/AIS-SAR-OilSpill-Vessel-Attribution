"""Normalisation must not quietly discard what the engines measured.

Four fields were computed by the engines and then dropped at the contract
boundary, each turning a qualified result into an unqualified-looking one:

  H-06  ellipse semi-axes  -> zero-filled, so every published confidence
                              ellipse had zero radius and the map drew
                              certainty the hindcast never claimed;
  H-11  forcing block      -> reduced to two filenames, hiding which provider
                              served the physics and whether it was a fallback;
  C-05  age_method         -> dropped, so a Fay inversion of an *assumed*
        C-07  and the categorical 'low'  read as a measurement;
  F1    weathering         -> dropped entirely, discarding the fate model's own
                              assumptions and its fixed 'low' confidence.

The tests below work from the real engine-native artefacts of a sealed run, so
they check the actual translation rather than a hand-built fixture.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))
sys.path.insert(0, str(REPO_ROOT / "analysis_engines"))

RUN = REPO_ROOT / "data" / "runs" / "inv-final-audit"
NATIVE = RUN / "engine_native"

from backend.services.pipeline import normalise  # noqa: E402


def _native(name: str) -> dict:
    path = NATIVE / name
    if not path.exists():
        pytest.skip(f"engine-native artefact absent: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scene_meta() -> dict:
    p = RUN / "scene_meta.json"
    if not p.exists():
        pytest.skip("reference run not present in this checkout")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# H-06 — ellipse axes
# --------------------------------------------------------------------------

def test_engine_emits_real_ellipse_axes():
    """Engine B computes the covariance already; it now publishes the axes."""
    from engines.drift.cloud import ellipse_axes_m

    rng = np.random.default_rng(1337)
    lons = 114.3 + rng.normal(0, 0.02, 400)
    lats = -8.8 + rng.normal(0, 0.005, 400)

    axes = ellipse_axes_m(lons, lats, 0.9)
    assert axes is not None
    major, minor, bearing = axes
    assert major > 0 and minor > 0, "a real cloud must have real axes"
    assert major >= minor, "major axis must be the larger one"
    assert 0 <= bearing < 180, "bearing folds to the contract's [0, 180)"
    # Spread is ~4x wider in longitude than latitude, so the ellipse must be
    # markedly elongated -- a circle here would mean the covariance was ignored.
    assert major > 2 * minor


def test_ellipse_axes_absent_rather_than_zero_when_unfittable():
    """A degenerate cloud has no ellipse. Returning zeros would publish
    certainty; returning None lets the caller drop the feature."""
    from engines.drift.cloud import ellipse_axes_m

    assert ellipse_axes_m(np.array([1.0]), np.array([2.0])) is None
    assert ellipse_axes_m(np.array([1.0, 1.0]), np.array([2.0, 2.0])) is None


def test_axes_describe_the_same_ellipse_as_the_drawn_ring():
    """Ring and axes come from one covariance solve; if they ever diverge the
    map and the numbers would disagree about the same object."""
    from engines.drift.cloud import confidence_ellipse, ellipse_axes_m
    from engines.common.geo import LocalFrame

    rng = np.random.default_rng(7)
    lons = 114.3 + rng.normal(0, 0.03, 500)
    lats = -8.8 + rng.normal(0, 0.01, 500)

    ring = confidence_ellipse(lons, lats, 0.9)
    major, _minor, _b = ellipse_axes_m(lons, lats, 0.9)

    frame = LocalFrame(float(np.mean(lats)), float(np.mean(lons)))
    rx, ry = frame.to_metres(np.array([p[0] for p in ring]),
                             np.array([p[1] for p in ring]))
    reach = float(np.max(np.hypot(rx - rx.mean(), ry - ry.mean())))
    assert abs(reach - major) / major < 0.05, "ring extent must match semi_major_m"


def test_normalise_refuses_to_zero_fill_partial_axes():
    """The specific failure mode of H-06: a missing number became 0.0."""
    with pytest.raises(normalise.MissingEllipseAxes):
        normalise._require_axes({"semi_major_m": 100.0}, step_index=3)
    with pytest.raises(normalise.MissingEllipseAxes):
        normalise._require_axes({}, step_index=0)

    ok = normalise._require_axes(
        {"semi_major_m": 10.0, "semi_minor_m": 4.0, "orientation_deg": 190.0}, 0)
    assert ok["semi_major_m"] == 10.0
    assert ok["orientation_deg"] == 10.0, "bearing folds into [0, 180)"


def test_published_ellipses_have_no_zero_radius(scene_meta):
    """End-to-end over the real artefact: every published ellipse is real."""
    payload = _native("origin_cloud.geojson")
    if not any(f["properties"].get("semi_major_m") is not None
               for f in payload["features"]
               if f["properties"].get("kind") == "confidence_ellipse"):
        pytest.skip("reference artefact predates the engine axis fix")

    out = normalise.normalise_origin_cloud(payload, scene_meta, forcing={})
    ellipses = [f for f in out["features"]
                if f["properties"]["feature_type"] == "ellipse"]
    assert ellipses, "the run produced confidence ellipses"
    assert all(f["properties"]["semi_major_m"] > 0 for f in ellipses)


# --------------------------------------------------------------------------
# H-11 — forcing provenance
# --------------------------------------------------------------------------

def test_forcing_merge_keeps_both_halves():
    """Engine structure + run identity. Replacing one with the other is what
    reduced the block to two filenames."""
    engine = {"currents": {"provider": "currents.nc", "variables": ["uo", "vo"],
                           "fallback": None},
              "windage": 0.03,
              "ml_residual": {"model": None, "applied": False}}
    run = {"currents": {"file": "currents.nc", "provider": "CMEMS"},
           "engine": "euler", "hours": 24}

    merged = normalise._merge_forcing(engine, run)
    assert merged["currents"]["variables"] == ["uo", "vo"]   # engine's
    assert merged["currents"]["provider"] == "CMEMS"         # run's, wins
    assert merged["windage"] == 0.03
    assert merged["ml_residual"]["applied"] is False, "the ML disclaimer must survive"
    assert merged["engine"] == "euler"


def test_forcing_merge_invents_nothing():
    assert normalise._merge_forcing(None, None) == {}
    assert "wind" not in normalise._merge_forcing({}, {"wind": None})


def test_forcing_provenance_reads_the_grid_itself():
    """Provider identity comes from the file's own global attributes, so a
    fallback cannot be published as though it were the primary."""
    from backend.services.pipeline.run import forcing_provenance

    grids = sorted((REPO_ROOT / "data" / "metocean").glob("*/wind.nc"))
    if not grids:
        pytest.skip("no normalised forcing grids in this checkout")

    block = forcing_provenance(grids[0])
    assert block["file"] == "wind.nc"
    if "provider" in block:
        assert block["provider"], "provider must be a real string when present"
    assert forcing_provenance(None) is None


# --------------------------------------------------------------------------
# C-05 / C-07 — age qualification
# --------------------------------------------------------------------------

def test_age_method_and_label_survive_normalisation(scene_meta):
    payload = _native("slick.geojson")
    detect_path = RUN / "detect_response.json"
    detect = json.loads(detect_path.read_text(encoding="utf-8")) if detect_path.exists() else {}

    out = normalise.normalise_slick(payload, scene_meta, detect)
    props = out["features"][0]["properties"]

    assert props["age_method"] == "damping+fay"
    assert props["age_confidence_label"] == "low"
    # The numeric score stays for anything that ranks on it.
    assert props["age_confidence"] == 0.25


def test_age_label_maps_scores_back_to_bands():
    assert normalise._age_label("low") == "low"
    assert normalise._age_label("LOW") == "low"
    assert normalise._age_label(0.25) == "low"
    assert normalise._age_label(0.5) == "medium"
    assert normalise._age_label(0.9) == "high"
    assert normalise._age_label(None) is None
    assert normalise._age_label("nonsense") is None


# --------------------------------------------------------------------------
# F1 — weathering assumptions
# --------------------------------------------------------------------------

def test_weathering_reaches_the_published_forecast(scene_meta):
    """The fate model states assumed oil type, assumed temperature, a fixed
    'low' confidence and what it does NOT model. All of it was dropped."""
    payload = _native("forecast.geojson")
    if "weathering" not in (payload.get("metadata") or {}):
        pytest.skip("reference artefact carries no weathering block")

    out = normalise.normalise_forecast(payload, scene_meta, forcing={})
    w = out["metadata"].get("weathering")
    assert w, "weathering must survive normalisation"
    assert w["oil_type_assumed"], "the assumed oil type is the point"
    assert w["confidence"] == "low"
    assert w["processes_not_modelled"], "what it cannot model must stay visible"


def test_weathering_absent_when_the_engine_did_not_run_it(scene_meta):
    """Never synthesised: a forecast without weathering says nothing about it."""
    out = normalise.normalise_forecast(
        {"features": [], "metadata": {"forcing": {}}}, scene_meta, forcing={})
    assert "weathering" not in out["metadata"]


# --------------------------------------------------------------------------
# the contract still accepts everything, old and new
# --------------------------------------------------------------------------

def test_new_fields_are_additive_and_optional():
    """Existing sealed artefacts must keep validating -- the additions carry
    defaults so nothing produced before this change becomes invalid."""
    sys.path.insert(0, str(REPO_ROOT))
    from contracts.schemas.geo import ForecastMetadata, OriginMetadata, SlickProperties

    origin = OriginMetadata(
        scene_id="S1", origin_window_start_utc="2023-05-04T17:52:00Z",
        origin_window_end_utc="2023-05-04T21:52:00Z",
        backtrack_hours=24, n_particles=500, timestep_minutes=60)
    assert origin.origin_uncertainty_km is None

    fc = ForecastMetadata(scene_id="S1", issued_utc="2023-05-04T21:52:00Z",
                          horizons_h=[6, 12, 24])
    assert fc.weathering is None

    slick = SlickProperties(
        slick_id="s1", confidence=0.9, area_km2=1.0, perimeter_km=1.0,
        centroid=[114.3, -8.8], major_axis_m=100.0, minor_axis_m=10.0,
        orientation_deg=100.0)
    assert slick.age_method is None and slick.age_confidence_label is None


def test_sealed_artefacts_still_validate(scene_meta):
    """The strongest backward-compatibility check available: re-validate what
    a previous release actually wrote to disk."""
    sys.path.insert(0, str(REPO_ROOT))
    from contracts.schemas.geo import OriginCloud, SlickCollection

    for name, model in (("slick.geojson", SlickCollection),
                        ("origin_cloud.geojson", OriginCloud)):
        path = RUN / name
        if not path.exists():
            continue
        model.model_validate(json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# The exclusion ledger must satisfy the contract it is written into
# --------------------------------------------------------------------------
#
# `normalise` writes `filter_reason` and `failed_gates` onto every filtered_out
# entry so the funnel can name the gate that excluded a vessel instead of
# pattern-matching English prose (audit H5). `FilteredVessel` forbade extra
# fields, so any run that actually filtered a vessel produced a suspects.json
# that its own validator rejected -- and the pipeline duly marked a completed
# attribution stage FAILED. It went unnoticed because every earlier run had an
# empty `filtered_out`; the first real-AIS run filtered 29 vessels and the
# stage failed on a CONTRACT VIOLATION while holding four ranked suspects.


def _report(filtered):
    return {
        "scene_id": "S1A_TEST",
        "run_id": "inv-test",
        "generated_utc": "2023-01-08T00:10:08Z",
        "weights": {"proximity": 0.30, "temporal": 0.20, "trajectory": 0.20,
                    "behaviour": 0.10, "ais_gap": 0.15, "vessel_prior": 0.05},
        "suspects": [],
        "filtered_out": filtered,
        "total_vessels_considered": len(filtered),
        "source": "real",
    }


def test_the_exclusion_ledger_validates():
    from contracts.schemas.tabular import SuspectsReport

    report = SuspectsReport.model_validate(_report([{
        "mmsi": 205125000,
        "reason": "Filtered out: closest approach was 13.563 km from the origin region.",
        "filter_reason": "outside origin region",
        "failed_gates": ["outside origin region",
                         "course incompatible with slick axis"],
    }]))
    entry = report.filtered_out[0]
    assert entry.filter_reason == "outside origin region"
    assert len(entry.failed_gates) == 2


def test_the_ledger_fields_stay_optional():
    """Runs sealed before the ledger existed carry only `reason`, and must
    still load -- otherwise a schema addition silently invalidates history."""
    from contracts.schemas.tabular import SuspectsReport

    report = SuspectsReport.model_validate(_report([
        {"mmsi": 205125000, "reason": "Filtered out: outside the time window."}]))
    entry = report.filtered_out[0]
    assert entry.filter_reason is None
    assert entry.failed_gates == []


def test_what_normalise_emits_is_what_the_contract_accepts():
    """Pins the two together. The defect was that they disagreed, and nothing
    compared them until a real run did."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(REPO_ROOT / "main_system"))
    from backend.services.pipeline import normalise

    src = inspect_source = Path(normalise.__file__).read_text(encoding="utf-8")
    assert '"filter_reason"' in src and '"failed_gates"' in src, \
        "normalise no longer emits the ledger; this test is stale"

    from contracts.schemas.tabular import FilteredVessel

    emitted = {"mmsi", "reason", "filter_reason", "failed_gates"}
    declared = set(FilteredVessel.model_fields)
    assert emitted <= declared, (
        f"normalise writes {sorted(emitted - declared)} which the contract does "
        "not declare; extend the contract rather than dropping the field")


# --------------------------------------------------------------------------
# How the origin window was derived must survive into the published cloud
# --------------------------------------------------------------------------
#
# `origin_window()` reports one of three methods. "cloud_convergence" means the
# backtracked cloud had a genuine spread minimum and the window localises a
# release. "age_estimate" and "midpoint" mean the current field did not deform
# the cloud at all -- there is no convergence to find, the whole run is reported
# as the window, and the peak is a placeholder carrying no information.
#
# Normalisation lifted start and end out of the engine's origin_window feature
# and dropped the feature, so the published artefact showed a window without
# saying which of those two very different things it was. A UI reading it could
# present "release localised to 10:10-00:10" for a run that had actually
# reported it could not localise anything.


def _cloud(method, peak="2023-01-08T00:10:08Z"):
    return {
        "type": "FeatureCollection",
        "metadata": {"forcing": {}},
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-91.0, 28.5]},
             "properties": {"kind": "origin_window", "method": method,
                            "start_utc": "2023-01-07T10:10:08Z",
                            "end_utc": "2023-01-08T00:10:08Z",
                            "peak_utc": peak,
                            "origin_uncertainty_km": 0.3658}},
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-91.0, 28.5]},
             "properties": {"step_index": 0, "t_utc": "2023-01-08T00:10:08Z"}},
        ],
    }


@pytest.mark.parametrize("method", ["cloud_convergence", "age_estimate", "midpoint"])
def test_the_derivation_method_reaches_the_published_cloud(method, tmp_path):
    import json

    import sys
    sys.path.insert(0, str(REPO_ROOT / "main_system"))
    from backend.services.pipeline import normalise

    path = tmp_path / "origin_cloud.geojson"
    path.write_text(json.dumps(_cloud(method)), encoding="utf-8")
    normalise.normalise_file("origin_cloud", path,
                             scene_meta={"scene_id": "S1A_TEST",
                                         "acquired_utc": "2023-01-08T00:10:08Z"})
    md = json.loads(path.read_text(encoding="utf-8"))["metadata"]

    assert md["origin_window_method"] == method, (
        "the published cloud does not say how its window was derived; a window "
        "from 'midpoint' carries no information and must not read like one from "
        "'cloud_convergence'")
    assert md["origin_peak_utc"] == "2023-01-08T00:10:08Z"
    assert md["origin_window_start_utc"] == "2023-01-07T10:10:08Z"
    assert md["origin_window_end_utc"] == "2023-01-08T00:10:08Z"


def test_every_field_normalise_publishes_is_declared_by_a_contract():
    """The failure mode this closes, twice over.

    Both `filter_reason`/`failed_gates` on suspects and
    `origin_window_method`/`origin_peak_utc` on the origin cloud were added to
    the published artefacts without being added to the schemas those artefacts
    are validated against. In both cases the pipeline ran the stage
    successfully, wrote a correct file, and then marked the stage FAILED on its
    own contract check -- so a completed attribution holding four ranked
    suspects was reported as a failure. Neither showed up in the suite because
    no test compared what normalisation writes with what the contract permits.
    """
    from contracts.schemas.geo import OriginMetadata
    from contracts.schemas.tabular import FilteredVessel

    # Fields normalisation is known to publish. Extend this list when
    # normalisation learns a new one -- that is the point of the test.
    published = {
        OriginMetadata: {
            "scene_id", "origin_window_start_utc", "origin_window_end_utc",
            "backtrack_hours", "n_particles", "timestep_minutes", "forcing",
            "origin_window_method", "origin_peak_utc",
            "origin_uncertainty_km", "origin_uncertainty_coverage",
            "origin_uncertainty_method", "source", "crs",
        },
        FilteredVessel: {"mmsi", "reason", "filter_reason", "failed_gates"},
    }
    for model, fields in published.items():
        declared = set(model.model_fields)
        missing = fields - declared
        assert not missing, (
            f"{model.__name__} does not declare {sorted(missing)}, which "
            "normalisation writes. The stage will run, produce a correct file, "
            "and then be marked FAILED by its own contract check.")


def test_the_contracts_forbid_extras_so_this_matters():
    """If the models ever allowed extras the tests above would pass vacuously."""
    from contracts.schemas.geo import OriginMetadata
    from contracts.schemas.tabular import FilteredVessel

    for model in (OriginMetadata, FilteredVessel):
        assert model.model_config.get("extra") == "forbid", (
            f"{model.__name__} no longer forbids extra fields; the contract "
            "has stopped being a contract")
