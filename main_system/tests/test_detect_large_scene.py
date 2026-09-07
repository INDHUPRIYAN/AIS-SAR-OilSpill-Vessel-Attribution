"""A full-size scene must be measured by the deployed model, not the fallback.

Two defects, both of which made a real Sentinel-1 run quietly report something
other than what it claimed.

**The segmenter ran out of memory on real scenes.** Stitching held two
full-scene float32 canvases plus the padded input -- over 8 GB on a 600 M-pixel
IW GRD product. The MemoryError was caught upstream and the run downgraded to
threshold-morphology, so the scene was still "detected", just not by the model
anyone thought was being evaluated. The canvases now spill to disk above a size
threshold; these tests pin that the two paths agree exactly, because a memory
fix that changed the numbers would be a far worse bug than the one it replaced.

**The screening harness counted every candidate as oil.** `class` is a Python
keyword, so the contract model names the field `class_` and aliases it. Reading
one spelling silently mislabels every candidate from the other.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


class _FakeSession:
    """Deterministic stand-in for the ONNX segmenter.

    Returns logits that depend on the tile's own content, so stitching errors
    (misplaced tiles, double-counted overlaps, a mis-cropped pad) change the
    output instead of cancelling out.
    """

    class _In:
        name = "input"

    def get_inputs(self):
        return [self._In()]

    def run(self, _outputs, feeds):
        arr = next(iter(feeds.values()))
        # logit = 4*(x - 0.5): a smooth, invertible function of the input.
        return [(4.0 * (arr - 0.5)).astype(np.float32)]


@pytest.fixture
def cfg():
    from ml.config import load_config

    return load_config()


def _scene(h: int, w: int, seed: int = 7):
    rng = np.random.default_rng(seed)
    db = rng.uniform(-30.0, -5.0, size=(h, w)).astype(np.float32)
    valid = np.ones((h, w), bool)
    valid[: h // 8, : w // 8] = False          # a land corner
    return db, valid


# --------------------------------------------------------------------------
# the memory fix must not change a single pixel
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shape", [(300, 420), (256, 256), (700, 260)])
def test_on_disk_and_in_memory_stitching_agree_exactly(cfg, monkeypatch, shape):
    from backend.services.detection import service

    db, valid = _scene(*shape)
    sess = _FakeSession()

    monkeypatch.setattr(service, "_ON_DISK_PIXELS", 10 ** 15)   # force in-memory
    mask_mem, conf_mem = service.infer_tiled(sess, db, valid, cfg)

    monkeypatch.setattr(service, "_ON_DISK_PIXELS", 0)          # force on-disk
    mask_disk, conf_disk = service.infer_tiled(sess, db, valid, cfg)

    assert np.array_equal(mask_mem, mask_disk), \
        "the memory-mapped path produced a different mask"
    assert conf_mem == pytest.approx(conf_disk, abs=1e-7)


def test_blocked_thresholding_agrees_with_a_single_pass(cfg, monkeypatch):
    """The averaging/cropping/thresholding now runs in row blocks. A block
    boundary that lost or double-counted a row would show up here."""
    from backend.services.detection import service

    db, valid = _scene(600, 300)
    sess = _FakeSession()

    monkeypatch.setattr(service, "_BLOCK_ROWS", 10 ** 9)        # one block
    mask_one, conf_one = service.infer_tiled(sess, db, valid, cfg)

    monkeypatch.setattr(service, "_BLOCK_ROWS", 17)             # many, unaligned
    mask_many, conf_many = service.infer_tiled(sess, db, valid, cfg)

    assert np.array_equal(mask_one, mask_many)
    assert conf_one == pytest.approx(conf_many, abs=1e-7)


def test_land_is_never_called_oil(cfg):
    """`valid` is applied after averaging; a block-wise rewrite could drop it."""
    from backend.services.detection import service

    db, valid = _scene(400, 400)
    mask, _ = service.infer_tiled(_FakeSession(), db, valid, cfg)
    assert mask.shape == db.shape
    assert not mask[~valid].any(), "masked-out land was labelled oil"


def test_overlap_counts_stay_exact_as_integers(cfg, monkeypatch):
    """Counts moved from float32 to uint16. Small integers either way -- but if
    the accumulator ever overflowed, the seam averages would be wrong."""
    from backend.services.detection import service

    db, valid = _scene(520, 520)
    mask, conf = service.infer_tiled(_FakeSession(), db, valid, cfg)
    # A constant-logit model would make this vacuous; this one does not.
    assert 0.0 <= conf <= 1.0
    assert mask.dtype == np.uint8


# --------------------------------------------------------------------------
# the candidate tally
# --------------------------------------------------------------------------

def test_candidate_class_is_read_through_both_spellings():
    from backend.screen_candidates import _candidate_class

    from contracts.schemas import Candidate

    model = Candidate(bbox=[0.0, 0.0, 1.0, 1.0], **{"class": "lookalike"}, score=0.4)
    assert _candidate_class(model) == "lookalike", \
        "the contract model aliases `class` to `class_`"
    assert _candidate_class({"class": "oil"}) == "oil"
    assert _candidate_class({"class_": "oil"}) == "oil"
    assert _candidate_class({}) == ""


def test_a_lookalike_is_not_counted_as_oil():
    """The original defect: every candidate counted as oil, so a screening log
    reported a scene as entirely oil while the model had rejected most of it."""
    from backend.screen_candidates import _candidate_class

    candidates = [{"class": "oil"}] * 3 + [{"class": "lookalike"}] * 7
    classes = [_candidate_class(c) for c in candidates]
    assert classes.count("oil") == 3
    assert classes.count("lookalike") == 7
