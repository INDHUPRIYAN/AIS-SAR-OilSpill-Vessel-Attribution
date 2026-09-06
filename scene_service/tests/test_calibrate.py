"""Tests for the radiometric calibration module (DN -> Sigma0 dB).

Unit tests run on tiny synthetic annotation XML fixtures; the integration
test runs on a small window of the real Chennai 2017-01-29 scene and is
skipped when that scene is not on disk (marked slow).
"""

import json
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import pytest

module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.calibrate import (  # noqa: E402
    Lut,
    border_noise_mask,
    check_domain_gap,
    find_safe_files,
    land_mask_for_block,
    load_db_range,
    parse_ads_header,
    parse_calibration_lut,
    parse_noise_lut,
    radiometric_block,
)

CHENNAI_SAFE_DIR = Path(module_root).parent / "data" / "scenes" / "S1A_CHENNAI_20170129"


# ---------------------------------------------------------------------------
# Synthetic annotation XML fixtures
# ---------------------------------------------------------------------------

CAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<calibration>
  <adsHeader>
    <missionId>S1A</missionId>
    <productType>GRD</productType>
    <polarisation>VV</polarisation>
    <startTime>2017-01-29T00:31:32.651003</startTime>
  </adsHeader>
  <calibrationVectorList count="2">
    <calibrationVector>
      <azimuthTime>2017-01-29T00:31:32.651003</azimuthTime>
      <line>0</line>
      <pixel count="3">0 4 8</pixel>
      <sigmaNought count="3">100.0 200.0 300.0</sigmaNought>
    </calibrationVector>
    <calibrationVector>
      <azimuthTime>2017-01-29T00:31:35.000000</azimuthTime>
      <line>10</line>
      <pixel count="3">0 4 8</pixel>
      <sigmaNought count="3">300.0 400.0 500.0</sigmaNought>
    </calibrationVector>
  </calibrationVectorList>
</calibration>
"""

# Old-style (pre-IPF 2.9, like the Chennai 2017 scene): noiseVectorList/noiseLut.
NOISE_XML_OLD = """<?xml version="1.0" encoding="UTF-8"?>
<noise>
  <noiseVectorList count="2">
    <noiseVector>
      <line>0</line>
      <pixel count="3">0 4 8</pixel>
      <noiseLut count="3">10.0 20.0 30.0</noiseLut>
    </noiseVector>
    <noiseVector>
      <line>10</line>
      <pixel count="3">0 4 8</pixel>
      <noiseLut count="3">30.0 40.0 50.0</noiseLut>
    </noiseVector>
  </noiseVectorList>
</noise>
"""

# Current-style: noiseRangeVectorList/noiseRangeLut.
NOISE_XML_NEW = """<?xml version="1.0" encoding="UTF-8"?>
<noise>
  <noiseRangeVectorList count="1">
    <noiseRangeVector>
      <line>0</line>
      <pixel count="2">0 8</pixel>
      <noiseRangeLut count="2">5.0 15.0</noiseRangeLut>
    </noiseRangeVector>
  </noiseRangeVectorList>
