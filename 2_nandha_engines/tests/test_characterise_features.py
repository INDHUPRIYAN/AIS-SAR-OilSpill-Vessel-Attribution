"""Engine A science tests: geometry, damping ratio, Fay age.

Ground truth comes from ``tests.fixtures.make_mask``, which returns the exact analytic
values it drew - no expected number is hard-coded here.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from skimage.measure import label as sk_label

from engines.characterise.age import FayParams, estimate_age, fay_age_hours
from engines.characterise.damping import compute_damping
from engines.characterise.features import extract_slicks
from tests.fixtures.make_mask import build_scene

# Anchored to this file so the suite passes from any working directory.
# These paths used to be CWD-relative, which meant the tests only ran
# when pytest happened to be invoked from 2_nandha_engines/.
MODULE_ROOT = Path(__file__).resolve().parents[1]



@pytest.fixture(scope="module")
def scene(tmp_path_factory) -> dict:
    """Generate the mock scene once per test session, into a temp dir."""
    out = tmp_path_factory.mktemp("scene")
    truth = build_scene(out)
    with rasterio.open(truth["mask_path"]) as src:
        truth["mask"] = src.read(1).astype(bool)
        truth["transform"] = src.transform
        truth["crs"] = src.crs
    with rasterio.open(truth["db_path"]) as src:
        truth["db"] = src.read(1)
    return truth


@pytest.fixture(scope="module")
def slicks(scene):
    found, warnings = extract_slicks(scene["mask"], scene["transform"])
    return found, warnings


# ------------------------------------------------------------------ geometry -------
def test_known_shape_recovers_drawn_ellipse(scene, slicks):
    """The must-pass test from handbook §8: a drawn ellipse returns its own geometry."""
    found, _ = slicks
    drawn = scene["slicks"][0]          # the 7.9 x 2.4 km main slick
    got = found[0]

    assert got.area_km2 == pytest.approx(drawn["area_km2"], rel=0.01)
    assert got.major_axis_km == pytest.approx(drawn["major_axis_km"], rel=0.02)
    assert got.minor_axis_km == pytest.approx(drawn["minor_axis_km"], rel=0.02)
    assert got.orientation_deg == pytest.approx(drawn["orientation_deg"], abs=1.0)

    # Centroid within a pixel of where it was drawn.
    assert got.centroid_lonlat[0] == pytest.approx(drawn["lon"], abs=scene["pixel_deg"])
    assert got.centroid_lonlat[1] == pytest.approx(drawn["lat"], abs=scene["pixel_deg"])


def test_perimeter_beats_the_raster_staircase(scene, slicks):
    """Simplification must remove the staircase inflation, not just soften it.

    An unsimplified raster boundary overshoots a smooth perimeter by ~30%; the
    simplified polygon lands within a few percent.
    """
    found, _ = slicks
    drawn = scene["slicks"][0]
    assert found[0].perimeter_km == pytest.approx(drawn["perimeter_km"], rel=0.05)

    raw, _ = extract_slicks(scene["mask"], scene["transform"], simplify_tolerance_px=0.0)
    assert raw[0].perimeter_km > drawn["perimeter_km"] * 1.25


def test_second_slick_is_measured_too(scene, slicks):
    found, _ = slicks
    drawn = scene["slicks"][1]
    got = found[1]
    assert got.area_km2 == pytest.approx(drawn["area_km2"], rel=0.02)
    assert got.major_axis_km == pytest.approx(drawn["major_axis_km"], rel=0.03)
    assert got.orientation_deg == pytest.approx(drawn["orientation_deg"], abs=2.0)


def test_speck_is_dropped_and_reported(slicks):
    """Three shapes are drawn; the sub-threshold speck must not reach the output."""
    found, warnings = slicks
    assert len(found) == 2
    assert any("speckle" in w for w in warnings)


def test_slicks_are_sorted_largest_first(slicks):
    found, _ = slicks
    assert [s.area_km2 for s in found] == sorted(
        (s.area_km2 for s in found), reverse=True
    )


def test_polygon_is_valid_closed_and_in_range(slicks):
    found, _ = slicks
    poly = found[0].polygon
    assert poly.is_valid and not poly.is_empty
    lons, lats = np.asarray(poly.exterior.coords).T
    assert lons.min() > -180 and lons.max() < 180
    assert lats.min() > -90 and lats.max() < 90
    assert poly.exterior.is_ring


def test_empty_mask_returns_nothing_without_crashing(scene):
    found, warnings = extract_slicks(np.zeros_like(scene["mask"]), scene["transform"])
    assert found == []


# ------------------------------------------------------------------- damping -------
def test_damping_recovers_the_drawn_contrast(scene, slicks):
    found, _ = slicks
    labelled = sk_label(scene["mask"], connectivity=2)
    component = labelled == found[0].label

    result, warnings = compute_damping(scene["db"], component, scene["mask"])
    assert result is not None
    assert result.damping_db == pytest.approx(scene["expected_damping_db"], abs=0.3)
    assert result.ring_pixels > 1000
    assert warnings == []


def test_damping_ring_excludes_other_slicks(scene, slicks):
    """The sea reference must not sample a neighbouring slick's dark pixels."""
    found, _ = slicks
    labelled = sk_label(scene["mask"], connectivity=2)
    component = labelled == found[0].label

    honest, _ = compute_damping(scene["db"], component, scene["mask"])
    # Pretending the other slicks are open sea lets their dark pixels into the ring,
    # which can only depress the sea reference and understate the damping.
    naive, _ = compute_damping(scene["db"], component, component)
    assert honest.damping_db >= naive.damping_db


