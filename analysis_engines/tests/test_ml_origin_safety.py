"""Safety envelope for the origin-correction ML component, and the calibrated
origin-uncertainty heuristic that ships in its place.

The learned correction is DISABLED by default on evidence
(docs/qa/evidence/ml_hindcast/). These tests exist anyway, because a disabled component
that can be enabled must still be incapable of producing physically absurd output: an
origin is an accusation's starting point, and a model that can move it arbitrarily is a
liability whether or not it is currently switched on.
"""

from __future__ import annotations

import numpy as np
import pytest

from engines.drift.ml_origin import (
    MAX_CORRECTION_KM,
    N_FEATURES,
    OriginModel,
    build_features,
    correct_origin,
    load_model,
)
from engines.drift.runner import (
    ORIGIN_UNCERTAINTY_COVERAGE,
    ORIGIN_UNCERTAINTY_K,
    ORIGIN_UNCERTAINTY_M,
)


def _toy_model(scale: float = 1.0) -> OriginModel:
    """A deliberately over-confident model, used to prove the guard rails hold."""
    rng = np.random.default_rng(0)
    h = 4
    return OriginModel(
        w1=rng.normal(0, 1, (N_FEATURES, h)), b1=np.zeros(h),
        w2=np.full((h, 2), scale), b2=np.zeros(2),
        x_mean=np.zeros(N_FEATURES), x_std=np.ones(N_FEATURES),
        y_mean=np.zeros(2), y_std=np.full(2, scale),
        metadata={"model_version": "toy", "checkpoint_sha256": "0" * 64},
    )


def _feats(**kw):
    base = dict(
        backtrack_hours=24.0,
        detect_lons=np.array([80.40, 80.41, 80.42]),
        detect_lats=np.array([13.30, 13.31, 13.32]),
        origin_lons=np.array([80.35, 80.36, 80.37]),
        origin_lats=np.array([13.25, 13.26, 13.27]),
        mean_current_uv=(0.2, -0.1),
        mean_wind_uv=(3.0, 1.0),
    )
    base.update(kw)
    return build_features(**base)


# ------------------------------------------------------------------ shape / determinism


def test_feature_vector_has_the_declared_width():
    f = _feats()
    assert f.shape == (1, N_FEATURES)
    assert np.all(np.isfinite(f))


def test_inference_is_deterministic():
    m = _toy_model()
    f = _feats()
    a = correct_origin(m, 80.0, 13.0, f)
    b = correct_origin(m, 80.0, 13.0, f)
    assert a[0] == b[0] and a[1] == b[1]


def test_absent_model_is_a_no_op_not_an_error():
    lon, lat, prov = correct_origin(None, 80.0, 13.0, _feats())
    assert (lon, lat) == (80.0, 13.0)
    assert prov["applied"] is False
    assert "physics" in prov["reason"]


# ------------------------------------------------------------------ physical bounds


def test_correction_is_bounded_even_for_an_absurd_model():
    """A wildly over-confident model must not be able to move the origin far."""
    m = _toy_model(scale=1e6)
    lon0, lat0 = 80.0, 13.0
    lon, lat, prov = correct_origin(m, lon0, lat0, _feats())
    moved_km = np.hypot((lon - lon0) * 111.320 * np.cos(np.radians(lat0)),
                        (lat - lat0) * 110.574)
    assert moved_km <= MAX_CORRECTION_KM + 1e-6, moved_km
    assert prov["clipped_at_max_km"] is True


def test_output_coordinates_stay_on_the_planet():
    m = _toy_model(scale=1e9)
    for lon0, lat0 in [(179.9, 89.5), (-179.9, -89.5), (0.0, 0.0)]:
        lon, lat, _ = correct_origin(m, lon0, lat0, _feats())
        assert -180.0 <= lon <= 180.0
        assert -90.0 <= lat <= 90.0


def test_non_finite_prediction_falls_back_to_the_physics_origin():
    m = _toy_model()
    bad = OriginModel(
        w1=m.w1, b1=m.b1, w2=np.full_like(m.w2, np.nan), b2=m.b2,
        x_mean=m.x_mean, x_std=m.x_std, y_mean=m.y_mean, y_std=m.y_std,
        metadata=m.metadata,
    )
    lon, lat, prov = correct_origin(bad, 80.0, 13.0, _feats())
    assert (lon, lat) == (80.0, 13.0)
    assert prov["applied"] is False
    assert "non-finite" in prov["reason"]


def test_provenance_names_the_model_when_it_is_applied():
    lon, lat, prov = correct_origin(_toy_model(), 80.0, 13.0, _feats())
    assert prov["applied"] is True
    assert prov["model"] == "toy"
    assert "correction_km" in prov
    # the honesty fields must travel with the number
    assert "does_not_correct" in prov


# ------------------------------------------------------------------ shipped state


def test_shipped_model_is_absent_so_the_default_path_is_pure_physics():
    """R9 is PARTIAL by decision: no origin-correction model ships enabled.

    If a future model is added this test should be updated deliberately, together with
    the evidence that justifies enabling it.
    """
    assert load_model() is None


# ------------------------------------------------------------------ uncertainty rule


def test_origin_uncertainty_constants_match_the_committed_calibration():
    assert ORIGIN_UNCERTAINTY_K == pytest.approx(0.0907, abs=1e-4)
    assert ORIGIN_UNCERTAINTY_M == pytest.approx(1.75, abs=1e-6)
    assert 0.85 <= ORIGIN_UNCERTAINTY_COVERAGE <= 0.95


def test_origin_uncertainty_grows_with_cloud_spread():
    from engines.drift.runner import _origin_uncertainty_km

    class C:
        def __init__(self, s):
            r = np.random.default_rng(3)
            self.lons = 80.0 + r.normal(0, s, 500)
            self.lats = 13.0 + r.normal(0, s, 500)

    tight = _origin_uncertainty_km(C(0.005))
    wide = _origin_uncertainty_km(C(0.05))
    assert wide > tight > 0


def test_origin_uncertainty_is_zero_for_an_empty_cloud():
    from engines.drift.runner import _origin_uncertainty_km

    class C:
        lons = np.array([])
        lats = np.array([])

    assert _origin_uncertainty_km(C()) == 0.0
