"""Unit tests for Phase 2 LocalSceneCache."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.cache import LocalSceneCache
from satellite.models import GeoBoundingBox, SceneMetadata


class TestLocalSceneCache(unittest.TestCase):
    """Comprehensive test suite for Phase 2 LocalSceneCache."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="oceantrace_cache_test_")
        self.cache = LocalSceneCache(cache_dir=self.temp_dir)
        self.dummy_raster_bytes = b"GEOTIFF_DUMMY_BINARY_DATA_TEST_12345"

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_metadata(self, scene_id="S1A_TEST_SCENE_001", checksum=None):
        return SceneMetadata(
            scene_id=scene_id,
            platform="Sentinel-1A",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1),
            product_type="GRD",
            polarisation="VV+VH",
            orbit_direction="DESCENDING",
            checksum=checksum,
        )

    def test_01_cache_directory_creation(self):
        """1. Test that cache and scenes directories are automatically created."""
        scenes_dir = os.path.join(self.temp_dir, "scenes")
        self.assertTrue(os.path.isdir(scenes_dir))

    def test_02_empty_cache_miss(self):
        """2. Test that querying an unpopulated cache returns None (cache miss)."""
        result = self.cache.get("NON_EXISTENT_SCENE")
        self.assertIsNone(result)
        self.assertFalse(self.cache.has_scene("NON_EXISTENT_SCENE"))

    def test_03_save_metadata(self):
        """3. Test saving metadata writes valid scene_meta.json."""
        meta = self._create_sample_metadata("SCENE_SAVE_META")
        meta_path = self.cache.save_metadata(meta)
        self.assertTrue(os.path.isfile(meta_path))
        self.assertEqual(meta_path, self.cache.get_meta_path("SCENE_SAVE_META"))

        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["scene_id"], "SCENE_SAVE_META")

    def test_04_save_raster(self):
        """4. Test saving raster writes scene_sigma0_db.tif from bytes and file paths."""
        # From bytes
        raster_path = self.cache.save_raster("SCENE_RASTER_BYTES", self.dummy_raster_bytes)
        self.assertTrue(os.path.isfile(raster_path))
        self.assertEqual(raster_path, self.cache.get_raster_path("SCENE_RASTER_BYTES"))
        with open(raster_path, "rb") as f:
            self.assertEqual(f.read(), self.dummy_raster_bytes)

        # From file path
        temp_src = os.path.join(self.temp_dir, "temp_src.tif")
        with open(temp_src, "wb") as f:
            f.write(b"FILE_SOURCE_DATA")
        raster_path_2 = self.cache.save_raster("SCENE_RASTER_FILE", temp_src)
        self.assertTrue(os.path.isfile(raster_path_2))
        with open(raster_path_2, "rb") as f:
            self.assertEqual(f.read(), b"FILE_SOURCE_DATA")

    def test_05_cache_hit(self):
        """5. Test storing a scene and retrieving it successfully (cache hit)."""
        meta = self._create_sample_metadata("SCENE_HIT_001")
        stored = self.cache.put(meta, raster_source=self.dummy_raster_bytes)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.scene_id, "SCENE_HIT_001")

        retrieved = self.cache.get("SCENE_HIT_001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.scene_id, "SCENE_HIT_001")
        self.assertEqual(retrieved.platform, "Sentinel-1A")
        self.assertEqual(retrieved.file_path, self.cache.get_raster_path("SCENE_HIT_001"))
        self.assertEqual(retrieved.file_size_bytes, len(self.dummy_raster_bytes))

    def test_06_has_scene(self):
        """6. Test has_scene() accuracy."""
        self.assertFalse(self.cache.has_scene("SCENE_HAS_TEST"))
        meta = self._create_sample_metadata("SCENE_HAS_TEST")
        self.cache.put(meta, raster_source=self.dummy_raster_bytes)
        self.assertTrue(self.cache.has_scene("SCENE_HAS_TEST"))

    def test_07_metadata_reload(self):
        """7. Test that metadata fields reload perfectly from disk."""
        meta = self._create_sample_metadata("SCENE_RELOAD")
        self.cache.put(meta, raster_source=self.dummy_raster_bytes)

        # Create new cache instance pointing to same directory
        cache2 = LocalSceneCache(cache_dir=self.temp_dir)
        reloaded = cache2.get("SCENE_RELOAD")
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.scene_id, "SCENE_RELOAD")
        self.assertEqual(reloaded.bbox_list, [2.5, 51.5, 3.2, 52.1])
        self.assertEqual(
            reloaded.acquisition_time,
            datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
        )

    def test_08_missing_metadata(self):
        """8. Test that missing metadata results in a cache miss."""
        scene_id = "SCENE_NO_META"
        self.cache.save_raster(scene_id, self.dummy_raster_bytes)
        self.assertFalse(self.cache.has_scene(scene_id))
        self.assertIsNone(self.cache.get(scene_id))

    def test_09_missing_geotiff(self):
        """9. Test that missing GeoTIFF results in a cache miss."""
        scene_id = "SCENE_NO_RASTER"
        meta = self._create_sample_metadata(scene_id)
        self.cache.save_metadata(meta)
        self.assertFalse(self.cache.has_scene(scene_id))
        self.assertIsNone(self.cache.get(scene_id))

    def test_10_invalid_metadata(self):
        """10. Test that corrupted metadata JSON results in a cache miss."""
        scene_id = "SCENE_CORRUPT_META"
        self.cache.save_raster(scene_id, self.dummy_raster_bytes)
        meta_path = self.cache.get_meta_path(scene_id)
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write("{ INVALID JSON CONTENT: [")

        self.assertFalse(self.cache.has_scene(scene_id))
        self.assertIsNone(self.cache.get(scene_id))

    def test_11_valid_checksum(self):
        """11. Test valid checksum validation."""
        sha = hashlib.sha256(self.dummy_raster_bytes).hexdigest()
        meta = self._create_sample_metadata("SCENE_VALID_CHECKSUM", checksum=sha)
        self.cache.put(meta, raster_source=self.dummy_raster_bytes)

        self.assertTrue(self.cache.has_scene("SCENE_VALID_CHECKSUM"))
        cached = self.cache.get("SCENE_VALID_CHECKSUM")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.checksum, sha)

    def test_12_invalid_checksum(self):
        """12. Test that checksum mismatch marks cache invalid (miss)."""
        wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"
        meta = self._create_sample_metadata("SCENE_BAD_CHECKSUM", checksum=wrong_sha)
        # Manually save files with wrong checksum in metadata
        self.cache.save_raster("SCENE_BAD_CHECKSUM", self.dummy_raster_bytes)
        self.cache.save_metadata(meta)

        self.assertFalse(self.cache.has_scene("SCENE_BAD_CHECKSUM"))
        self.assertIsNone(self.cache.get("SCENE_BAD_CHECKSUM"))

    def test_13_duplicate_scene_handling(self):
        """13. Test overwriting/updating existing cached scene."""
        meta1 = self._create_sample_metadata("SCENE_DUP")
        self.cache.put(meta1, raster_source=b"INITIAL_DATA")
        self.assertEqual(self.cache.get("SCENE_DUP").file_size_bytes, len(b"INITIAL_DATA"))

        # Update with new data
        self.cache.put(meta1, raster_source=b"UPDATED_NEW_DATA_LONGER")
        updated = self.cache.get("SCENE_DUP")
        self.assertEqual(updated.file_size_bytes, len(b"UPDATED_NEW_DATA_LONGER"))

    def test_14_offline_operation(self):
        """14. Test that all cache operations execute strictly offline."""
        meta = self._create_sample_metadata("SCENE_OFFLINE")
        self.cache.put(meta, raster_source=self.dummy_raster_bytes)

        # List scenes
        cached_list = self.cache.list_cached_scenes()
        self.assertIn("SCENE_OFFLINE", cached_list)

        # Delete scene
        deleted = self.cache.delete("SCENE_OFFLINE")
        self.assertTrue(deleted)
        self.assertFalse(self.cache.has_scene("SCENE_OFFLINE"))


if __name__ == "__main__":
    unittest.main()
