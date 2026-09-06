"""Sentinel-2 optical path: index behaviour and product search shape.

These tests do not need network access or a downloaded product: the index is pure
arithmetic on reflectance arrays, which is exactly the part that must be correct.
The live catalogue search and the real-product read are exercised separately and their
evidence lives in docs/qa/evidence/eo_optical/.
"""

from __future__ import annotations

import numpy as np
import pytest

from satellite.s2_adapter import (
    S2_L2A,
    Sentinel2Adapter,
    ndwi,
    optical_slick_index,
    threshold_index,
)


# --------------------------------------------------------------------------
# water masking
# --------------------------------------------------------------------------


def test_ndwi_separates_water_from_land():
    # water: green high, NIR low.  land/vegetation: NIR high, green low.
    water = ndwi(np.array([0.08]), np.array([0.01]))
    land = ndwi(np.array([0.05]), np.array([0.35]))
    assert water[0] > 0
    assert land[0] < 0


def test_ndwi_is_safe_when_both_bands_are_zero():
    out = ndwi(np.zeros(4), np.zeros(4))
    assert np.all(np.isfinite(out))


# --------------------------------------------------------------------------
# the slick index
# --------------------------------------------------------------------------


def _scene(with_slick: bool, noise: float = 0.0015, seed: int = 7):
    """Small synthetic reflectance scene: dark water, optional slick patch.

    Sensor noise is included deliberately. A perfectly uniform scene is not a fair
    test: its median absolute deviation is exactly zero, which is a degenerate case no
    real acquisition produces.
    """
    rng = np.random.default_rng(seed)
    h = w = 64
    green = np.full((h, w), 0.060, dtype=np.float32) + rng.normal(0, noise, (h, w))
    red = np.full((h, w), 0.040, dtype=np.float32) + rng.normal(0, noise, (h, w))
    nir = np.full((h, w), 0.012, dtype=np.float32) + rng.normal(0, noise / 3, (h, w))
    swir = np.full((h, w), 0.004, dtype=np.float32) + rng.normal(0, noise / 3, (h, w))
    if with_slick:
        # An oil film flattens the visible slope: red rises toward green.
        red[20:40, 20:40] += 0.018
    return (green.astype(np.float32), red.astype(np.float32),
            nir.astype(np.float32), swir.astype(np.float32))


def test_water_mask_covers_an_all_water_scene():
    g, r, n, s = _scene(False)
    _, water = optical_slick_index(g, r, n, s)
    assert water.mean() > 0.99


def test_index_is_elevated_inside_a_slick_patch():
    g, r, n, s = _scene(True)
    idx, water = optical_slick_index(g, r, n, s)
    inside = idx[25:35, 25:35]
    outside = np.concatenate([idx[0:10, 0:10].ravel(), idx[50:60, 50:60].ravel()])
    assert np.nanmean(inside) > np.nanmean(outside)


def test_clean_water_yields_no_detection():
    """The whole point of the MAD threshold: featureless water must stay empty."""
    g, r, n, s = _scene(False)
    idx, water = optical_slick_index(g, r, n, s)
    assert threshold_index(idx, water, k=3.0).sum() == 0


def test_slick_patch_is_detected_and_roughly_the_right_size():
    g, r, n, s = _scene(True)
    idx, water = optical_slick_index(g, r, n, s)
    mask = threshold_index(idx, water, k=2.0)
    assert mask.sum() > 0
    # the planted patch is 20x20 = 400 px; allow generous slack either way
    assert 100 <= int(mask.sum()) <= 900
    assert mask[25:35, 25:35].mean() > 0.5      # concentrated where it was planted


def test_bright_swir_is_excluded_as_cloud_or_land():
    g, r, n, s = _scene(True)
    s = np.full_like(s, 0.40)                    # everything now looks like cloud
    _, water = optical_slick_index(g, r, n, s)
    assert water.sum() == 0


def test_threshold_needs_enough_water_pixels():
    idx = np.full((4, 4), 0.5, dtype=np.float32)
    water = np.ones((4, 4), dtype=bool)
    assert threshold_index(idx, water).sum() == 0        # 16 px, under the floor


def test_index_is_finite_where_water_and_nan_elsewhere():
    g, r, n, s = _scene(True)
    g[0:5, :] = 0.02
    n[0:5, :] = 0.40                             # land strip
    idx, water = optical_slick_index(g, r, n, s)
    assert np.all(np.isfinite(idx[water]))
    assert np.all(np.isnan(idx[~water]))


# --------------------------------------------------------------------------
# catalogue query construction (no network)
# --------------------------------------------------------------------------


def test_bbox_polygon_is_a_closed_wgs84_ring():
    poly = Sentinel2Adapter._bbox_polygon([-91.0, 28.4, -90.0, 29.2])
    assert poly.startswith("POLYGON((") and poly.endswith("))")
    assert poly.count(",") == 4                  # five vertices, first repeated last
    assert "-91.0 28.4" in poly


def test_l2a_product_type_constant():
    assert S2_L2A == "S2MSI2A"
