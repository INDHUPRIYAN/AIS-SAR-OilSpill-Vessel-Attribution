"""Which hindcast is primary, and on what evidence.

The problem statement asks for "an automated detection and hindcasting machine
learning model", and for the ML and physics hindcasts to be BENCHMARKED with
the winner made primary. This module is the benchmark's public face: it reports
the candidates, their measured outcomes, the selection, and -- crucially -- the
BASIS for that selection.

THE DISTINCTION THIS MODULE EXISTS TO PRESERVE
----------------------------------------------
There are three completely different reasons physics could be primary, and a UI
that rendered them identically would be lying in two of the three cases:

  1. **ML lost a benchmark.** A measurement exists and physics won.
  2. **ML was never measured.** No benchmark exists, so physics is primary by
     DEFAULT, not by victory.
  3. **ML was never built.** Nothing to compare.

`selection_basis` names which one applies. Saying "physics outperformed ML"
when case 2 holds would be fabricating a benchmark result, which standing rule
11 forbids and which the problem statement calls out directly: "Never fabricate
ML superiority" cuts both ways -- fabricating physics superiority is the same
offence with the same consequence, an operator trusting a comparison nobody
ran.

CURRENT STATE
-------------
Two ML candidates have existed.

**v1, the per-step residual** (`ml_residual.py`, weights present) was trained
and evaluated and FAILED, and the failure is measured rather than asserted:
held-out trajectory RMSE degraded in 6 of 6 held-out forcing fields (mean
-359.7%), because the learning target -- forward-Euler truncation error at a
600 s step -- is a 0.675 m RMS quantity sitting on a 128.75 m advective step
and 77.5 m of per-step diffusive noise, and the model's 1.121 m per-step bias
integrated to 161 m over 144 steps. Physics 20.3 m against ML 220.7 m on the
held-out field. It is DISABLED and contributes to no run. That is case 1, and
it is a real benchmark result with a real loser.

**v2, the origin correction** (`ml_origin.py`) targets the quantity the problem
statement actually asks for -- the origin location -- and applies ONE bounded
correction to the finished hindcast rather than one per step, which is the fix
the v1 root-cause analysis derived. The code is implemented and unit-tested.
**It has no trained weights in this checkout**, so it has never been measured
against physics, so it cannot be selected. That is case 2, and it is reported
as case 2.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (REPO_ROOT, REPO_ROOT / "analysis_engines"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DRIFT_DIR = REPO_ROOT / "analysis_engines" / "engines" / "drift"
WEIGHTS_DIR = DRIFT_DIR / "weights"
EVIDENCE_DIR = REPO_ROOT / "docs" / "qa" / "evidence" / "final" / "ml_hindcast"

# Where a completed v2 benchmark would be written. Read rather than assumed, so
# training and benchmarking v2 makes this endpoint report a real comparison
# with no code change here -- and so its ABSENCE is what produces the honest
# "never measured" answer rather than a hardcoded verdict.
BENCHMARK_RESULT = EVIDENCE_DIR / "origin_correction_benchmark.json"

# The v1 result, transcribed from `root_cause_analysis.md`. Hardcoded because
# it is a historical finding about a disabled model, not a live measurement --
# and the document is cited so the numbers are checkable.
V1_MEASURED = {
    "model_version": "drift-residual-mlp-20260906",
    "held_out_fields_degraded": "6 of 6",
    "mean_trajectory_rmse_change_percent": -359.7,
    "per_step_rmse_change_percent": 12.4,
    "physics_trajectory_error_m": 20.3,
    "ml_trajectory_error_m": 220.7,
    "target_rms_m": 0.675,
    "advective_step_m": 128.75,
    "diffusive_noise_per_step_m": 77.5,
    "per_step_bias_m": 1.121,
    "accumulated_bias_24h_m": 161.4,
    "steps_in_24h": 144,
    "evidence": "docs/qa/evidence/final/ml_hindcast/root_cause_analysis.md",
    "why_it_failed": (
        "The learning target was wrong, not the training. Forward-Euler "
        "truncation error at a 600 s step is 0.5% of the advective step it "
        "corrects and 0.9% of the diffusive noise it is buried under, so any "
        "cross-field generalisation error exceeds the signal -- and a per-step "
        "correction applied 144 times integrates its bias linearly. A per-step "
        "RMSE improvement is the wrong acceptance metric: RMSE rewards variance "
        "reduction and is blind to the small constant offset that destroys a "
        "multi-step integration."),
}


def _repo_relative(path: Path) -> str:
    """A readable path for a response body, without assuming it is in the repo.

    `Path.relative_to` RAISES when the target is outside the base, so building
    a display string with it crashed the whole endpoint as soon as the
    benchmark file lived anywhere else -- which is true in a deployment that
    stores evidence outside the checkout, and was true of the temp directory a
    test pointed it at.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        return None


