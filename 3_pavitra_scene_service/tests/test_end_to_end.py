"""End-to-End Functional Validation Tests for Satellite Scene Service (Phase 9A).

Validates the complete satellite acquisition workflow across:
- CLI interface
- SceneRetrievalChain
- LocalSceneCache
- CDSEAdapter
- ASFAdapter
- Provider Status Probes
- Fixtures and Cache Integrity
"""

import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

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
)
from satellite.asf_adapter import ASFAdapter
from satellite.cache import LocalSceneCache
from satellite.cdse_adapter import CDSEAdapter
from satellite.chain import SceneRetrievalChain
from satellite.cli import main
from satellite.models import GeoBoundingBox, RetrievalResponse, SceneMetadata
from satellite.status import get_api_status


class TestEndToEndSatelliteWorkflow(unittest.TestCase):
    """Phase 9A Comprehensive End-to-End Functional Validation Suite."""

    def setUp(self):
        ensure_demo_fixture()
        self.temp_cache_dir = tempfile.mkdtemp(prefix="oceantrace_e2e_cache_")
        self.temp_out_dir = tempfile.mkdtemp(prefix="oceantrace_e2e_out_")
        self.scene_id = SCENE_ID

    def tearDown(self):
        for path in (self.temp_cache_dir, self.temp_out_dir):
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)

    def test_01_mock_scene_retrieval_via_cli(self):
        """Test 1: End-to-end scene retrieval via CLI in mock mode."""
        with patch("urllib.request.urlopen", side_effect=AssertionError("Live network request attempted")):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main([
                    "--scene-id",
                    self.scene_id,
                    "--mock",
                    "--cache-dir",
                    self.temp_cache_dir,
                    "--output-dir",
                    self.temp_out_dir,
                ])
                self.assertEqual(exit_code, 0)
                output = json.loads(fake_out.getvalue())
                self.assertTrue(output["success"])
                self.assertEqual(output["scene_id"], self.scene_id)
                self.assertIn(output["source_provider"], ["CDSE", "ASF", "CACHE"])
                self.assertIsNotNone(output["metadata"])
                self.assertIsNotNone(output["geotiff_path"])
                self.assertTrue(os.path.exists(output["geotiff_path"]))
                # Check for sensitive credentials in output
                self.assertNotIn("password", fake_out.getvalue().lower())
                self.assertNotIn("token", fake_out.getvalue().lower())

    def test_02_cache_hit_workflow(self):
        """Test 2: Two sequential retrievals ensure the second request is a CACHE hit without remote calls."""
        cache = LocalSceneCache(cache_dir=self.temp_cache_dir)
        mock_cdse = CDSEAdapter(mock_mode=True)
        mock_asf = ASFAdapter(mock_mode=True)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse,
            asf_adapter=mock_asf,
            cache=cache,
            download_dir=self.temp_out_dir,
        )

        # 1st request: Cache miss -> CDSE mock retrieval -> cached
        resp1 = chain.retrieve_scene(self.scene_id)
        self.assertTrue(resp1.success)
        self.assertEqual(resp1.source_provider, "CDSE")
        self.assertTrue(cache.has_scene(self.scene_id))

        # 2nd request: Cache hit -> immediately served from local cache
        with patch.object(mock_cdse, "download_scene", side_effect=AssertionError("CDSE should not be called")):
            with patch.object(mock_asf, "download_scene", side_effect=AssertionError("ASF should not be called")):
                resp2 = chain.retrieve_scene(self.scene_id)
                self.assertTrue(resp2.success)
                self.assertEqual(resp2.source_provider, "CACHE")
                self.assertEqual(resp2.scene_id, self.scene_id)

    def test_03_cache_integrity_validation(self):
        """Test 3: Local cache entry integrity, checksum, and file size validation."""
        cache = LocalSceneCache(cache_dir=self.temp_cache_dir)
        mock_cdse = CDSEAdapter(mock_mode=True)
        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse,
            cache=cache,
            download_dir=self.temp_out_dir,
        )

        resp = chain.retrieve_scene(self.scene_id)
        self.assertTrue(resp.success)

        # Validate cache files
        scene_dir = Path(self.temp_cache_dir) / "scenes" / self.scene_id
        meta_file = scene_dir / "scene_meta.json"
        raster_file = scene_dir / "scene_sigma0_db.tif"

        self.assertTrue(meta_file.exists())
        self.assertTrue(raster_file.exists())

        # Validate file size and SHA-256
        with open(meta_file, "r", encoding="utf-8") as f:
            meta_json = json.load(f)

        actual_size = raster_file.stat().st_size
        self.assertEqual(meta_json["file_size_bytes"], actual_size)

        with open(raster_file, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(meta_json["checksum"], actual_sha)

        # Validate cache entry method
        self.assertTrue(cache.validate_cache_entry(self.scene_id))

    def test_04_cli_status_check(self):
        """Test 4: CLI provider health check returns structured JSON in mock mode without network."""
        with patch("urllib.request.urlopen", side_effect=AssertionError("Live network request attempted")):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--check-status", "--mock"])
                self.assertEqual(exit_code, 0)
                output = json.loads(fake_out.getvalue())
                self.assertTrue(output["success"])
                self.assertIn("providers", output)
                self.assertIn("cdse", output["providers"])
                self.assertIn("asf", output["providers"])
                self.assertEqual(output["providers"]["cdse"]["status"], "UP")
                self.assertEqual(output["providers"]["asf"]["status"], "UP")

    def test_05_invalid_input_rejection(self):
        """Test 5: CLI rejects invalid bounding box and invalid datetime with exit code 2."""
        # 1. Out of range bounding box (lon > 180)
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--bbox", "200,51,3,52"])
            self.assertEqual(exit_code, 2)
            output = json.loads(fake_out.getvalue())
            self.assertFalse(output["success"])
            self.assertIn("Invalid --bbox argument", output["error"])

        # 2. Inverted bounding box (min_lon > max_lon)
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--bbox", "10,51,5,52"])
            self.assertEqual(exit_code, 2)

        # 3. Invalid datetime string
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--bbox", "2.5,51.5,3.2,52.1", "--start-time", "INVALID_DATE_STRING"])
            self.assertEqual(exit_code, 2)
            output = json.loads(fake_out.getvalue())
            self.assertFalse(output["success"])
            self.assertIn("Invalid datetime argument", output["error"])

    def test_06_provider_fallback_chain(self):
        """Test 6: Cache Miss -> CDSE Failure -> ASF Fallback Success -> Cached."""
        cache = LocalSceneCache(cache_dir=self.temp_cache_dir)
        mock_cdse = CDSEAdapter(mock_mode=True)
        mock_asf = ASFAdapter(mock_mode=True)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse,
            asf_adapter=mock_asf,
            cache=cache,
            download_dir=self.temp_out_dir,
        )

        # Force CDSE failure
        with patch.object(mock_cdse, "download_scene", side_effect=RuntimeError("CDSE API 503 Outage")):
            resp = chain.retrieve_scene(self.scene_id)
            self.assertTrue(resp.success)
            self.assertEqual(resp.source_provider, "ASF")
            self.assertIsNotNone(resp.geotiff_path)
            self.assertTrue(os.path.exists(resp.geotiff_path))
            # Verify result was saved to cache
            self.assertTrue(cache.has_scene(self.scene_id))

    def test_07_complete_provider_failure(self):
        """Test 7: Cache Miss -> CDSE Failure -> ASF Failure -> Structured Failure Response."""
        cache = LocalSceneCache(cache_dir=self.temp_cache_dir)
        mock_cdse = CDSEAdapter(mock_mode=True)
        mock_asf = ASFAdapter(mock_mode=True)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse,
            asf_adapter=mock_asf,
            cache=cache,
            download_dir=self.temp_out_dir,
        )

        with patch.object(mock_cdse, "download_scene", side_effect=RuntimeError("CDSE down")):
            with patch.object(mock_asf, "download_scene", side_effect=RuntimeError("ASF down")):
                resp = chain.retrieve_scene("NON_EXISTENT_SCENE")
                self.assertFalse(resp.success)
                self.assertIsNone(resp.source_provider)
                self.assertIsNone(resp.metadata)
                self.assertIsNone(resp.geotiff_path)
                self.assertIsNotNone(resp.error_message)
                self.assertIn("All providers failed", resp.error_message)

    def test_08_mock_mode_network_safety(self):
        """Test 8: Strict proof that mock mode makes zero live HTTP calls."""
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network call forbidden in mock mode")):
            # 1. CDSE mock download
            cdse = CDSEAdapter(mock_mode=True)
            out_file = os.path.join(self.temp_out_dir, "cdse_test.tif")
            meta = cdse.download_scene(self.scene_id, out_file)
            self.assertEqual(meta.scene_id, self.scene_id)

            # 2. ASF mock download
            asf = ASFAdapter(mock_mode=True)
            out_file_asf = os.path.join(self.temp_out_dir, "asf_test.tif")
            meta_asf = asf.download_scene(self.scene_id, out_file_asf)
            self.assertEqual(meta_asf.scene_id, self.scene_id)

            # 3. Health status mock
            status = get_api_status(mock_mode=True)
            self.assertEqual(status["cdse"].status, "UP")
            self.assertEqual(status["asf"].status, "UP")

    def test_09_demo_raster_tiff_structure_validation(self):
        """Test 9: Inspect and validate baseline TIFF structure of the demo raster fixture."""
        self.assertTrue(DEMO_RASTER_PATH.exists())
        size = DEMO_RASTER_PATH.stat().st_size
        self.assertGreater(size, 0)

        with open(DEMO_RASTER_PATH, "rb") as f:
            header = f.read(8)

        # Baseline TIFF Little-Endian header verification: 'II\x2a\x00' followed by IFD offset
        self.assertEqual(header[:4], b"II\x2a\x00", "Must have valid TIFF little-endian magic bytes")
        ifd_offset = struct.unpack("<I", header[4:8])[0]
        self.assertEqual(ifd_offset, 8, "First IFD offset must be at byte 8")

        # Read checksum from metadata and verify exact match
        with open(DEMO_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)

        with open(DEMO_RASTER_PATH, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()

        self.assertEqual(meta["checksum"], actual_sha)
        self.assertEqual(meta["file_size_bytes"], size)


if __name__ == "__main__":
    unittest.main()
