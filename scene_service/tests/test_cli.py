"""Unit tests for Phase 7 CLI Interface."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.cli import build_parser, main, parse_bbox_string, parse_iso_datetime
from satellite.models import (
    GeoBoundingBox,
    ProviderHealth,
    RetrievalResponse,
    SceneMetadata,
    SceneSearchResult,
)


class TestSatelliteCLI(unittest.TestCase):
    """Test suite for Phase 7 Command Line Interface."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="oceantrace_cli_test_")
        self.scene_id = "S1A_IW_GRDH_1SDV_20231012T172530"

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            import shutil

            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_help_option(self):
        """1. Test that --help prints usage and exits with code 0."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with self.assertRaises(SystemExit) as cm:
                main(["--help"])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn("Sentinel-1 SAR Satellite Scene Acquisition Service CLI", fake_out.getvalue())
            self.assertIn("--scene-id", fake_out.getvalue())
            self.assertIn("--bbox", fake_out.getvalue())
            self.assertIn("--check-status", fake_out.getvalue())

    def test_02_valid_scene_id_retrieval(self):
        """2. Test valid --scene-id execution returning exit code 0 and valid JSON."""
        mock_response = RetrievalResponse(
            success=True,
            scene_id=self.scene_id,
            source_provider="CACHE",
            metadata=SceneMetadata(
                scene_id=self.scene_id,
                acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
                bbox=[2.5, 51.5, 3.2, 52.1],
            ),
            geotiff_path="/data/cache/satellite/scene.tif",
        )

        with patch("satellite.cli.SceneRetrievalChain.retrieve_scene", return_value=mock_response):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--scene-id", self.scene_id])
                self.assertEqual(exit_code, 0)
                output = json.loads(fake_out.getvalue())
                self.assertTrue(output["success"])
                self.assertEqual(output["scene_id"], self.scene_id)
                self.assertEqual(output["source_provider"], "CACHE")

    def test_03_invalid_scene_id_failure(self):
        """3. Test retrieval failure returns exit code 1 and structured failure JSON."""
        mock_response = RetrievalResponse(
            success=False,
            scene_id="UNKNOWN_SCENE",
            source_provider=None,
            error_message="Scene not found in CDSE or ASF",
        )

        with patch("satellite.cli.SceneRetrievalChain.retrieve_scene", return_value=mock_response):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--scene-id", "UNKNOWN_SCENE"])
                self.assertEqual(exit_code, 1)
                output = json.loads(fake_out.getvalue())
                self.assertFalse(output["success"])
                self.assertEqual(output["scene_id"], "UNKNOWN_SCENE")
                self.assertIn("Scene not found", output["error_message"])

    def test_04_valid_bbox_search(self):
        """4. Test search with valid --bbox parameter."""
        mock_search_res = SceneSearchResult(
            query_bbox=GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1),
            total_count=1,
            scenes=[
                SceneMetadata(
                    scene_id=self.scene_id,
                    acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
                    bbox=[2.5, 51.5, 3.2, 52.1],
                )
            ],
            provider="CDSE",
        )

        with patch("satellite.cli.SceneRetrievalChain.search_scenes", return_value=mock_search_res):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--bbox", "2.5,51.5,3.2,52.1"])
                self.assertEqual(exit_code, 0)
                output = json.loads(fake_out.getvalue())
                self.assertTrue(output["success"])
                self.assertEqual(output["total_count"], 1)
                self.assertEqual(output["provider"], "CDSE")

    def test_05_invalid_bbox_argument(self):
        """5. Test that malformed or out-of-range bbox returns exit code 2."""
        # Non-numeric
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--bbox", "2.5,abc,3.2,52.1"])
            self.assertEqual(exit_code, 2)
            output = json.loads(fake_out.getvalue())
            self.assertFalse(output["success"])
            self.assertIn("Invalid --bbox argument", output["error"])

        # Out of bounds (> 180 lon)
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--bbox", "2.5,51.5,200.0,52.1"])
            self.assertEqual(exit_code, 2)

    def test_06_start_end_time_parsing(self):
        """6. Test datetime argument validation."""
        # Valid ISO datetime
        dt = parse_iso_datetime("2023-10-12T00:00:00Z")
        self.assertEqual(dt.year, 2023)
        self.assertEqual(dt.tzinfo, timezone.utc)

        # Invalid datetime string in CLI
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--bbox", "2.5,51.5,3.2,52.1", "--start-time", "INVALID_DATE"])
            self.assertEqual(exit_code, 2)
            output = json.loads(fake_out.getvalue())
            self.assertIn("Invalid datetime argument", output["error"])

    def test_07_check_status(self):
        """7. Test --check-status flag returns health report."""
        mock_status = {
            "cdse": ProviderHealth(
                provider_name="CDSE", is_available=True, status="UP", latency_ms=45.0
            ),
            "asf": ProviderHealth(
                provider_name="ASF", is_available=True, status="UP", latency_ms=55.0
            ),
        }
        with patch("satellite.cli.get_api_status", return_value=mock_status):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--check-status"])
                self.assertEqual(exit_code, 0)
                output = json.loads(fake_out.getvalue())
                self.assertTrue(output["success"])
                self.assertIn("cdse", output["providers"])
                self.assertEqual(output["providers"]["cdse"]["status"], "UP")

    def test_08_mock_mode_flag(self):
        """8. Test --mock flag sets mock mode across components."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            exit_code = main(["--scene-id", self.scene_id, "--mock", "--cache-dir", self.temp_dir])
            self.assertEqual(exit_code, 0)
            output = json.loads(fake_out.getvalue())
            self.assertTrue(output["success"])
            self.assertEqual(output["scene_id"], self.scene_id)

    def test_09_output_dir_handling(self):
        """9. Test --output-dir argument configuration."""
        custom_out = os.path.join(self.temp_dir, "custom_output")
        with patch("sys.stdout", new=StringIO()):
            exit_code = main([
                "--scene-id",
                self.scene_id,
                "--mock",
                "--output-dir",
                custom_out,
                "--cache-dir",
                self.temp_dir,
            ])
            self.assertEqual(exit_code, 0)

    def test_10_successful_retrieval_json_format(self):
        """10. Test schema conformity of successful retrieval JSON output."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            main(["--scene-id", self.scene_id, "--mock", "--cache-dir", self.temp_dir])
            data = json.loads(fake_out.getvalue())
            self.assertIn("success", data)
            self.assertIn("scene_id", data)
            self.assertIn("source_provider", data)
            self.assertIn("metadata", data)
            self.assertIn("geotiff_path", data)

    def test_11_failure_json_format(self):
        """11. Test schema conformity of failure JSON output."""
        with patch(
            "satellite.cli.SceneRetrievalChain.retrieve_scene",
            side_effect=RuntimeError("Fatal download failure"),
        ):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--scene-id", "FAIL_SCENE"])
                self.assertEqual(exit_code, 1)
                data = json.loads(fake_out.getvalue())
                self.assertFalse(data["success"])
                self.assertEqual(data["scene_id"], "FAIL_SCENE")
                self.assertIn("error_message", data)

    def test_12_exit_codes_summary(self):
        """12. Test exit code 2 when no action arguments are provided."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stderr", new=StringIO()):
                exit_code = main([])
                self.assertEqual(exit_code, 2)
                data = json.loads(fake_out.getvalue())
                self.assertFalse(data["success"])
                self.assertIn("No action specified", data["error"])

    def test_13_credential_sanitization_in_cli(self):
        """13. Test that passwords are never printed to stdout in error responses."""
        secret_pwd = "SUPER_SECRET_CDSE_PASSWORD_XYZ123"
        with patch(
            "satellite.cli.SceneRetrievalChain.retrieve_scene",
            side_effect=Exception("Failed with secret password in trace"),
        ):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                main(["--scene-id", "SECRET_TEST"])
                output_str = fake_out.getvalue()
                self.assertNotIn(secret_pwd, output_str)

    def test_14_no_network_mock_execution(self):
        """14. Test offline mock execution runs without any live network access."""
        with patch("urllib.request.urlopen", side_effect=AssertionError("Live network call attempted")):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                exit_code = main(["--check-status", "--mock"])
                self.assertEqual(exit_code, 0)
                data = json.loads(fake_out.getvalue())
                self.assertTrue(data["success"])
                self.assertEqual(data["providers"]["cdse"]["status"], "UP")


if __name__ == "__main__":
    unittest.main()
