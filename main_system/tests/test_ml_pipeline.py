"""Tests for the detection training pipeline.

Focused on the invariants that fail *silently* in ML code — normalisation
round-trips, scene-level splitting, unfiltered test tiles — rather than on
model quality, which only real data can speak to.

Run:  .venv/Scripts/python -m pytest main_system/tests -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

from ml.config import (CONFIG_PATH, db_to_model, db_to_uint8, load_config,  # noqa: E402
                       uint8_to_model)
from ml.dataset import scene_split  # noqa: E402


# --------------------------------------------------------------------------
# normalisation — the shared train/inference contract
# --------------------------------------------------------------------------

def test_config_loads_and_matches_handbook():
    cfg = load_config()
    assert CONFIG_PATH.exists()
    assert cfg.sar.db_min == -35.0, "handbook specifies a -35 dB floor"
    assert cfg.sar.db_max == 0.0
    assert cfg.tiling.tile_size == 256
    assert cfg.sar.db_min < cfg.sar.db_max


def test_fingerprint_is_stable_and_sensitive():
    """The fingerprint must not drift between calls, but must change when the
    constants do — it is the only thing standing between us and a silent
    train/inference mismatch."""
    a = load_config().fingerprint
    b = load_config().fingerprint
    assert a == b and len(a) == 12

    import dataclasses
    cfg = load_config()
    altered = dataclasses.replace(cfg, sar=dataclasses.replace(cfg.sar, db_min=-30.0))
    assert altered.fingerprint != a, "a changed dB range must change the fingerprint"


def test_db_to_uint8_maps_range_endpoints():
    cfg = load_config()
    lo, hi = cfg.sar.db_min, cfg.sar.db_max
    out = db_to_uint8(np.array([lo, (lo + hi) / 2, hi], dtype=np.float32), cfg)
    assert out.dtype == np.uint8
    assert out[0] == 0 and out[2] == 255
    assert 126 <= out[1] <= 129


def test_db_to_uint8_clips_out_of_range_and_nodata():
    cfg = load_config()
    vals = np.array([-999.0, -80.0, 10.0, np.nan, np.inf], dtype=np.float32)
    out = db_to_uint8(vals, cfg)
    assert out[0] == 0, "nodata must clamp to the dB floor, not wrap"
    assert out[1] == 0, "below-floor clips to 0"
    assert out[2] == 255, "above-ceiling clips to 255"
    # NaN and +inf both clamp to the floor rather than the ceiling: non-finite
    # means broken data, and rendering it as "bright target" is precisely what
    # a detector would mistake for a slick. Dark sea is the safe failure.
    assert out[3] == 0 and out[4] == 0, "non-finite must clamp to the dB floor"


def test_uint8_and_direct_paths_agree():
    """db_to_model (live scene) and uint8_to_model(db_to_uint8(...)) (tile
    cache) must produce identical inputs, or training and inference diverge."""
    cfg = load_config()
    db = np.linspace(cfg.sar.db_min - 5, cfg.sar.db_max + 5, 257).astype(np.float32)
    np.testing.assert_array_equal(db_to_model(db, cfg), uint8_to_model(db_to_uint8(db, cfg)))


def test_model_input_is_unit_range_float32():
    cfg = load_config()
    x = db_to_model(np.random.uniform(-60, 10, (32, 32)).astype(np.float32), cfg)
    assert x.dtype == np.float32
    assert 0.0 <= float(x.min()) and float(x.max()) <= 1.0


# --------------------------------------------------------------------------
# splitting — the leak that inflates val IoU
# --------------------------------------------------------------------------

def _records(n_scenes=10, per_scene=8):
    return [{"i": s * per_scene + k, "scene": f"scene_{s:03d}", "kind": "oil",
             "oil_fraction": 0.1, "row": 0, "col": 0}
            for s in range(n_scenes) for k in range(per_scene)]


def test_scene_split_never_shares_a_scene():
    records = _records()
    train, val = scene_split(records, val_fraction=0.2, seed=1337)
    by_i = {r["i"]: r["scene"] for r in records}
    train_scenes = {by_i[i] for i in train}
    val_scenes = {by_i[i] for i in val}
    assert train_scenes and val_scenes
    assert not (train_scenes & val_scenes), "a scene appeared in both splits"
    assert len(train) + len(val) == len(records), "tiles were lost or duplicated"


def test_scene_split_is_deterministic():
    records = _records()
    assert scene_split(records, seed=1337) == scene_split(records, seed=1337)
    assert scene_split(records, seed=1337) != scene_split(records, seed=99)


def test_scene_split_respects_fraction():
    records = _records(n_scenes=20, per_scene=5)
    _, val = scene_split(records, val_fraction=0.25, seed=1337)
    by_i = {r["i"]: r["scene"] for r in records}
    assert len({by_i[i] for i in val}) == 5


# --------------------------------------------------------------------------
# tile cache — format and the unfiltered-test rule
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synth_cache(tmp_path_factory):
    from ml.synth import main as synth_main
    out = tmp_path_factory.mktemp("tiles")
    synth_main(["--split", "trainval", "--scenes", "4", "--scene-size", "512",
                "--out", str(out / "trainval")])
    synth_main(["--split", "test", "--scenes", "3", "--scene-size", "512",
                "--out", str(out / "test")])
    return out


def test_prepared_cache_has_expected_shape(synth_cache):
    for split in ("trainval", "test"):
        d = synth_cache / split
        images = np.load(d / "images.npy", mmap_mode="r")
        masks = np.load(d / "masks.npy", mmap_mode="r")
        index = json.loads((d / "index.json").read_text())

        assert images.dtype == np.uint8 and masks.dtype == np.uint8
        assert images.shape == masks.shape
        assert images.shape[1:] == (256, 256)
        assert images.shape[0] == index["meta"]["n_tiles"] == len(index["tiles"])
        assert set(np.unique(np.asarray(masks[:8]))) <= {0, 1}


def test_cache_is_stamped_with_config_fingerprint(synth_cache):
    meta = json.loads((synth_cache / "trainval" / "index.json").read_text())["meta"]
    assert meta["config_fingerprint"] == load_config().fingerprint
    assert meta["db_min"] == -35.0 and meta["db_max"] == 0.0


def test_test_split_keeps_no_oil_tiles(synth_cache):
    """Filtering the test split would inflate every false-positive number."""
    meta = json.loads((synth_cache / "test" / "index.json").read_text())["meta"]
    tiles = json.loads((synth_cache / "test" / "index.json").read_text())["tiles"]
    assert meta["filtered"] is False
    assert any(t["oil_fraction"] == 0.0 for t in tiles), \
        "test cache has no clean-water tiles to measure false positives on"


def test_trainval_split_is_filtered(synth_cache):
    payload = json.loads((synth_cache / "trainval" / "index.json").read_text())
    assert payload["meta"]["filtered"] is True
    kinds = {t["kind"] for t in payload["tiles"]}
    assert kinds <= {"oil", "hard_negative"}
    cfg = load_config()
    for t in payload["tiles"]:
        if t["kind"] == "oil":
            assert t["oil_fraction"] >= cfg.tiling.min_oil_fraction
        else:
            assert t["oil_fraction"] == 0.0


def test_dataset_rejects_fingerprint_mismatch(synth_cache):
    """A stale tile cache must fail loudly, not train quietly on wrong constants."""
    from ml.dataset import TrujilloTiles

    d = synth_cache / "trainval"
    index_path = d / "index.json"
    original = index_path.read_text()
    payload = json.loads(original)
    payload["meta"]["config_fingerprint"] = "deadbeefcafe"
    index_path.write_text(json.dumps(payload))
    try:
        with pytest.raises(RuntimeError, match="fingerprint"):
            TrujilloTiles(d)
    finally:
        index_path.write_text(original)


def test_dataset_yields_model_ready_tensors(synth_cache):
    from ml.dataset import TrujilloTiles

    ds = TrujilloTiles(synth_cache / "trainval")
    x, y = ds[0]
    assert x.shape == (1, 256, 256) and y.shape == (1, 256, 256)
    assert x.dtype.is_floating_point and 0.0 <= float(x.min()) <= float(x.max()) <= 1.0
    assert set(np.unique(y.numpy())) <= {0.0, 1.0}


# --------------------------------------------------------------------------
# loss and metrics
# --------------------------------------------------------------------------

def test_dice_bce_rewards_a_perfect_prediction():
    import torch
    from ml.train_unet import DiceBCELoss

    crit = DiceBCELoss()
    target = torch.zeros(2, 1, 32, 32)
    target[:, :, 8:24, 8:24] = 1.0
    perfect = torch.where(target > 0, 12.0, -12.0)   # confident logits
    wrong = -perfect
    assert float(crit(perfect, target)) < float(crit(wrong, target))
    assert float(crit(perfect, target)) < 0.05


def test_metrics_are_correct_on_a_known_case():
    import torch
    from ml.train_unet import binary_metrics, reduce_metrics

    target = torch.zeros(1, 1, 10, 10)
    target[..., :5, :] = 1.0                # 50 oil pixels
    logits = torch.full((1, 1, 10, 10), -10.0)
    logits[..., :3, :] = 10.0               # predict 30, all correct

    m = reduce_metrics(binary_metrics(logits, target))
    assert m["precision"] == pytest.approx(1.0, abs=1e-4)   # 30/30
    assert m["recall"] == pytest.approx(0.6, abs=1e-4)      # 30/50
    assert m["iou"] == pytest.approx(0.6, abs=1e-4)         # 30/(30+0+20)


def test_metrics_expose_an_all_sea_predictor():
    """The all-sea model scores >99% pixel accuracy. IoU must call it zero —
    this is exactly why pixel accuracy is never reported."""
    import torch
    from ml.train_unet import binary_metrics, reduce_metrics

    target = torch.zeros(1, 1, 100, 100)
    target[..., :5, :5] = 1.0               # 0.25% oil
    m = reduce_metrics(binary_metrics(torch.full((1, 1, 100, 100), -10.0), target))
    assert m["iou"] == pytest.approx(0.0, abs=1e-6)
    assert m["recall"] == pytest.approx(0.0, abs=1e-6)
