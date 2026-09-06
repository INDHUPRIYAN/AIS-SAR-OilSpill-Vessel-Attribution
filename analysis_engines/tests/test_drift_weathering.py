"""Weathering: the oil state must actually change with time, wind and oil type.

These tests exist because a weathering model that returns constants would satisfy a
schema check while telling an investigator nothing. Each test therefore asserts a
*direction of change*, not a hard-coded number, so the model can be recalibrated
without rewriting the suite.
"""

from __future__ import annotations

import json
import math

import pytest

from engines.drift.weathering import (
    DEFAULT_OIL_TYPE,
    OIL_TYPES,
    evaporated_fraction,
    mooney_viscosity,
    water_fraction,
    weather_at,
    weathering_series,
)


# --------------------------------------------------------------------------
# evaporation
# --------------------------------------------------------------------------


def test_no_weathering_at_time_zero():
    s = weather_at(0.0)
    assert s.evaporated_fraction == 0.0
    assert s.water_fraction == 0.0
    assert s.oil_remaining_fraction == 1.0


def test_evaporation_increases_with_time():
    series = [evaporated_fraction(h) for h in (1, 3, 6, 12, 24, 48)]
    assert all(b > a for a, b in zip(series, series[1:])), series
    # and it is a real loss, not a rounding artefact
    assert series[-1] > 0.05


def test_evaporation_is_bounded():
    """No oil may evaporate more than 100% - the empirical fit must be clamped."""
    for hours in (1e3, 1e5, 1e7):
        assert 0.0 <= evaporated_fraction(hours) <= 1.0


def test_evaporation_increases_with_temperature():
    cold = evaporated_fraction(12.0, temperature_c=5.0)
    warm = evaporated_fraction(12.0, temperature_c=30.0)
    assert warm > cold


def test_light_oil_evaporates_more_than_heavy():
    light = evaporated_fraction(24.0, oil_type="light_crude")
    heavy = evaporated_fraction(24.0, oil_type="heavy_crude")
    assert light > heavy


def test_unknown_oil_type_falls_back_to_default_not_crash():
    assert evaporated_fraction(12.0, oil_type="unobtanium") == pytest.approx(
        evaporated_fraction(12.0, oil_type=DEFAULT_OIL_TYPE)
    )


# --------------------------------------------------------------------------
# emulsification
# --------------------------------------------------------------------------


def test_emulsification_requires_evaporation_first():
    """A fresh oil is too fluid to hold water; uptake starts only after light-end loss."""
    spec = OIL_TYPES[DEFAULT_OIL_TYPE]
    # find an early time whose evaporation is still under the threshold
    early = 0.01
    assert evaporated_fraction(early) * 100.0 < spec["evap_threshold_pct"]
    assert water_fraction(early, wind_speed_m_s=10.0) == 0.0


def test_water_fraction_increases_with_time_and_saturates():
    series = [water_fraction(h, wind_speed_m_s=8.0) for h in (6, 12, 24, 48, 96)]
    assert all(b >= a for a, b in zip(series, series[1:])), series
    assert series[-1] <= OIL_TYPES[DEFAULT_OIL_TYPE]["max_water"] + 1e-9


def test_stronger_wind_emulsifies_faster():
    calm = water_fraction(12.0, wind_speed_m_s=1.0)
    blow = water_fraction(12.0, wind_speed_m_s=15.0)
    assert blow > calm


# --------------------------------------------------------------------------
# derived state
# --------------------------------------------------------------------------


def test_viscosity_rises_with_water_content():
    base = 50.0
    assert mooney_viscosity(base, 0.0) == pytest.approx(base)
    assert mooney_viscosity(base, 0.3) > base
    assert mooney_viscosity(base, 0.6) > mooney_viscosity(base, 0.3)


def test_emulsion_volume_exceeds_remaining_oil_once_water_is_taken_up():
    s = weather_at(24.0, wind_speed_m_s=10.0)
    assert s.water_fraction > 0.0
    assert s.emulsion_volume_factor > s.oil_remaining_fraction


def test_density_moves_toward_seawater_as_water_is_taken_up():
    dry = weather_at(0.0)
    wet = weather_at(24.0, wind_speed_m_s=10.0)
    assert wet.density_kg_m3 > dry.density_kg_m3
    assert wet.density_kg_m3 <= 1025.0


def test_state_changes_between_horizons():
    """The whole point: 6 h old oil is not the same substance as 24 h old oil."""
    a = weather_at(6.0, wind_speed_m_s=8.0)
    b = weather_at(24.0, wind_speed_m_s=8.0)
    assert b.evaporated_fraction > a.evaporated_fraction
    assert b.water_fraction > a.water_fraction
    assert b.viscosity_cst > a.viscosity_cst
    assert b.oil_remaining_fraction < a.oil_remaining_fraction


# --------------------------------------------------------------------------
# the reported series
# --------------------------------------------------------------------------


def test_series_is_json_serialisable_and_declares_its_assumptions():
    out = weathering_series([6, 12, 24], wind_speed_m_s=6.0)
    json.dumps(out)                                   # must survive the contract writer
    assert out["model"] == "fingas+mackay"
    assert out["confidence"] == "low"
    assert out["oil_type_assumed"] in OIL_TYPES
    # the limitations must travel with the numbers, not live only in a doc
    assert "dispersion" in out["processes_not_modelled"]
    assert out["references"]
    assert len(out["states"]) == 3


def test_series_states_are_monotonic_in_time():
    out = weathering_series([1, 6, 12, 24], wind_speed_m_s=7.0)
    ev = [s["evaporated_fraction"] for s in out["states"]]
    assert all(b >= a for a, b in zip(ev, ev[1:])), ev


def test_series_tolerates_an_unknown_oil_type():
    out = weathering_series([6], oil_type="not-an-oil")
    assert out["oil_type_assumed"] == DEFAULT_OIL_TYPE
