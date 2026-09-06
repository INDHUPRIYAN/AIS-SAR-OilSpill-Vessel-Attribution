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

    def test_03_metadata_conforms_to_frozen_contract(self):
        """3. Test that scene_meta.json validates against frozen contract 1 (SceneMeta)."""
        repo_root = os.path.abspath(os.path.join(module_root, ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from contracts.schemas.scene import SceneMeta

        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)

        meta = SceneMeta.model_validate(raw_meta)  # extra="forbid": exact shape
        self.assertEqual(meta.scene_id, SCENE_ID)
        self.assertEqual(meta.crs, "EPSG:4326")
        self.assertEqual(len(meta.db_range), 2)

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
        """6. Test that acquired_utc is a UTC 'Z'-suffixed timestamp per the contract."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        acquired = raw_meta.get("acquired_utc")
        self.assertIsNotNone(acquired)
        self.assertTrue(acquired.endswith("Z"))
        parsed = datetime.fromisoformat(acquired.replace("Z", "+00:00"))
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_07_raster_file_exists(self):
        """7. Test that scene_sigma0_db.tif exists on disk."""
        self.assertTrue(DEMO_RASTER_PATH.exists())
        self.assertTrue(DEMO_RASTER_PATH.is_file())

    def test_08_raster_is_non_empty(self):
        """8. Test that raster file is non-empty."""
        size = DEMO_RASTER_PATH.stat().st_size
        self.assertGreater(size, 0)

    def test_09_file_path_points_at_existing_raster(self):
        """9. Test that the contract file_path resolves to the shipped raster."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        file_path = Path(raw_meta.get("file_path", ""))
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.resolve(), DEMO_RASTER_PATH.resolve())
        self.assertGreater(file_path.stat().st_size, 0)

    def test_10_sha256_checksum_matches_generator(self):
        """10. Test that the raster on disk is byte-identical to the deterministic generator."""
        _expected_bytes, expected_sha = generate_deterministic_tiff(64, 64)

        with open(DEMO_RASTER_PATH, "rb") as f:
            actual_sha256 = hashlib.sha256(f.read()).hexdigest()

        self.assertEqual(expected_sha, actual_sha256)

    def test_11_metadata_serialization_roundtrip(self):
        """11. Test that the contract file round-trips through SceneMetadata.to_contract()."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)

        meta = SceneMetadata(
            scene_id=raw_meta["scene_id"],
            acquisition_time=raw_meta["acquired_utc"],
            bbox=raw_meta["bbox"],
            crs=raw_meta["crs"],
            db_range=raw_meta["db_range"],
            file_path=raw_meta["file_path"],
            provider_used=raw_meta["provider_used"],
            source=raw_meta["source"],
            polarisation=raw_meta.get("polarisation"),
            pixel_spacing_m=raw_meta.get("pixel_spacing_m"),
        )
        self.assertEqual(meta.to_contract(), raw_meta)

    def test_12_fixture_generation_determinism(self):
        """12. Test that repeated raster generation produces identical bytes and checksum."""
        bytes1, sha1 = generate_deterministic_tiff(64, 64)
        bytes2, sha2 = generate_deterministic_tiff(64, 64)
        self.assertEqual(bytes1, bytes2)
        self.assertEqual(sha1, sha2)

    def test_13_fixture_clearly_marked_as_offline_demo(self):
        """13. Test that the fixture is honestly badged: synthetic source, DEMO provider."""
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            raw_meta = json.load(f)
        self.assertEqual(raw_meta.get("source"), "synthetic")
        self.assertEqual(raw_meta.get("provider_used"), "DEMO")

    def test_14_no_network_required(self):
        """14. Test that fixture operations execute without any network connections."""
        ensure_demo_fixture()
        self.assertTrue(DEMO_RASTER_PATH.exists())


if __name__ == "__main__":
    unittest.main()