def test_damping_returns_none_when_dB_is_unusable(scene, slicks):
    found, _ = slicks
    labelled = sk_label(scene["mask"], connectivity=2)
    component = labelled == found[0].label

    result, warnings = compute_damping(
        np.full_like(scene["db"], np.nan), component, scene["mask"]
    )
    assert result is None
    assert warnings and "damping ratio omitted" in warnings[0]


# ----------------------------------------------------------------------- age -------
def test_fay_age_scales_as_area_to_the_two_thirds():
    params = FayParams()
    assert fay_age_hours(4.0, params) / fay_age_hours(1.0, params) == pytest.approx(
        4.0 ** (2.0 / 3.0), rel=1e-9
    )


def test_thinner_assumed_slick_reads_as_older():
    """t ~ h^(-4/3): the assumption dominates, which is why confidence is 'low'."""
    thick = fay_age_hours(15.0, FayParams(assumed_thickness_m=1.0e-3))
    thin = fay_age_hours(15.0, FayParams(assumed_thickness_m=1.0e-4))
    assert thin / thick == pytest.approx(10.0 ** (4.0 / 3.0), rel=1e-9)


def test_demo_slick_lands_in_a_sane_range():
    """The 1 mm default must put a ~15 km2 slick at hours-to-a-couple-of-days."""
    hours = fay_age_hours(14.89, FayParams())
    assert 10.0 < hours < 72.0


def test_weak_damping_reads_as_older_than_strong_damping():
    params = FayParams()
    fresh, _ = estimate_age(15.0, damping_db=10.0, params=params)
    old, _ = estimate_age(15.0, damping_db=3.0, params=params)
    assert old.age_hours > fresh.age_hours


def test_method_reflects_whether_damping_was_available():
    with_damping, _ = estimate_age(15.0, damping_db=7.0)
    without, _ = estimate_age(15.0, damping_db=None)
    assert with_damping.method == "damping+fay"
    assert without.method == "fay"
    assert with_damping.confidence == "low" and without.confidence == "low"


def test_absurd_age_is_clamped_and_warned():
    """A 1 um assumption puts the demo slick at ~300000 h - clamp it, and say so."""
    params = FayParams(assumed_thickness_m=1.0e-6)
    estimate, warnings = estimate_age(14.89, damping_db=7.0, params=params)
    assert estimate.age_hours == params.max_age_hours
    assert warnings and "clamped" in warnings[0]


def test_params_load_from_config_file():
    import yaml

    cfg = yaml.safe_load((MODULE_ROOT / "config" / "characterise.yaml").read_text(encoding="utf-8"))
    params = FayParams.from_config(cfg["fay"])
    assert params.assumed_thickness_m == pytest.approx(1.0e-3)
    assert params.damping_factor_bounds == (0.5, 1.6)
