"""The metrics endpoint must describe the checkpoint that actually ships.

Audit M-05: `/api/metrics` read `data/runs/training/metrics.json`, a
training-time scratch file still holding the superseded POC epoch (29), while
the deployed segmenter is `unet-r34-fullcorpus-e48`. The endpoint therefore
advertised a model nobody runs -- with numbers ~2x better than the real one at
some thresholds.

These tests pin the three things that made that possible:

1. identity comes from the ONNX file the detection service loads,
2. the numbers come from that checkpoint's own held-out evaluation,
3. the stale file is never consulted, even though it is still on disk.

The values asserted here are read from `docs/eval/*_holdout.json`, so they are
the measured figures, not literals someone hoped for. They are written out
explicitly anyway: a test that recomputes its expectation from the same file it
is checking would pass no matter what the endpoint served.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Deployed identities, from the ONNX metadata_props of the shipped weights.
DEPLOYED_SEGMENT = "unet-r34-fullcorpus-e48"
DEPLOYED_SEGMENT_EPOCH = 48
DEPLOYED_SCREEN = "yolo11n-screen-dartis-2026-08-24"

# Superseded POC checkpoint. Its metrics file is still on disk and must stay
# unread; the epoch is what the endpoint used to leak.
SUPERSEDED_EPOCH = 29


@pytest.fixture(scope="module")
def client(sign_in_helper):
    """App built against the real repo data root.

    Other suites redirect DATA_ROOT to a tmp dir at import time; pinning it
    here (and dropping any cached `backend` modules) keeps this file correct
    regardless of collection order. The endpoint under test is read-only.
    """
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    sys.path.insert(0, str(REPO_ROOT / "main_system"))
    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        # Every /api route needs a session since PROMPT-07.
        sign_in_helper(c)
        yield c


@pytest.fixture(scope="module")
def payload(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    return r.json()


def _close(actual, expected, tol=5e-5):
    assert actual is not None, "metric is absent from the response"
    assert abs(float(actual) - expected) < tol, f"{actual} != {expected}"


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def test_segmentation_reports_the_deployed_checkpoint(payload):
    ckpt = (payload.get("segmentation") or {}).get("checkpoint")
    assert ckpt, "segmentation must name the checkpoint it describes"
    assert ckpt["name"] == DEPLOYED_SEGMENT
    assert ckpt["epoch"] == DEPLOYED_SEGMENT_EPOCH
    assert ckpt["file"].endswith("segment.onnx")
    assert len(ckpt["sha256"]) == 64
    assert ckpt["config_fingerprint"] == "01e24b0fb0e8"


def test_screening_reports_the_deployed_checkpoint(payload):
    ckpt = (payload.get("screening") or {}).get("checkpoint")
    assert ckpt, "screening must name the checkpoint it describes"
    assert ckpt["name"] == DEPLOYED_SCREEN
    assert ckpt["file"].endswith("screen.onnx")
    assert len(ckpt["sha256"]) == 64


def test_checkpoint_hash_matches_the_file_on_disk(payload):
    """The advertised hash must be of the weights actually present."""
    import hashlib

    ckpt = payload["segmentation"]["checkpoint"]
    onnx_path = REPO_ROOT / ckpt["file"]
    assert onnx_path.exists(), f"advertised weights missing: {ckpt['file']}"
    assert hashlib.sha256(onnx_path.read_bytes()).hexdigest() == ckpt["sha256"]
    assert ckpt["bytes"] == onnx_path.stat().st_size


# --------------------------------------------------------------------------
# the numbers  (deployed U-Net + ResNet-34, threshold 0.5)
# --------------------------------------------------------------------------

def test_segmentation_serves_the_e48_holdout_numbers(payload):
    seg = payload["segmentation"]
    assert seg["threshold"] == 0.5
    assert seg["checkpoint_epoch"] == DEPLOYED_SEGMENT_EPOCH

    # Oil-tile scope: the 512 tiles that contain oil.
    _close(seg["oil_tile_iou"], 0.5723)

    # Overall scope: includes the 5,248 background tiles. Publishing this
    # alongside the oil-tile figure is what stops the page reading ~29% better
    # than the model is.
    _close(seg["overall_iou"], 0.4445)
    _close(seg["overall_precision"], 0.6387)
    _close(seg["overall_recall"], 0.5938)
    _close(seg["overall_f1"], 0.6154)


def test_false_positive_burden_is_published(payload):
    """The 'overall' numbers only mean something with the FP count beside them."""
    seg = payload["segmentation"]
    assert seg["no_oil_tiles"] == 5248
    assert seg["no_oil_firing"] == 280
    _close(seg["no_oil_firing_rate"], 0.05335, tol=1e-4)


def test_screening_serves_the_dartis_numbers(payload):
    scr = payload["screening"]
    _close(scr["map50"], 0.6230, tol=1e-4)
    _close(scr["map50_95"], 0.2970, tol=1e-4)
    _close(scr["precision"], 0.6570, tol=1e-4)
    _close(scr["recall"], 0.5653, tol=1e-4)


def test_numbers_match_the_deployed_holdout_file(payload):
    """Cross-check: every served figure traces to the checkpoint's eval file."""
    holdout = json.loads(
        (REPO_ROOT / "docs" / "eval" / f"{DEPLOYED_SEGMENT}_holdout.json")
        .read_text(encoding="utf-8"))
    half = holdout["results"]["0.5"]
    seg = payload["segmentation"]

    assert seg["oil_tile_iou"] == half["per_kind"]["oil"]["iou"]
    assert seg["overall_iou"] == half["overall"]["iou"]
    assert seg["overall_f1"] == half["overall"]["f1"]
    assert holdout["checkpoint_epoch"] == DEPLOYED_SEGMENT_EPOCH