</noise>
"""


class TestLutParsingAndBilinear(unittest.TestCase):
    """LUT parsing plus the two interpolation axes = bilinear correctness."""

    def _write(self, tmpdir, name, content):
        p = Path(tmpdir) / name
        p.write_text(content, encoding="utf-8")
        return p

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.cal_path = self._write(self._tmp.name, "calibration-x-vv-x.xml", CAL_XML)
        self.noise_old_path = self._write(self._tmp.name, "noise-x-vv-old.xml", NOISE_XML_OLD)
        self.noise_new_path = self._write(self._tmp.name, "noise-x-vv-new.xml", NOISE_XML_NEW)

    def tearDown(self):
        self._tmp.cleanup()

    def test_01_pixel_axis_interpolation(self):
        """Sparse pixel samples are linearly densified along the pixel axis."""
        lut = parse_calibration_lut(self.cal_path, width=9)
        self.assertEqual(lut.dense.shape, (2, 9))
        # Exact sample points survive.
        np.testing.assert_allclose(lut.dense[0, [0, 4, 8]], [100.0, 200.0, 300.0])
        # Midpoints are linear: pixel 2 between (0,100) and (4,200) -> 150.
        self.assertAlmostEqual(lut.dense[0, 2], 150.0)
        self.assertAlmostEqual(lut.dense[0, 6], 250.0)

    def test_02_line_axis_interpolation(self):
        """rows_for() interpolates between calibration vectors along the line axis."""
        lut = parse_calibration_lut(self.cal_path, width=9)
        rows = lut.rows_for(np.array([5]))  # halfway between line 0 and line 10
        # Bilinear: pixel 0 -> (100+300)/2 = 200; pixel 4 -> (200+400)/2 = 300.
        self.assertAlmostEqual(rows[0, 0], 200.0, places=4)
        self.assertAlmostEqual(rows[0, 4], 300.0, places=4)
        # Full bilinear at (line 5, pixel 2): mean of 150 and 350 = 250.
        self.assertAlmostEqual(rows[0, 2], 250.0, places=4)

    def test_03_line_axis_clamped_outside_vectors(self):
        """Rows beyond the first/last vector clamp to the edge vector (no wild extrapolation)."""
        lut = parse_calibration_lut(self.cal_path, width=9)
        below = lut.rows_for(np.array([-5]))
        above = lut.rows_for(np.array([99]))
        np.testing.assert_allclose(below[0, [0, 4, 8]], [100.0, 200.0, 300.0], rtol=1e-5)
        np.testing.assert_allclose(above[0, [0, 4, 8]], [300.0, 400.0, 500.0], rtol=1e-5)

    def test_04_noise_lut_old_and_new_element_names(self):
        """Both noiseVectorList (pre-2018) and noiseRangeVectorList parse."""
        old = parse_noise_lut(self.noise_old_path, width=9)
        new = parse_noise_lut(self.noise_new_path, width=9)
        self.assertAlmostEqual(old.dense[0, 4], 20.0)
        self.assertAlmostEqual(new.dense[0, 4], 10.0)  # midpoint of 5..15

    def test_05_single_vector_lut_repeats(self):
        """A one-vector LUT applies to every row."""
        lut = Lut(lines=np.array([0.0]), dense=np.array([[1.0, 2.0, 3.0]]))
        rows = lut.rows_for(np.array([0, 50, 100]))
        self.assertEqual(rows.shape, (3, 3))
        np.testing.assert_allclose(rows[2], [1.0, 2.0, 3.0])

    def test_06_ads_header_parse(self):
        hdr = parse_ads_header(self.cal_path)
        self.assertEqual(hdr["polarisation"], "VV")
        self.assertEqual(hdr["startTime"], "2017-01-29T00:31:32.651003")


class TestRadiometricBlock(unittest.TestCase):
    def test_07_db_conversion_and_clipping(self):
        """sigma0 = DN^2/A^2 -> dB, clipped to db_range."""
        # DN=100, A=100 -> sigma0=1.0 -> 0 dB; DN=10, A=100 -> 0.01 -> -20 dB.
        dn = np.array([[100, 100, 100, 10]], dtype=np.uint16)
        a = np.full((1, 4), 100.0, dtype=np.float32)
        eta = np.zeros((1, 4), dtype=np.float32)
        db = radiometric_block(dn, a, eta, db_min=-35.0, db_max=-5.0,
                               border_dn_threshold=0)
        self.assertAlmostEqual(float(db[0, 3]), -20.0, places=3)
        # 0 dB exceeds db_max -> clipped to -5.
        self.assertAlmostEqual(float(db[0, 0]), -5.0, places=3)

    def test_08_clip_false_keeps_raw_db(self):
        dn = np.array([[100]], dtype=np.uint16)
        a = np.full((1, 1), 100.0, dtype=np.float32)
        eta = np.zeros((1, 1), dtype=np.float32)
        db = radiometric_block(dn, a, eta, db_min=-35.0, db_max=-5.0,
                               border_dn_threshold=0, clip=False)
        self.assertAlmostEqual(float(db[0, 0]), 0.0, places=3)

    def test_09_noise_subtraction_and_floor(self):
        """DN^2 < noise power floors at a small positive value, never log(<=0)."""
        # DN=50 -> DN^2=2500; eta=3000 > DN^2: would be negative without floor.
        dn = np.full((1, 3), 50, dtype=np.uint16)
        a = np.full((1, 3), 100.0, dtype=np.float32)
        eta = np.array([[0.0, 900.0, 3000.0]], dtype=np.float32)
        db = radiometric_block(dn, a, eta, db_min=-35.0, db_max=0.0,
                               border_dn_threshold=0)
        self.assertTrue(np.all(np.isfinite(db)))
        # eta=0: 2500/1e4 = 0.25 -> ~-6.02 dB
        self.assertAlmostEqual(float(db[0, 0]), 10 * np.log10(0.25), places=3)
        # eta=900: 1600/1e4 = 0.16 -> ~-7.96 dB (noise removal brightens nothing,
        # it darkens: subtracting noise lowers sigma0)
        self.assertAlmostEqual(float(db[0, 1]), 10 * np.log10(0.16), places=3)
        # eta > DN^2: floored, then clipped to db_min.
        self.assertAlmostEqual(float(db[0, 2]), -35.0, places=3)

    def test_10_zero_dn_is_nodata(self):
        dn = np.array([[0, 100, 0]], dtype=np.uint16)
        a = np.full((1, 3), 100.0, dtype=np.float32)
        eta = np.zeros((1, 3), dtype=np.float32)
        db = radiometric_block(dn, a, eta, db_min=-35.0, db_max=0.0,
                               border_dn_threshold=0)
        self.assertTrue(np.isnan(db[0, 0]))
        self.assertTrue(np.isnan(db[0, 2]))
        self.assertTrue(np.isfinite(db[0, 1]))


class TestBorderNoiseMask(unittest.TestCase):
    def test_11_edge_runs_masked_interior_dark_kept(self):
        """Leading/trailing near-zero runs are trimmed; an interior dark slick is not."""
        dn = np.array([
            [1, 2, 100, 5, 120, 3, 1],   # dark cols at both edges + dark pixel inside
            [80, 90, 100, 110, 120, 130, 140],  # clean row
        ], dtype=np.uint16)
        mask = border_noise_mask(dn, threshold=20)
        np.testing.assert_array_equal(
            mask[0], [True, True, False, False, False, True, True]
        )
        # The interior DN=5 pixel (col 3) is inside the first/last valid span: kept.
        self.assertFalse(mask[0, 3])
        self.assertFalse(mask[1].any())

    def test_12_fully_dark_row_fully_masked(self):
        dn = np.zeros((1, 5), dtype=np.uint16)
        mask = border_noise_mask(dn, threshold=20)
        self.assertTrue(mask.all())


class TestLandMask(unittest.TestCase):
    def test_13_chennai_coast_land_vs_sea(self):
        """global-land-mask separates Chennai city from the Bay of Bengal."""
        from affine import Affine

        # 0.01 deg pixels spanning lon 80.0..81.0, lat 13.5 down to 12.5.
        transform = Affine(0.01, 0.0, 80.0, 0.0, -0.01, 13.5)
        mask = land_mask_for_block(transform, height=100, width=100)
        # Chennai city ~ (13.06 N, 80.20 E): row=(13.5-13.06)/0.01=44, col=20.
        self.assertTrue(mask[44, 20])
        # Open Bay of Bengal ~ (13.06 N, 80.80 E): col=80.
        self.assertFalse(mask[44, 80])


class TestConfigAndGate(unittest.TestCase):
    def test_14_load_db_range_reads_normalisation_yaml(self):
        """db_range comes from the frozen contract file, never a hardcode."""
        import yaml

        db_min, db_max = load_db_range()
        cfg_path = Path(module_root).parent / "main_system" / "config" / "normalisation.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.assertEqual(db_min, float(cfg["sar"]["db_min"]))
        self.assertEqual(db_max, float(cfg["sar"]["db_max"]))
        self.assertLess(db_min, db_max)

    def _write_sigma0(self, path, data):
        import rasterio
        from rasterio.transform import from_origin

        profile = {
            "driver": "GTiff", "width": data.shape[1], "height": data.shape[0],
            "count": 1, "dtype": "float32", "nodata": float("nan"),
            "crs": "EPSG:4326", "transform": from_origin(80.0, 13.5, 0.01, 0.01),
        }
        with rasterio.open(str(path), "w", **profile) as dst:
            dst.write(data.astype(np.float32), 1)

    def test_15_domain_gap_gate_pass(self):
        import tempfile

        db_min, db_max = load_db_range()
        rng = np.random.default_rng(42)
        # Comfortably inside the clip range.
        data = rng.normal((db_min + db_max) / 2.0, 2.0, size=(64, 64))
        data = np.clip(data, db_min + 5, db_max - 5)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sigma0.tif"
            self._write_sigma0(p, data)
            result = check_domain_gap(p, verbose=False)
        self.assertEqual(result["verdict"], "PASS")
        self.assertGreater(result["p1_db"], db_min)
        self.assertLess(result["p99_db"], db_max)

    def test_16_domain_gap_gate_fails_when_pinned_at_floor(self):
        """>=1% of sea pixels at the clip floor == unclipped p1 outside the range."""
        import tempfile

        db_min, db_max = load_db_range()
        data = np.full((64, 64), (db_min + db_max) / 2.0)
        data[:8, :] = db_min  # 12.5% pinned at the floor, as clipping would leave them
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sigma0.tif"
            self._write_sigma0(p, data)
            result = check_domain_gap(p, verbose=False)
        self.assertEqual(result["verdict"], "DOMAIN GAP")

    def test_17_domain_gap_ignores_nan_land(self):
        import tempfile

        db_min, db_max = load_db_range()
        data = np.full((64, 64), (db_min + db_max) / 2.0)
        data[:32, :] = np.nan  # "land"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sigma0.tif"
            self._write_sigma0(p, data)
            result = check_domain_gap(p, verbose=False)
        self.assertEqual(result["n_sea_pixels"], 32 * 64)


class TestSafeDiscovery(unittest.TestCase):
    def test_18_flat_cog_layout_discovery(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "S1A_IW_GRDH_TEST_COG.SAFE").mkdir()
            (td / "s1a-iw-grd-vv-x-001.tiff").write_bytes(b"x")
            (td / "calibration-s1a-iw-grd-vv-x-001.xml").write_text(CAL_XML)
            (td / "noise-s1a-iw-grd-vv-x-001.xml").write_text(NOISE_XML_OLD)
            (td / "s1a-iw-grd-vv-x-001.xml").write_text("<product/>")
            files = find_safe_files(td, "vv")
            self.assertEqual(files.scene_id, "S1A_IW_GRDH_TEST_COG")
            self.assertEqual(files.measurement_tif.name, "s1a-iw-grd-vv-x-001.tiff")
            self.assertEqual(files.calibration_xml.name, "calibration-s1a-iw-grd-vv-x-001.xml")
            self.assertEqual(files.noise_xml.name, "noise-s1a-iw-grd-vv-x-001.xml")
            self.assertEqual(files.annotation_xml.name, "s1a-iw-grd-vv-x-001.xml")

    def test_19_standard_safe_layout_discovery(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            safe = Path(td) / "S1A_IW_GRDH_TEST.SAFE"
            (safe / "measurement").mkdir(parents=True)
            (safe / "annotation" / "calibration").mkdir(parents=True)
            (safe / "measurement" / "s1a-iw-grd-vh-x-002.tiff").write_bytes(b"x")
            (safe / "annotation" / "s1a-iw-grd-vh-x-002.xml").write_text("<product/>")
            (safe / "annotation" / "calibration" / "calibration-s1a-iw-grd-vh-x-002.xml").write_text(CAL_XML)
            (safe / "annotation" / "calibration" / "noise-s1a-iw-grd-vh-x-002.xml").write_text(NOISE_XML_OLD)
            files = find_safe_files(safe, "vh")
            self.assertEqual(files.scene_id, "S1A_IW_GRDH_TEST")
            self.assertEqual(files.polarisation, "VH")

    def test_20_missing_files_raise(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "s1a-iw-grd-vv-x-001.tiff").write_bytes(b"x")
            with self.assertRaises(FileNotFoundError):
                find_safe_files(td, "vv")


@pytest.mark.slow
@pytest.mark.skipif(
    not CHENNAI_SAFE_DIR.is_dir()
    or not any(CHENNAI_SAFE_DIR.glob("*-vv-*.tiff")),
    reason="Real Chennai S1A scene not on disk",
)
class TestRealSceneWindow(unittest.TestCase):
    """Integration: calibrate a small window of the real Chennai 2017 scene."""

    def test_21_real_window_calibration(self):
        import rasterio
        from rasterio.windows import Window

        from satellite.calibrate import (
            parse_calibration_lut as pcl,
            parse_noise_lut as pnl,
        )

        files = find_safe_files(CHENNAI_SAFE_DIR, "vv")
        db_min, db_max = load_db_range()
        with rasterio.open(str(files.measurement_tif)) as src:
            self.assertGreater(len(src.gcps[0]), 0)  # GRD carries GCPs
            cal = pcl(files.calibration_xml, src.width)
            noise = pnl(files.noise_xml, src.width)
            row0, col0, size = 8000, 4000, 512  # offshore window (east = sea)
            dn = src.read(1, window=Window(col0, row0, size, size))
            rows_idx = np.arange(row0, row0 + size)
            db = radiometric_block(
                dn,
                cal.rows_for(rows_idx)[:, col0:col0 + size],
                noise.rows_for(rows_idx)[:, col0:col0 + size],
                db_min, db_max,
            )
        finite = db[np.isfinite(db)]
        self.assertGreater(finite.size, 0.9 * db.size)  # interior window: mostly valid
        self.assertGreaterEqual(float(finite.min()), db_min)
        self.assertLessEqual(float(finite.max()), db_max)
        # Ocean VV backscatter must land in a physically sane range.
        p50 = float(np.percentile(finite, 50))
        self.assertGreater(p50, -30.0)
        self.assertLess(p50, -2.0)


if __name__ == "__main__":
    unittest.main()
