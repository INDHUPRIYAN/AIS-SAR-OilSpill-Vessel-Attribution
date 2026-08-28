"""Unit tests for Phase 3 CDSEAdapter."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch
import urllib.error

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.cdse_adapter import CDSEAdapter
from satellite.models import GeoBoundingBox, SceneMetadata, SceneSearchResult


class TestCDSEAdapter(unittest.TestCase):
    """Test suite for Phase 3 CDSEAdapter."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="oceantrace_cdse_test_")
        self.bbox = GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_adapter_initialization(self):
        """1. Test initialization with parameters and mock mode."""
        adapter = CDSEAdapter(username="user@example.com", password="secret_password", mock_mode=False)
        self.assertEqual(adapter.username, "user@example.com")
        self.assertEqual(adapter.password, "secret_password")
        self.assertFalse(adapter.mock_mode)

        mock_adapter = CDSEAdapter(mock_mode=True)
        self.assertTrue(mock_adapter.mock_mode)

    def test_02_missing_credentials_raises_error(self):
        """2. Test that refresh_token raises ValueError when credentials are missing."""
        with patch.dict(os.environ, {}, clear=True):
            adapter = CDSEAdapter(username=None, password=None, mock_mode=False)
            with self.assertRaises(ValueError) as ctx:
                adapter.refresh_token()
            self.assertIn("CDSE credentials missing", str(ctx.exception))

    def test_03_mock_authentication(self):
        """3. Test mock authentication generates mock token without network call."""
        adapter = CDSEAdapter(mock_mode=True)
        token = adapter.refresh_token()
        self.assertIsNotNone(token)
        self.assertEqual(token, "mock_cdse_bearer_token_xyz")
        self.assertGreater(adapter.token_expiry, time.time())

    def test_04_mock_scene_search(self):
        """4. Test mock scene search returns deterministic SceneSearchResult."""
        adapter = CDSEAdapter(mock_mode=True)
        result = adapter.search_scenes(
            bbox=self.bbox,
            start_time=datetime(2023, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2023, 10, 15, 0, 0, 0, tzinfo=timezone.utc),
            product_type="GRD",
        )
        self.assertIsInstance(result, SceneSearchResult)
        self.assertEqual(result.provider, "CDSE")
        self.assertEqual(result.total_count, 1)
        self.assertEqual(len(result.scenes), 1)

    def test_05_mock_scene_metadata_conversion(self):
        """5. Test mock scene metadata matches Phase 1 model types and fields."""
        adapter = CDSEAdapter(mock_mode=True)
        result = adapter.search_scenes(bbox=[2.5, 51.5, 3.2, 52.1])
        scene = result.scenes[0]
        self.assertIsInstance(scene, SceneMetadata)
        self.assertEqual(scene.scene_id, "S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertEqual(scene.platform, "Sentinel-1A")
        self.assertEqual(scene.product_type, "GRD")
        self.assertEqual(scene.bbox_list, [2.5, 51.5, 3.2, 52.1])

    def test_06_bbox_query_construction(self):
        """6. Test OData filter polygon query generation."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        filter_str = adapter._build_odata_filter(self.bbox, product_type="GRD")
        self.assertIn("OData.CSC.Intersects", filter_str)
        self.assertIn("POLYGON((2.5 51.5, 3.2 51.5, 3.2 52.1, 2.5 52.1, 2.5 51.5))", filter_str)

    def test_07_time_range_query_construction(self):
        """7. Test temporal bounds in OData filter string."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        start = datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2023, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
        filter_str = adapter._build_odata_filter(self.bbox, start_time=start, end_time=end)
        self.assertIn("ContentDate/Start ge 2023-10-01T12:00:00Z", filter_str)
        self.assertIn("ContentDate/End le 2023-10-02T12:00:00Z", filter_str)

    def test_08_sentinel1_product_filtering(self):
        """8. Test Sentinel-1 collection and productType filter rules."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        filter_str = adapter._build_odata_filter(self.bbox, product_type="SLC")
        self.assertIn("SENTINEL-1", filter_str)
        self.assertIn("productType", filter_str)
        self.assertIn("'SLC'", filter_str)

    def test_09_token_expiration_handling_logic(self):
        """9. Test token caching and refresh on expiry."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        adapter.token = "valid_cached_token"
        adapter.token_expiry = time.time() + 300.0  # 5 minutes ahead

        # Should return cached token without calling refresh_token
        with patch.object(adapter, "refresh_token") as mock_refresh:
            token = adapter.get_valid_token()
            self.assertEqual(token, "valid_cached_token")
            mock_refresh.assert_not_called()

        # When token is close to expiry (< 30s) or expired
        adapter.token_expiry = time.time() + 10.0
        with patch.object(adapter, "refresh_token", return_value="refreshed_token") as mock_refresh:
            token = adapter.get_valid_token()
            self.assertEqual(token, "refreshed_token")
            mock_refresh.assert_called_once()

    def test_10_http_error_handling(self):
        """10. Test that HTTP errors during authentication raise clean RuntimeError."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        mock_http_err = urllib.error.HTTPError(
            url="https://identity.dataspace.copernicus.eu/token",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(b"Unauthorized access"),
        )
        with patch("urllib.request.urlopen", side_effect=mock_http_err):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.refresh_token()
            self.assertIn("CDSE authentication failed: HTTP 401", str(ctx.exception))
            self.assertNotIn("pwd", str(ctx.exception))

    def test_11_download_handling_mock_mode(self):
        """11. Test downloading scene in mock mode writes valid file."""
        adapter = CDSEAdapter(mock_mode=True)
        scene = SceneMetadata(
            scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=[2.5, 51.5, 3.2, 52.1],
        )
        downloaded = adapter.download_scene(scene, destination_dir=self.temp_dir)
        self.assertIsNotNone(downloaded.file_path)
        self.assertTrue(os.path.isfile(downloaded.file_path))
        self.assertGreater(downloaded.file_size_bytes, 0)
        self.assertIsNotNone(downloaded.checksum)

    def test_12_empty_download_handling(self):
        """12. Test that empty download raises RuntimeError."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        adapter.token = "test_token"
        adapter.token_expiry = time.time() + 1000

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.side_effect = [b""]  # Empty stream
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.download_scene("EMPTY_SCENE", destination_dir=self.temp_dir, retries=1)
            self.assertIn("is empty (0 bytes)", str(ctx.exception))

    def test_13_checksum_calculation_and_validation(self):
        """13. Test checksum verification on download."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        adapter.token = "test_token"
        adapter.token_expiry = time.time() + 1000

        content = b"VALID_GEOTIFF_TEST_CONTENT"
        expected_sha = hashlib.sha256(content).hexdigest()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.side_effect = [content, b""]
        mock_resp.__enter__.return_value = mock_resp

        # Matching checksum
        scene = SceneMetadata(
            scene_id="SCENE_CHECKSUM_OK",
            acquisition_time=datetime.now(timezone.utc),
            bbox=[0.0, 0.0, 1.0, 1.0],
            checksum=expected_sha,
        )
        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = adapter.download_scene(scene, destination_dir=self.temp_dir, retries=1)
            self.assertEqual(res.checksum, expected_sha)

        # Mismatched checksum
        bad_scene = SceneMetadata(
            scene_id="SCENE_CHECKSUM_BAD",
            acquisition_time=datetime.now(timezone.utc),
            bbox=[0.0, 0.0, 1.0, 1.0],
            checksum="bad_checksum_1234567890",
        )
        mock_resp.read.side_effect = [content, b""]
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.download_scene(bad_scene, destination_dir=self.temp_dir, retries=1)
            self.assertIn("Checksum mismatch", str(ctx.exception))

    def test_14_no_results_handling(self):
        """14. Test handling when OData returns empty product list."""
        adapter = CDSEAdapter(username="test", password="pwd", mock_mode=False)
        adapter.token = "test_token"
        adapter.token_expiry = time.time() + 1000

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"value": []}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = adapter.search_scenes(bbox=self.bbox)
            self.assertEqual(result.total_count, 0)
            self.assertEqual(len(result.scenes), 0)


if __name__ == "__main__":
    unittest.main()