# --------------------------------------------------------------------------
# regression: the stale file stays unread
# --------------------------------------------------------------------------

def test_the_superseded_metrics_file_is_still_on_disk():
    """Guards the regression below: if this file vanished, that test would
    pass for the wrong reason."""
    stale = REPO_ROOT / "data" / "runs" / "training" / "metrics.json"
    if not stale.exists():
        pytest.skip("superseded metrics.json not present in this checkout")
    assert json.loads(stale.read_text(encoding="utf-8"))["checkpoint_epoch"] == SUPERSEDED_EPOCH


def test_endpoint_does_not_serve_the_superseded_checkpoint(payload):
    """M-05 itself. The stale file is on disk and says epoch 29; the response
    must show the deployed epoch instead."""
    seg = payload["segmentation"]
    assert seg["checkpoint_epoch"] != SUPERSEDED_EPOCH
    assert seg["checkpoint"]["name"] == DEPLOYED_SEGMENT
    # The POC numbers, which must never appear again.
    assert abs(float(seg["oil_tile_iou"]) - 0.3505) > 0.01


def test_segmentation_source_is_the_deployed_eval_not_training_scratch():
    """The resolver is keyed by the checkpoint's own name, so a re-export
    cannot keep quoting the previous model's evaluation."""
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    from backend.api.analytics import _deployed_segmentation_eval

    assert _deployed_segmentation_eval(None) is None
    assert _deployed_segmentation_eval("no-such-checkpoint-v9") is None

    resolved = _deployed_segmentation_eval(DEPLOYED_SEGMENT)
    assert resolved is not None
    assert resolved["checkpoint_epoch"] == DEPLOYED_SEGMENT_EPOCH


# --------------------------------------------------------------------------
# honesty of the surrounding claims
# --------------------------------------------------------------------------

def test_drift_is_labelled_experimental_and_disabled(payload):
    drift = payload["drift"]
    assert drift["accuracy_reported"] is False
    assert drift["status"] == "experimental"
    assert drift["applied"] is False
    assert drift["evaluated"] == "negative"


def test_pixel_accuracy_is_still_refused(payload):
    note = payload["segmentation"]["pixel_accuracy_note"]
    assert "not reported" in note.lower()


def test_no_response_field_names_an_undeployed_model(payload):
    """YOLOv8 is not what ships, and the UI reads these strings verbatim."""
    blob = json.dumps(payload).lower()
    assert "yolov8" not in blob
    assert "yolo11n" in blob


def test_split_caveat_survives_to_the_response(payload):
    """The split's own index.json says `poc_holdout: true` and carries a
    WARNING, while the e48 model card line 24 calls the same split
    "untouched". Two sources disagree, so the endpoint follows the split
    metadata -- the artefact that records how the set was actually built --
    and keeps the caveat visible. Fixing better numbers in place while
    dropping this note would overstate the model.
    """
    split = json.loads(
        (REPO_ROOT / "data" / "processed" / "trujillo" / "test" / "index.json")
        .read_text(encoding="utf-8"))["meta"]
    if not split.get("poc_holdout"):
        pytest.skip("split is no longer flagged as a POC holdout")

    assert payload["segmentation"]["poc_holdout"] is True
    assert payload["notes"], "the split's WARNING must reach the UI"
    assert any("re-measure" in n or "POC" in n for n in payload["notes"])
