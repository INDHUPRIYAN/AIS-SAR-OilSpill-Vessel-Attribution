"""Regions the screen rejected must not become the slick that drift traces.

Found by auditing the frozen flagship against its own detect_response.json:
the segmenter found 392 regions, the screen called 361 of them look-alikes,
and raw_mask.tif -- which characterisation measured -- still held all 392.
Drift seeds from the largest slick, and the largest was a look-alike, so the
hindcast and the ranked vessels traced a region the two-stage detector had
rejected. The only guard was a warning for the case where EVERY region was
rejected; the mixed case, which is the common one, passed silently.

Pinned here:

* screen_mask removes exactly the rejected components, and cannot remove oil
  even when a neighbour's pixels share the rejected region's window;
* detect() writes an oil-only mask only in the mixed case, and never alters
  raw_mask.tif, which stays the segmenter's full output (it is a hashed
  artefact of every sealed run);
* the pipeline's characterise input is that screened mask when it exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

from backend.services.detection import service  # noqa: E402
from backend.services.detection.service import (  # noqa: E402
    DetectionOutcome, regions_from_mask, screen_mask)


def _scene():
    """Three blobs: a large one (look-alike), a medium one (oil), and a small
    one (look-alike)."""
    m = np.zeros((60, 80), np.uint8)
    m[5:25, 5:45] = 1          # large, 800 px  -> look-alike
    m[35:50, 40:60] = 1        # medium, 300 px -> oil
    m[30:34, 30:42] = 1        # small, 48 px   -> look-alike
    return m


def _classify(regions, oil_areas):
    for r in regions:
        r["class"] = "oil" if r["area_px"] in oil_areas else "lookalike"
    return regions


def test_removes_the_rejected_components_and_keeps_oil():
    m = _scene()
    regions = _classify(regions_from_mask(m, None), oil_areas={300})
    removed = screen_mask(m, regions)
    assert removed == 2
    kept = regions_from_mask(m, None)
    assert [r["area_px"] for r in kept] == [300]


def test_a_neighbour_inside_the_window_is_not_removed():
    # An L-shaped look-alike whose bbox window fully contains a small oil blob
    # sitting in the L's empty corner.
    m = np.zeros((40, 40), np.uint8)
    m[2:30, 2:6] = 1
    m[26:30, 2:30] = 1                     # the L: 112 + 112 - 16 overlap = 208 px
    m[8:14, 12:20] = 1                     # oil, 48 px, inside the L's bbox
    regions = regions_from_mask(m, None)
    _classify(regions, oil_areas={48})
    screen_mask(m, regions)
    left = regions_from_mask(m, None)
    assert [r["area_px"] for r in left] == [48]


def test_an_unmatched_region_is_left_in_rather_than_guessed_out():
    m = _scene()
    regions = _classify(regions_from_mask(m, None), oil_areas={300})
    for r in regions:
        if r["class"] == "lookalike":
            r["area_px"] += 1          # no component matches any more
    assert screen_mask(m, regions) == 0
    assert int(m.sum()) == 800 + 300 + 48


@pytest.fixture
def fake_detect(monkeypatch, tmp_path):
    """Run detect() end to end with the scene reader and models stubbed."""
    from rasterio.transform import from_origin

    def make(oil_areas):
        m = _scene()
        regions = _classify(regions_from_mask(m, None), oil_areas)
        profile = {"driver": "GTiff", "height": m.shape[0], "width": m.shape[1],
                   "count": 1, "dtype": "uint8", "crs": "EPSG:4326",
                   "transform": from_origin(-90.0, 28.0, 0.001, 0.001)}
        monkeypatch.setattr(service, "load_config", lambda: None)
        monkeypatch.setattr(service, "read_scene",
                            lambda p: (np.zeros(m.shape, np.float32), profile,
                                       np.ones(m.shape, bool)))
        monkeypatch.setattr(service, "run_detection",
                            lambda *a, **k: DetectionOutcome(
                                m, 0.7, "ml", "unet-test+yolo-screen",
                                regions, [], {"detections": [1]}))
        return m
    return make


def _read(path):
    import rasterio
    with rasterio.open(path) as ds:
        return ds.read(1)


def test_detect_writes_an_oil_only_mask_and_leaves_raw_untouched(fake_detect, tmp_path):
    fake_detect(oil_areas={300})
    screened = tmp_path / "engine_native" / "screened_mask.tif"
    service.detect(Path("scene.tif"), "S1A_TEST", tmp_path, screened_out=screened)

    raw = _read(tmp_path / "raw_mask.tif")
    assert int(raw.sum()) == 800 + 300 + 48          # every region the segmenter found
    oil = _read(screened)
    assert int(oil.sum()) == 300                       # only what the screen called oil
    warnings = (tmp_path / "detect_warnings.json").read_text()
    assert "2 of 2 rejected region(s) removed" in warnings


def test_no_screened_mask_when_nothing_was_called_oil(fake_detect, tmp_path):
    fake_detect(oil_areas=set())
    screened = tmp_path / "engine_native" / "screened_mask.tif"
    service.detect(Path("scene.tif"), "S1A_TEST", tmp_path, screened_out=screened)
    assert not screened.exists()


def test_no_screened_mask_when_nothing_was_rejected(fake_detect, tmp_path):
    fake_detect(oil_areas={800, 300, 48})
    screened = tmp_path / "engine_native" / "screened_mask.tif"
    service.detect(Path("scene.tif"), "S1A_TEST", tmp_path, screened_out=screened)
    assert not screened.exists()


def test_pipeline_characterises_the_screened_mask_when_present(tmp_path):
    from backend.services.pipeline.run import (SCREENED_MASK, characterise_mask_for,
                                               engine_dir)
    detect_result = {"mask_path": str(tmp_path / "raw_mask.tif")}
    assert characterise_mask_for(tmp_path, detect_result) == tmp_path / "raw_mask.tif"
    (engine_dir(tmp_path) / SCREENED_MASK).write_bytes(b"x")
    assert characterise_mask_for(tmp_path, detect_result).name == SCREENED_MASK
