"""Tests for the shared layer: geodesy, UTC handling, error taxonomy, status object."""

from __future__ import annotations

import math

import numpy as np
import pytest

from engines.common import (
    EMPTY_MASK,
    EngineError,
    Status,
    empty_mask,
    format_utc,
    parse_utc,
)
from engines.common.geo import (
    LocalFrame,
    axis_bearing_deg,
    bearing_deg,
    covariance_ellipse,
    deg_lat_to_km,
    deg_lon_to_km,
    km_to_deg_lat,
    km_to_deg_lon,
    m_per_deg_lat,
    m_per_deg_lon,
    pixel_area_m2,
    polyline_length_m,
)


# --------------------------------------------------------------------- geodesy ------
def test_degree_lengths_shrink_with_latitude():
    """A degree of longitude collapses toward the pole; latitude barely moves."""
    assert m_per_deg_lon(0.0) == pytest.approx(111320, rel=1e-3)
    # ~cos(lat), give or take the higher-order terms of the series.
    assert m_per_deg_lon(60.0) == pytest.approx(m_per_deg_lon(0.0) * 0.5, rel=5e-3)
    assert m_per_deg_lat(0.0) == pytest.approx(110574, rel=1e-3)
    assert m_per_deg_lat(90.0) == pytest.approx(111694, rel=1e-3)


def test_km_deg_roundtrip_at_demo_latitude():
    lat = 13.052
    assert deg_lon_to_km(km_to_deg_lon(10.0, lat), lat) == pytest.approx(10.0, rel=1e-12)
    assert deg_lat_to_km(km_to_deg_lat(10.0, lat), lat) == pytest.approx(10.0, rel=1e-12)
    # 10 km spans more degrees of longitude than of latitude away from the equator.
    assert km_to_deg_lon(10.0, lat) > km_to_deg_lat(10.0, lat)

    frame = LocalFrame(lat, 80.312)
    x, y = frame.to_metres(80.412, 13.152)
    lon, lat_back = frame.to_lonlat(x, y)
    assert float(lon) == pytest.approx(80.412, abs=1e-9)
    assert float(lat_back) == pytest.approx(13.152, abs=1e-9)


def test_pixel_area_uses_latitude():
    """Identical degree steps cover less ground the further north you go."""
    equator = pixel_area_m2(0.0001, 0.0001, 0.0)
    north = pixel_area_m2(0.0001, 0.0001, 60.0)
    assert north < equator * 0.55


# ----------------------------------------------------------------- orientation ------
def test_bearing_convention_is_clockwise_from_north():
    assert bearing_deg(0, 1) == pytest.approx(0.0)      # north
    assert bearing_deg(1, 1) == pytest.approx(45.0)     # north-east
    assert bearing_deg(1, 0) == pytest.approx(90.0)     # east
    assert bearing_deg(0, -1) == pytest.approx(180.0)   # south
    assert bearing_deg(-1, 0) == pytest.approx(270.0)   # west


def test_axis_bearing_folds_opposite_directions_together():
    """An ellipse axis is undirected: NE and SW must report the same orientation."""
    assert axis_bearing_deg(1, 1) == pytest.approx(45.0)
    assert axis_bearing_deg(-1, -1) == pytest.approx(45.0)
    assert axis_bearing_deg(0, -1) == pytest.approx(0.0)


@pytest.mark.parametrize("orientation", [0.0, 30.0, 62.0, 90.0, 155.0])
def test_covariance_ellipse_recovers_a_drawn_ellipse(orientation):
    """Fill an ellipse with points; the fit must return its own axes and bearing."""
    a, b = 4000.0, 1200.0                     # semi-axes, metres
    t = math.radians(orientation)
    u = np.array([math.sin(t), math.cos(t)])  # major axis, (east, north)
    v = np.array([math.cos(t), -math.sin(t)])

    grid = np.linspace(-1.0, 1.0, 220)
    gx, gy = np.meshgrid(grid, grid)
    inside = gx**2 + gy**2 <= 1.0
    along, across = gx[inside] * a, gy[inside] * b
    x = along * u[0] + across * v[0]
    y = along * u[1] + across * v[1]

    major, minor, bearing = covariance_ellipse(x, y)
    assert major == pytest.approx(2 * a, rel=0.02)
    assert minor == pytest.approx(2 * b, rel=0.02)
    assert bearing == pytest.approx(orientation, abs=0.5)


def test_polyline_length():
    assert polyline_length_m([(0, 0), (3, 4), (3, 4)]) == pytest.approx(5.0)


# ----------------------------------------------------------------------- UTC --------
def test_parse_utc_accepts_z_and_offsets():
    assert format_utc(parse_utc("2017-02-02T00:39:42Z")) == "2017-02-02T00:39:42Z"
    # IST input must be converted, not silently kept.
    assert format_utc(parse_utc("2017-02-02T06:09:42+05:30")) == "2017-02-02T00:39:42Z"


def test_parse_utc_rejects_naive_timestamps():
    """The handbook's named trap: a naive local time must never be assumed to be UTC."""
    with pytest.raises(ValueError, match="no timezone"):
        parse_utc("2017-02-02T00:39:42")


# ------------------------------------------------------------ errors + status -------
def test_error_class_must_be_declared():
    with pytest.raises(ValueError):
        EngineError("SOMETHING_ELSE", "not in the taxonomy")


def test_status_success_and_failure_shapes():
    ok = Status()
    ok.warn("no dB band supplied; damping ratio omitted")
    ok.warn("no dB band supplied; damping ratio omitted")   # deduplicated
    ok.add_output("slick", "out/slick.geojson")
    payload = ok.to_dict()
    assert payload["ok"] is True
    assert payload["engine_used"] == "primary"
    assert payload["warnings"] == ["no dB band supplied; damping ratio omitted"]
    assert payload["outputs"]["slick"] == "out/slick.geojson"

    bad = Status().fail(empty_mask("mask contains no oil pixels", path="mask.tif")).to_dict()
    assert bad["ok"] is False
    assert bad["error"]["error_class"] == EMPTY_MASK
    assert bad["error"]["detail"]["path"] == "mask.tif"
