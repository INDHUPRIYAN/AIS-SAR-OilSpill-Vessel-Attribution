"""The hindcast selection, and the three reasons physics could be primary.

The problem statement asks for an ML hindcast candidate benchmarked against
the physics one, with the winner made primary, and says "Never fabricate ML
superiority". That prohibition cuts both ways: reporting physics as the
benchmark winner when no benchmark has been run is the same offence, and it is
the one a demo is actually tempted by.

So the assertions here are about `selection_basis`, not about which engine
wins. Physics being primary means three different things:

    measured             a benchmark ran and physics won
    untested_candidate   ML is trained, nothing has been compared
    candidate_untrained  ML has no weights and has never predicted anything

and the API must say which.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("hb_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'hb.db').as_posix()}"
    os.environ["SECRET_KEY"] = "h" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        db.add(User(email="analyst@example.invalid",
                    password_hash=security.hash_password("hb-suite-password"),
                    role="analyst", active=True))
        db.commit()

    with TestClient(app, base_url="https://testserver") as client:
        client.post("/api/auth/login",
                    json={"email": "analyst@example.invalid",
                          "password": "hb-suite-password"})
        yield client

    os.environ.pop("DATABASE_URL", None)


def test_physics_is_primary_and_no_benchmark_is_claimed(env):
    body = env.get("/api/models/hindcast").json()
    assert body["primary"] == "physics"
    # The headline assertion of this file.
    assert body["benchmark_run"] is False
    assert body["selection_basis"] in ("candidate_untrained",
                                       "untested_candidate")
    # Whichever of the two default cases applies, the detail must say the
    # comparison has not happened rather than imply a result.
    detail = body["selection_detail"]
    assert ("has not been run" in detail
            or "never been measured" in detail), detail


def test_the_selection_detail_never_claims_physics_won_an_unrun_benchmark(env):
    body = env.get("/api/models/hindcast").json()
    detail = body["selection_detail"].lower()
    for forbidden in ("physics outperformed", "physics won",
                      "physics is more accurate than the ml"):
        assert forbidden not in detail, (
            f"the selection detail claims {forbidden!r} for a benchmark that "
            f"was never run")


def test_the_v1_loss_is_reported_as_a_real_measured_result(env):
    """v1 IS a completed benchmark with a real loser, and its numbers are
    measured. That must not be softened into "experimental"."""
    body = env.get("/api/models/hindcast").json()
    v1 = next(c for c in body["candidates"] if c["candidate"] == "ml_residual")
    assert v1["applied_to_runs"] is False
    m = v1["measured"]
    assert m["held_out_fields_degraded"] == "6 of 6"
    assert m["mean_trajectory_rmse_change_percent"] == pytest.approx(-359.7)
    assert m["physics_trajectory_error_m"] < m["ml_trajectory_error_m"]
    # A per-step RMSE *improvement* alongside a trajectory *degradation* is
    # the whole finding, so both numbers travel together.
    assert m["per_step_rmse_change_percent"] > 0
    assert "wrong acceptance metric" in m["why_it_failed"]
    assert m["evidence"].endswith("root_cause_analysis.md")
    assert (REPO_ROOT / m["evidence"]).exists(), \
        "the cited evidence file does not exist"


def test_the_v2_candidate_is_unmeasured_not_disabled(env):
    """The distinction the module exists for: v2 losing and v2 never having
    been run are different facts."""
    body = env.get("/api/models/hindcast").json()
    v2 = next(c for c in body["candidates"]
              if c["candidate"] == "ml_origin_correction")
    assert v2["implemented"] is True
    assert v2["applied_to_runs"] is False
    assert v2["status"] in ("NOT TRAINED", "TRAINED", "UNLOADABLE")
    if v2["status"] == "NOT TRAINED":
        assert "never been compared" in v2["note"]
        # Explicitly NOT "disabled on evidence".
        assert "NOT disabled on evidence" in v2["note"]
        assert v2["trainer"], "no trainer is named, so it cannot be measured"
        assert (REPO_ROOT / v2["trainer"]).exists()


def test_the_vocabulary_is_published_with_the_selection(env):
    """A UI must not have to invent a meaning for `candidate_untrained`."""
    body = env.get("/api/models/hindcast").json()
    assert set(body["vocabulary"]) >= {
        "measured", "untested_candidate", "candidate_untrained", "no_candidate"}
    assert body["selection_basis"] in body["vocabulary"]


def test_the_comparison_metrics_are_named(env):
    """A benchmark that does not say what it compares is unfalsifiable."""
    body = env.get("/api/models/hindcast").json()
    metrics = " ".join(body["compared_on"]).lower()
    assert "origin" in metrics
    assert "trajectory" in metrics or "rmse" in metrics


def test_the_models_page_lists_both_ml_candidates_with_distinct_states(env):
    body = env.get("/api/models").json()
    by_kind = {m["kind"]: m for m in body["models"]}
    assert by_kind["drift_residual"]["status"] == "EXPERIMENTAL"
    assert by_kind["drift_residual"]["applied"] is False
    v2 = by_kind["drift_origin_correction"]
    assert v2["applied"] is False
    # Different status strings, because they are different situations.
    assert v2["status"] != by_kind["drift_residual"]["status"]


def test_a_recorded_result_actually_changes_the_selection(tmp_path,
                                                          monkeypatch):
    """Proves the verdict is READ from disk rather than hardcoded.

    Without this, every assertion above would also pass for a module that
    simply returned "physics" unconditionally -- and the benchmark would be
    decoration that could never report an ML win even if one happened.
    """
    from backend.services import hindcast_benchmark as hb

    result_file = tmp_path / "origin_correction_benchmark.json"
    result_file.write_text(json.dumps({
        "winner": "ml_origin_correction",
        "metrics": ["origin position error (km)"],
        "physics": {"origin_error_km": 1.84},
        "ml": {"origin_error_km": 0.91},
        "held_out_fields": 6,
    }), encoding="utf-8")
    monkeypatch.setattr(hb, "BENCHMARK_RESULT", result_file)

    body = hb.benchmark()
    assert body["primary"] == "ml_origin_correction"
    assert body["fallback"] == "physics"
    assert body["selection_basis"] == "measured"
    assert body["benchmark_run"] is True
    assert body["result"]["ml"]["origin_error_km"] == 0.91
    # And the physics candidate stops claiming to be primary.
    physics = next(c for c in body["candidates"] if c["candidate"] == "physics")
    assert physics["primary"] is False


def test_a_recorded_physics_win_is_reported_as_measured(tmp_path, monkeypatch):
    """The symmetric case. Physics winning a REAL benchmark is a different
    (and stronger) statement than physics winning by default."""
    from backend.services import hindcast_benchmark as hb

    result_file = tmp_path / "origin_correction_benchmark.json"
    result_file.write_text(json.dumps({
        "winner": "physics",
        "metrics": ["origin position error (km)"],
        "physics": {"origin_error_km": 0.88},
        "ml": {"origin_error_km": 2.31},
    }), encoding="utf-8")
    monkeypatch.setattr(hb, "BENCHMARK_RESULT", result_file)

    body = hb.benchmark()
    assert body["primary"] == "physics"
    assert body["selection_basis"] == "measured"
    assert body["benchmark_run"] is True
    assert "selected 'physics'" in body["selection_detail"]


def test_unloadable_weights_are_not_reported_as_trained(tmp_path, monkeypatch):
    """A weights file that will not load is worse than none: it would read as
    TRAINED while every run silently fell back to physics."""
    from backend.services import hindcast_benchmark as hb

    weights_dir = tmp_path / "weights"
    weights_dir.mkdir()
    (weights_dir / "origin_correction.npz").write_bytes(b"not an npz at all")
    monkeypatch.setattr(hb, "WEIGHTS_DIR", weights_dir)

    state = hb._v2_state()
    assert state["weights_present"] is True
    assert state["trained"] is False
    assert state["status"] == "UNLOADABLE"


def test_the_summary_line_never_implies_a_benchmark_ran(env):
    from backend.services import hindcast_benchmark as hb

    line = hb.summary_line()
    assert "PHYSICS" in line
    assert "no ML/physics benchmark has been run" in line