def _v2_state() -> dict:
    """Whether the origin-correction model exists, is trained, and is loadable."""
    weights = WEIGHTS_DIR / "origin_correction.npz"
    state: dict[str, Any] = {
        "candidate": "ml_origin_correction",
        "name": "learned origin-recovery correction (v2)",
        "implemented": (DRIFT_DIR / "ml_origin.py").exists(),
        "trainer": ("analysis_engines/engines/drift/train_origin.py"
                    if (DRIFT_DIR / "train_origin.py").exists() else None),
        "weights_path": "analysis_engines/engines/drift/weights/origin_correction.npz",
        "weights_present": weights.exists(),
        "trained": False,
        "applied_to_runs": False,
        "sha256": None,
        "metadata": None,
    }
    if not weights.exists():
        state["status"] = "NOT TRAINED"
        state["note"] = (
            "The model is implemented and unit-tested; no weights file exists "
            "in this checkout, so it has never produced a prediction and has "
            "never been compared against the physics hindcast. It is NOT "
            "disabled on evidence -- it is unmeasured. Train it with "
            "`python -m engines.drift.train_origin` and the benchmark below "
            "will report a real comparison.")
        return state

    state["sha256"] = _sha256(weights)
    # A weights file that will not load is worse than none: it would be
    # reported as trained while every run silently fell back to physics.
    try:
        from engines.drift.ml_origin import load_model

        model = load_model(weights)
        state["trained"] = model is not None
        state["metadata"] = getattr(model, "metadata", None)
        state["status"] = "TRAINED" if model is not None else "UNLOADABLE"
        if model is None:
            state["note"] = (
                f"a weights file exists at {state['weights_path']} but would "
                f"not load, so nothing can use it. Reported as UNLOADABLE "
                f"rather than TRAINED.")
    except Exception as exc:                       # noqa: BLE001
        state["status"] = "UNLOADABLE"
        state["note"] = f"{type(exc).__name__}: {exc}"
    return state


def benchmark() -> dict:
    """The hindcast comparison, the selection, and the basis for it."""
    v2 = _v2_state()
    result = _read_json(BENCHMARK_RESULT)

    physics = {
        "candidate": "physics",
        "name": "Lagrangian backward integration",
        "status": "DEPLOYED",
        "primary": True,
        "formulation": "V_oil = V_current + 0.03 * V_wind + stochastic diffusion",
        "windage_coefficient": 0.03,
        "forcing": ["CMEMS (currents)", "ERA5 (10 m wind)"],
        "note": ("The origin estimate every sealed run carries. No machine "
                 "learning contributes to it."),
    }

    v1 = {
        "candidate": "ml_residual",
        "name": "per-step learned residual (v1)",
        "status": "DISABLED ON EVIDENCE",
        "applied_to_runs": False,
        "weights_present": (WEIGHTS_DIR / "drift_residual.npz").exists(),
        "sha256": _sha256(WEIGHTS_DIR / "drift_residual.npz"),
        "measured": V1_MEASURED,
        "note": ("Trained, evaluated, and it lost. This IS a completed "
                 "benchmark with a real loser, and the numbers are measured "
                 "rather than asserted."),
    }

    # --- the selection, and which of the three cases we are in ---------
    if result and result.get("winner"):
        winner = result["winner"]
        basis = "measured"
        detail = (
            f"a completed benchmark selected '{winner}'. "
            f"See {_repo_relative(BENCHMARK_RESULT)}.")
        if winner != "physics":
            v2["applied_to_runs"] = True
            physics["primary"] = False
    elif v2["status"] == "TRAINED":
        winner = "physics"
        basis = "untested_candidate"
        detail = (
            "the v2 origin-correction model is trained but no benchmark "
            "result has been recorded, so physics remains primary by default. "
            "This is NOT a statement that physics is better -- the comparison "
            "has not been run. Run the benchmark before relying on either.")
    elif v2["implemented"]:
        winner = "physics"
        basis = "candidate_untrained"
        detail = (
            "physics is primary because the v2 ML candidate has no trained "
            "weights in this checkout and has therefore never been measured "
            "against it. The only completed hindcast benchmark is v1, which "
            "ML lost decisively (6 of 6 held-out fields degraded, mean "
            "-359.7% trajectory RMSE). v2 targets a different quantity and "
            "its outcome is UNKNOWN.")
    else:
        winner = "physics"
        basis = "no_candidate"
        detail = "no ML hindcast candidate is implemented."

    return {
        "primary": winner,
        "fallback": ("physics" if winner != "physics" else None),
        "selection_basis": basis,
        "selection_detail": detail,
        # Said out loud, because this is the field a demo is most tempted to
        # render as a victory.
        "benchmark_run": bool(result and result.get("winner")),
        "candidates": [physics, v1, v2],
        "compared_on": result.get("metrics") if result else [
            "origin position error (km)",
            "origin time-window error (h)",
            "trajectory RMSE (m)",
            "stability across held-out forcing fields",
        ],
        "result": result,
        "how_to_benchmark": {
            "train": "python -m engines.drift.train_origin",
            "note": ("Ground truth is generated closed-loop on real CMEMS + "
                     "ERA5 forcing: seed a known release point, integrate "
                     "forward with diffusion, run the ordinary hindcast on the "
                     "resulting cloud, and label the recovery error. That "
                     "corrects hindcast-process bias ONLY -- it cannot correct "
                     "error in the forcing itself, and a run whose forcing is "
                     "wrong will still have a wrong origin."),
            "writes": _repo_relative(BENCHMARK_RESULT),
        },
        "vocabulary": {
            "measured": "a benchmark was run and this is its winner",
            "untested_candidate": ("a candidate exists and is trained, but no "
                                   "comparison has been run; the primary is a "
                                   "default, not a winner"),
            "candidate_untrained": ("a candidate is implemented but has no "
                                    "weights, so it has never been measured"),
            "no_candidate": "nothing to compare",
        },
    }


def summary_line() -> str:
    """One sentence for a status strip. Never claims an unrun benchmark."""
    data = benchmark()
    if data["benchmark_run"]:
        return (f"hindcast: {data['primary'].upper()} (selected by benchmark)")
    return (f"hindcast: {data['primary'].upper()} "
            f"({data['selection_basis'].replace('_', ' ')} -- no ML/physics "
            f"benchmark has been run)")
