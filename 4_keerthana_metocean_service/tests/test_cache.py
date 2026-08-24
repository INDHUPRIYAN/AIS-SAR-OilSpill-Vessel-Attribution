"""
Unit and Contract Tests for Metocean Cache and Offline Serving Engine (Phase 6).
Tests cache key determinism, atomic persistence, NetCDF integrity validation,
corrupted file handling, and offline fallback serving.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add module root to sys.path
MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from metocean.cache import (
    MetoceanCache,
    generate_cache_key,
    validate_cached_netcdf,
)
from metocean.chain import MetoceanChain
from metocean.errors import UnavailableError
from metocean.models import BBox, MetoceanRequest
from metocean.status import ProviderStatusTracker


def create_mock_netcdf_file(path: Path, data_type: str = "currents", corrupted: bool = False) -> Path:
    """Create a mock NetCDF file for testing.

    This writes a REAL NetCDF with the contract's dimensions and variables --
    `uo`/`vo` for currents, `u10`/`v10` for wind, all on `(time, lat, lon)`.

    It previously wrote only the magic bytes `CDF\\x02` followed by zeros. That
    passed `validate_cached_netcdf` only because the validator skips its deep
    schema check when xarray is not importable; with xarray installed the same
    fixture fails, because a header with no dimensions is not a valid dataset.

    A fixture that only satisfies a disabled code path tests nothing. Worse, it
    hid the fact that cache validation degrades to a header-only check in any
    environment missing xarray -- so a corrupt file with the right first four
    bytes would have been served to the drift engine as valid forcing data.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if corrupted:
        with open(path, "wb") as f:
            f.write(b"CORRUPTED_NOT_NETCDF_DATA")
        return path

    try:
        import numpy as np
        import xarray as xr
    except ImportError:  # keep the old behaviour where xarray is unavailable
        with open(path, "wb") as f:
            f.write(b"CDF\x02" + b"\x00" * 64)
        return path

    times = np.array(["2017-01-31T00:00:00", "2017-01-31T06:00:00",
                      "2017-01-31T12:00:00"], dtype="datetime64[ns]")
    lats = np.linspace(12.70, 13.55, 6)
    lons = np.linspace(79.90, 80.75, 6)
    shape = (len(times), len(lats), len(lons))
    rng = np.random.default_rng(1337)

    names = ("uo", "vo") if data_type == "currents" else ("u10", "v10")
    units = "m s-1"
    ds = xr.Dataset(
        {
            names[0]: (("time", "lat", "lon"), rng.normal(0, 0.3, shape).astype("float32"),
                       {"units": units, "standard_name": f"eastward_{data_type}"}),
            names[1]: (("time", "lat", "lon"), rng.normal(0, 0.3, shape).astype("float32"),
                       {"units": units, "standard_name": f"northward_{data_type}"}),
        },
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds.to_netcdf(path)
    ds.close()
    return path


class TestMetoceanCache(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp_dir.name)
        self.cache = MetoceanCache(cache_dir=self.cache_dir)

        self.req_a = MetoceanRequest(
            bbox=[79.90, 12.70, 80.75, 13.55],
            start="2017-01-31T00:00:00Z",
            end="2017-02-03T00:00:00Z",
            what="currents",
            provider="cmems",
        )
        self.req_a_dup = MetoceanRequest(
            bbox=[79.90, 12.70, 80.75, 13.55],
            start="2017-01-31T00:00:00Z",
            end="2017-02-03T00:00:00Z",
            what="currents",
            provider="cmems",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    # Test 1 — Cache key determinism
    def test_01_cache_key_determinism(self):
        key_1 = self.cache.get_cache_key(self.req_a, "currents")
        key_2 = self.cache.get_cache_key(self.req_a_dup, "currents")
        self.assertEqual(key_1, key_2)

    # Test 2 — Different bbox produces different cache key
    def test_02_different_bbox_different_key(self):
        req_diff_bbox = MetoceanRequest(
            bbox=[80.00, 12.70, 80.75, 13.55],
            start="2017-01-31T00:00:00Z",
            end="2017-02-03T00:00:00Z",
            what="currents",
            provider="cmems",
        )
        key_a = self.cache.get_cache_key(self.req_a, "currents")
        key_b = self.cache.get_cache_key(req_diff_bbox, "currents")
        self.assertNotEqual(key_a, key_b)

    # Test 3 — Different time window produces different cache key
    def test_03_different_time_different_key(self):
        req_diff_time = MetoceanRequest(
            bbox=[79.90, 12.70, 80.75, 13.55],
            start="2017-01-31T00:00:00Z",
            end="2017-02-05T00:00:00Z",
            what="currents",
            provider="cmems",
        )
        key_a = self.cache.get_cache_key(self.req_a, "currents")
        key_b = self.cache.get_cache_key(req_diff_time, "currents")
        self.assertNotEqual(key_a, key_b)

    # Test 4 — Different product/provider produces different cache key
    def test_04_different_provider_different_key(self):
        key_cmems = self.cache.get_cache_key(self.req_a, "currents", product="CMEMS")
        key_hycom = self.cache.get_cache_key(self.req_a, "currents", product="HYCOM")
        self.assertNotEqual(key_cmems, key_hycom)

    # Test 5 — Cache write stores valid normalized NetCDF
    def test_05_cache_write(self):
        src_file = self.cache_dir / "src_currents.nc"
        create_mock_netcdf_file(src_file, "currents", corrupted=False)

        stored_path = self.cache.put_currents(self.req_a, src_file)
        self.assertTrue(Path(stored_path).exists())
        self.assertTrue(Path(stored_path).stat().st_size > 0)

    # Test 6 — Cache hit returns cached file
    def test_06_cache_hit(self):
        src_file = self.cache_dir / "src_currents.nc"
        create_mock_netcdf_file(src_file, "currents", corrupted=False)
        self.cache.put_currents(self.req_a, src_file)

        hit_path = self.cache.get_currents(self.req_a)
        self.assertIsNotNone(hit_path)
        self.assertTrue(Path(hit_path).exists())

    # Test 7 — Cache miss correctly reports None
    def test_07_cache_miss(self):
        req_unseen = MetoceanRequest(
            bbox=[10.0, 10.0, 20.0, 20.0],
            start="2020-01-01T00:00:00Z",
            end="2020-01-02T00:00:00Z",
            what="currents",
        )
        miss_path = self.cache.get_currents(req_unseen)
        self.assertIsNone(miss_path)

    # Test 8 — Cache validation rejects invalid/empty NetCDF
    def test_08_cache_validation_empty_file(self):
        empty_file = self.cache_dir / "empty.nc"
        empty_file.touch()
        self.assertFalse(validate_cached_netcdf(empty_file, "currents"))

    # Test 9 — Corrupted cache file is rejected on lookup
    def test_09_corrupted_cache_rejected(self):
        key = self.cache.get_cache_key(self.req_a, "currents")
        corrupted_target = self.cache_dir / f"currents_{key}.nc"
        create_mock_netcdf_file(corrupted_target, "currents", corrupted=True)

        lookup_res = self.cache.get_currents(self.req_a)
        self.assertIsNone(lookup_res)

    # Test 10 — Offline serving: live network fails but valid cache exists -> success
    def test_10_offline_serving_success(self):
        # 1. Populate cache with valid dataset
        src_file = self.cache_dir / "src_wind.nc"
        create_mock_netcdf_file(src_file, "wind", corrupted=False)
        req_wind = MetoceanRequest(
            bbox=[79.90, 12.70, 80.75, 13.55],
            start="2017-01-31T00:00:00Z",
            end="2017-02-03T00:00:00Z",
            what="wind",
        )
        self.cache.put_wind(req_wind, src_file)

        # 2. Mock external adapters to fail (simulating complete network outage)
        mock_era5 = MagicMock()
        mock_era5.fetch_data.side_effect = UnavailableError("Offline: No network connection")
        mock_openmeteo = MagicMock()
        mock_openmeteo.fetch_data.side_effect = UnavailableError("Offline: No network connection")

        status_tracker = ProviderStatusTracker(output_dir=self.cache_dir)
        chain = MetoceanChain(
            era5_adapter=mock_era5,
            openmeteo_adapter=mock_openmeteo,
            cache=self.cache,
            status_tracker=status_tracker,
        )

        res = chain.fetch_wind(req_wind)
        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "StaticCache")
        self.assertIsNotNone(res["path"])

    # Test 11 — Offline cache miss: network fails and no cache -> structured degraded result
    def test_11_offline_cache_miss_degraded(self):
        mock_era5 = MagicMock()
        mock_era5.fetch_data.side_effect = UnavailableError("No network")
        mock_openmeteo = MagicMock()
        mock_openmeteo.fetch_data.side_effect = UnavailableError("No network")

        empty_cache = MetoceanCache(cache_dir=self.cache_dir / "empty_dir")
        status_tracker = ProviderStatusTracker(output_dir=self.cache_dir)
        chain = MetoceanChain(
            era5_adapter=mock_era5,
            openmeteo_adapter=mock_openmeteo,
            cache=empty_cache,
            status_tracker=status_tracker,
        )

        req_unseen = MetoceanRequest(
            bbox=[0.0, 0.0, 1.0, 1.0],
            start="2020-01-01T00:00:00Z",
            end="2020-01-02T00:00:00Z",
            what="wind",
        )
        res = chain.fetch_wind(req_unseen)
        self.assertFalse(res["success"])
        self.assertTrue(res["degraded"])
        self.assertIsNone(res["path"])

    # Test 12 — Contract validation on scene directory cache structure
    def test_12_scene_directory_cache_structure(self):
        scene_id = "SCENE_CHENNAI_2017"
        src_file = self.cache_dir / "src_currents.nc"
        create_mock_netcdf_file(src_file, "currents", corrupted=False)

        # Store with scene_id
        self.cache.put_currents(self.req_a, src_file, scene_id=scene_id)

        # Retrieve by scene_id
        scene_hit = self.cache.get_currents(self.req_a, scene_id=scene_id)
        self.assertIsNotNone(scene_hit)
        self.assertIn(scene_id, scene_hit)


if __name__ == "__main__":
    unittest.main()
