"""Unit tests for Phase 4 ASFAdapter."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch
import urllib.error

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.asf_adapter import ASFAdapter
from satellite.models import GeoBoundingBox, SceneMetadata, SceneSearchResult


class TestASFAdapter(unittest.TestCase):
    """Test suite for Phase 4 ASFAdapter."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="oceantrace_asf_test_")
        self.bbox = GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_adapter_initialization(self):
        """1. Test initialization with parameters and mock mode."""
        adapter = ASFAdapter(username="earthdata_user", password="secret_earthdata_pwd", mock_mode=False)
        self.assertEqual(adapter.username, "earthdata_user")
        self.assertEqual(adapter.password, "secret_earthdata_pwd")
        self.assertFalse(adapter.mock_mode)

        mock_adapter = ASFAdapter(mock_mode=True)
        self.assertTrue(mock_adapter.mock_mode)

    def test_02_missing_credentials_handling(self):
        """2. Test that missing credentials defaults to None and does not crash on init."""
        with patch.dict(os.environ, {}, clear=True):
            adapter = ASFAdapter(username=None, password=None, mock_mode=False)
            self.assertIsNone(adapter.username)
            self.assertIsNone(adapter.password)

    def test_03_mock_mode_offline(self):
        """3. Test mock mode executes without network access."""
        adapter = ASFAdapter(mock_mode=True)
        self.assertTrue(adapter.mock_mode)

    def test_04_mock_scene_search(self):
        """4. Test mock scene search returns deterministic SceneSearchResult."""
        adapter = ASFAdapter(mock_mode=True)
        result = adapter.search_asf(
            bbox=self.bbox,
            start_time=datetime(2023, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2023, 10, 15, 0, 0, 0, tzinfo=timezone.utc),
            product_type="GRD",
        )
        self.assertIsInstance(result, SceneSearchResult)
        self.assertEqual(result.provider, "ASF")
        self.assertEqual(result.total_count, 1)
        self.assertEqual(len(result.scenes), 1)

    def test_05_mock_metadata_conversion(self):
        """5. Test mock metadata conforms to Phase-1 model specifications."""
        adapter = ASFAdapter(mock_mode=True)
        result = adapter.search_asf(bbox=[2.5, 51.5, 3.2, 52.1])
        scene = result.scenes[0]
        self.assertIsInstance(scene, SceneMetadata)
        self.assertEqual(scene.scene_id, "S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertEqual(scene.platform, "Sentinel-1A")
        self.assertEqual(scene.product_type, "GRD")
        self.assertEqual(scene.bbox_list, [2.5, 51.5, 3.2, 52.1])

    def test_06_bbox_query_construction(self):
        """6. Test bounding box query parameters."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        params = adapter._build_search_params(self.bbox, product_type="GRD")
        self.assertEqual(params["bbox"], "2.5,51.5,3.2,52.1")
        self.assertEqual(params["dataset"], "SENTINEL-1")

    def test_07_date_query_construction(self):
        """7. Test start and end date query parameters."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        start = datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2023, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
        params = adapter._build_search_params(self.bbox, start_time=start, end_time=end)
        self.assertEqual(params["start"], "2023-10-01T12:00:00Z")
        self.assertEqual(params["end"], "2023-10-02T12:00:00Z")

    def test_08_sentinel1_filtering(self):
        """8. Test Sentinel-1 and IW beamMode parameter constraints."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        params = adapter._build_search_params(self.bbox)
        self.assertEqual(params["dataset"], "SENTINEL-1")
        self.assertEqual(params["beamMode"], "IW")

    def test_09_grd_filtering(self):
        """9. Test processing level parameter for GRD."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        params = adapter._build_search_params(self.bbox, product_type="GRD")
        self.assertIn("GRD", params["processingLevel"])

        params_slc = adapter._build_search_params(self.bbox, product_type="SLC")
        self.assertEqual(params_slc["processingLevel"], "SLC")

    def test_10_earthdata_auth_error_handling(self):
        """10. Test Earthdata authentication error handling during download."""
        adapter = ASFAdapter(username="bad_user", password="bad_password", mock_mode=False)
        mock_http_err = urllib.error.HTTPError(
            url="https://datapool.asf.alaska.edu/test.zip",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(b"Unauthorized Earthdata"),
        )
        with patch("urllib.request.urlopen", side_effect=mock_http_err):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.download_scene(
                    SceneMetadata(
                        scene_id="TEST_SCENE",
                        acquisition_time=datetime.now(timezone.utc),
                        bbox=[0.0, 0.0, 1.0, 1.0],
                        download_url="https://datapool.asf.alaska.edu/test.zip",
                    ),
                    destination_dir=self.temp_dir,
                    retries=1,
                )
            self.assertIn("401", str(ctx.exception))
            self.assertNotIn("bad_password", str(ctx.exception))

    def test_11_search_http_error_handling(self):
        """11. Test search HTTP error handling."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        mock_http_err = urllib.error.HTTPError(
            url="https://api.daac.asf.alaska.edu/services/search/param",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(b"Internal Error"),
        )
        with patch("urllib.request.urlopen", side_effect=mock_http_err):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.search_asf(bbox=self.bbox)
            self.assertIn("ASF search failed: HTTP 500", str(ctx.exception))

    def test_12_download_mocked_response(self):
        """12. Test download using mock mode and mocked HTTP streaming."""
        # Mock mode
        adapter = ASFAdapter(mock_mode=True)
        scene = SceneMetadata(
            scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=[2.5, 51.5, 3.2, 52.1],
        )
        downloaded = adapter.download_scene(scene, destination_dir=self.temp_dir)
        self.assertTrue(os.path.isfile(downloaded.file_path))
        self.assertGreater(downloaded.file_size_bytes, 0)
        self.assertIsNotNone(downloaded.checksum)

    def test_13_empty_download_handling(self):
        """13. Test that 0-byte download raises RuntimeError."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.side_effect = [b""]  # Empty stream
        mock_resp.__enter__.return_value = mock_resp

        scene = SceneMetadata(
            scene_id="EMPTY_ASF_SCENE",
            acquisition_time=datetime.now(timezone.utc),
            bbox=[0.0, 0.0, 1.0, 1.0],
            download_url="https://datapool.asf.alaska.edu/empty.zip",
        )

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.download_scene(scene, destination_dir=self.temp_dir, retries=1)
            self.assertIn("is empty (0 bytes)", str(ctx.exception))

    def test_14_sha256_checksum(self):
        """14. Test SHA-256 calculation and verification during download."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        content = b"ASF_BINARY_TIFF_SAMPLE_DATA_12345"
        expected_sha = hashlib.sha256(content).hexdigest()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.side_effect = [content, b""]
        mock_resp.__enter__.return_value = mock_resp

        scene = SceneMetadata(
            scene_id="SCENE_SHA_TEST",
            acquisition_time=datetime.now(timezone.utc),
            bbox=[0.0, 0.0, 1.0, 1.0],
            download_url="https://datapool.asf.alaska.edu/test.zip",
            checksum=expected_sha,
        )

        with patch("urllib.request.urlopen", return_value=mock_resp):
            downloaded = adapter.download_scene(scene, destination_dir=self.temp_dir, retries=1)
            self.assertEqual(downloaded.checksum, expected_sha)

    def test_15_no_results_handling(self):
        """15. Test handling when ASF returns 0 matching scenes."""
        adapter = ASFAdapter(username="test", password="pwd", mock_mode=False)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps([]).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = adapter.search_asf(bbox=self.bbox)
            self.assertEqual(result.total_count, 0)
            self.assertEqual(len(result.scenes), 0)


if __name__ == "__main__":
    unittest.main()
