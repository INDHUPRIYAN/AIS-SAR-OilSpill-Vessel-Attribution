"""Unit tests for Phase 8 Demo Scenes and Offline Fixtures."""

import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from fixtures import (
    DEMO_META_PATH,
    DEMO_RASTER_PATH,
    DEMO_SCENE_DIR,
    SCENE_ID,
    ensure_demo_fixture,
    generate_deterministic_tiff,
)
from satellite.models import SceneMetadata


class TestDemoFixture(unittest.TestCase):
    """Test suite for Phase 8 Deterministic Demo Fixture."""

    @classmethod
    def setUpClass(cls):
        ensure_demo_fixture()

    def test_01_demo_fixture_directory_exists(self):
        """1. Test that demo fixture directory exists."""
        self.assertTrue(DEMO_SCENE_DIR.exists())
        self.assertTrue(DEMO_SCENE_DIR.is_dir())

    def test_02_metadata_json_exists(self):
        """2. Test that scene_meta.json exists on disk."""
        self.assertTrue(DEMO_META_PATH.exists())
        self.assertTrue(DEMO_META_PATH.is_file())

    def test_03_metadata_conforms_to_scene_metadata_model(self):
        """3. Test that metadata deserializes into SceneMetadata model without errors."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)

        meta = SceneMetadata(**raw_meta)
        self.assertIsInstance(meta, SceneMetadata)
        self.assertEqual(meta.scene_id, SCENE_ID)

    def test_04_scene_id_present(self):
        """4. Test that scene_id is present and formatted correctly."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        self.assertEqual(raw_meta.get("scene_id"), SCENE_ID)
        self.assertTrue(raw_meta.get("scene_id").startswith("S1A_IW_GRDH"))

    def test_05_bbox_is_valid(self):
        """5. Test that bbox coordinate ordering and values are valid."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        bbox = raw_meta.get("bbox")
        self.assertIsInstance(bbox, list)
        self.assertEqual(len(bbox), 4)
        min_lon, min_lat, max_lon, max_lat = bbox
        self.assertLess(min_lon, max_lon)
        self.assertLess(min_lat, max_lat)

    def test_06_acquisition_time_valid_utc(self):
        """6. Test that acquisition time is in UTC."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        meta = SceneMetadata(**raw_meta)
        self.assertIsNotNone(meta.acquisition_time.tzinfo)
        self.assertEqual(meta.acquisition_time.tzinfo, timezone.utc)

    def test_07_raster_file_exists(self):
        """7. Test that scene_sigma0_db.tif exists on disk."""
        self.assertTrue(DEMO_RASTER_PATH.exists())
        self.assertTrue(DEMO_RASTER_PATH.is_file())

    def test_08_raster_is_non_empty(self):
        """8. Test that raster file is non-empty."""
        size = DEMO_RASTER_PATH.stat().st_size
        self.assertGreater(size, 0)

    def test_09_file_size_bytes_matches_actual_file(self):
        """9. Test that file_size_bytes in metadata matches actual file size."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        actual_size = DEMO_RASTER_PATH.stat().st_size
        self.assertEqual(raw_meta.get("file_size_bytes"), actual_size)

    def test_10_sha256_checksum_matches_metadata(self):
        """10. Test that calculated SHA-256 of raster file exactly matches metadata."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)

        with open(DEMO_RASTER_PATH, "rb") as f:
            actual_sha256 = hashlib.sha256(f.read()).hexdigest()

        self.assertEqual(raw_meta.get("checksum"), actual_sha256)

    def test_11_metadata_serialization_roundtrip(self):
        """11. Test that SceneMetadata round-trip serialization is lossless."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)

        meta = SceneMetadata(**raw_meta)
        dumped_json = meta.model_dump_json()
        reconstructed = SceneMetadata.model_validate_json(dumped_json)
        self.assertEqual(meta.scene_id, reconstructed.scene_id)
        self.assertEqual(meta.checksum, reconstructed.checksum)

    def test_12_fixture_generation_determinism(self):
        """12. Test that repeated raster generation produces identical bytes and checksum."""
        bytes1, sha1 = generate_deterministic_tiff(64, 64)
        bytes2, sha2 = generate_deterministic_tiff(64, 64)
        self.assertEqual(bytes1, bytes2)
        self.assertEqual(sha1, sha2)

    def test_13_fixture_clearly_marked_as_offline_demo(self):
        """13. Test that fixture contains offline demonstration labeling."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        note = raw_meta.get("_fixture_note", "")
        self.assertIn("OFFLINE_DEMO_FIXTURE", note)

    def test_14_no_network_required(self):
        """14. Test that fixture operations execute without any network connections."""
        ensure_demo_fixture()
        self.assertTrue(DEMO_RASTER_PATH.exists())


if __name__ == "__main__":
    unittest.main()
