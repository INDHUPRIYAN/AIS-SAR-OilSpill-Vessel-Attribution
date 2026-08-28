"""
Validation Tests for Authoritative Demo Scene NetCDF Datasets (Phase 7).
Validates schema compliance, offline cache accessibility, and provider status for:
- Scene A: S1A_IW_GRDH_1SDV_20170131T003445 (Chennai 2017)
- Scene B: S1A_IW_GRDH_1SDV_20231012T172530 (North Sea 2023)
"""

from pathlib import Path
import sys
import unittest

# Ensure module root is on sys.path
MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from metocean.cache import MetoceanCache, validate_cached_netcdf
from metocean.models import BBox, MetoceanRequest
from metocean.pull_demo_scenes import DEMO_SCENE_A, DEMO_SCENE_B, execute_demo_scene_pulls


class TestDemoScenes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Execute one-time cached pulls (uses existing cache if already generated)
        cls.results = execute_demo_scene_pulls()
        cls.root_data = Path("data/metocean")
        cls.cache = MetoceanCache(cache_dir=cls.root_data)

    def test_01_scene_a_files_exist_and_non_empty(self):
        scene_a_dir = self.root_data / DEMO_SCENE_A["short_id"]
        cur_file = scene_a_dir / "currents.nc"
        wind_file = scene_a_dir / "wind.nc"

        self.assertTrue(cur_file.exists(), f"Missing {cur_file}")
        self.assertTrue(wind_file.exists(), f"Missing {wind_file}")
        self.assertGreater(cur_file.stat().st_size, 100)
        self.assertGreater(wind_file.stat().st_size, 100)

    def test_02_scene_b_files_exist_and_non_empty(self):
        scene_b_dir = self.root_data / DEMO_SCENE_B["short_id"]
        cur_file = scene_b_dir / "currents.nc"
        wind_file = scene_b_dir / "wind.nc"

        self.assertTrue(cur_file.exists(), f"Missing {cur_file}")
        self.assertTrue(wind_file.exists(), f"Missing {wind_file}")
        self.assertGreater(cur_file.stat().st_size, 100)
        self.assertGreater(wind_file.stat().st_size, 100)

    def test_03_scene_a_netcdf_validation(self):
        scene_a_dir = self.root_data / DEMO_SCENE_A["short_id"]
        self.assertTrue(validate_cached_netcdf(scene_a_dir / "currents.nc", "currents"))
        self.assertTrue(validate_cached_netcdf(scene_a_dir / "wind.nc", "wind"))

    def test_04_scene_b_netcdf_validation(self):
        scene_b_dir = self.root_data / DEMO_SCENE_B["short_id"]
        self.assertTrue(validate_cached_netcdf(scene_b_dir / "currents.nc", "currents"))
        self.assertTrue(validate_cached_netcdf(scene_b_dir / "wind.nc", "wind"))

    def test_05_offline_cache_lookup_scene_a(self):
        req_a = MetoceanRequest(
            bbox=DEMO_SCENE_A["bbox"],
            start=DEMO_SCENE_A["start_utc"],
            end=DEMO_SCENE_A["end_utc"],
            what="both",
        )
        cached_cur = self.cache.get_currents(req_a, scene_id=DEMO_SCENE_A["short_id"])
        cached_wind = self.cache.get_wind(req_a, scene_id=DEMO_SCENE_A["short_id"])

        self.assertIsNotNone(cached_cur)
        self.assertIsNotNone(cached_wind)
        self.assertTrue(Path(cached_cur).exists())
        self.assertTrue(Path(cached_wind).exists())

    def test_06_offline_cache_lookup_scene_b(self):
        req_b = MetoceanRequest(
            bbox=DEMO_SCENE_B["bbox"],
            start=DEMO_SCENE_B["start_utc"],
            end=DEMO_SCENE_B["end_utc"],
            what="both",
        )
        cached_cur = self.cache.get_currents(req_b, scene_id=DEMO_SCENE_B["short_id"])
        cached_wind = self.cache.get_wind(req_b, scene_id=DEMO_SCENE_B["short_id"])

        self.assertIsNotNone(cached_cur)
        self.assertIsNotNone(cached_wind)
        self.assertTrue(Path(cached_cur).exists())
        self.assertTrue(Path(cached_wind).exists())


if __name__ == "__main__":
    unittest.main()
