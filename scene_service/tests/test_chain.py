"""Unit tests for Phase 5 SceneRetrievalChain."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.asf_adapter import ASFAdapter
from satellite.cache import LocalSceneCache
from satellite.cdse_adapter import CDSEAdapter
from satellite.chain import SceneRetrievalChain
from satellite.models import (
    GeoBoundingBox,
    RetrievalResponse,
    SceneMetadata,
    SceneSearchResult,
)


class TestSceneRetrievalChain(unittest.TestCase):
    """Test suite for Phase 5 SceneRetrievalChain orchestration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="oceantrace_chain_test_")
        self.cache = LocalSceneCache(cache_dir=os.path.join(self.temp_dir, "cache"))
        self.dummy_raster = b"TEST_TIFF_BINARY_DATA_FOR_CHAIN_12345"
        self.bbox = GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_metadata(self, scene_id: str = "S1A_TEST_001"):
        return SceneMetadata(
            scene_id=scene_id,
            platform="Sentinel-1A",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=self.bbox,
            product_type="GRD",
            polarisation="VV+VH",
            orbit_direction="DESCENDING",
        )

    def test_01_cache_hit_bypasses_remote_providers(self):
        """1. Test that a valid cache entry returns immediately without calling CDSE or ASF."""
        scene_id = "SCENE_CACHE_HIT"
        meta = self._create_sample_metadata(scene_id)
        self.cache.put(meta, raster_source=self.dummy_raster)

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_asf = MagicMock(spec=ASFAdapter)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        res = chain.retrieve_scene(scene_id)
        self.assertTrue(res.success)
        self.assertEqual(res.source_provider, "CACHE")
        self.assertEqual(res.scene_id, scene_id)
        self.assertIsNotNone(res.geotiff_path)
        self.assertTrue(os.path.isfile(res.geotiff_path))

        mock_cdse.download_scene.assert_not_called()
        mock_asf.download_scene.assert_not_called()

    def test_02_cache_miss_cdse_success(self):
        """2. Test that cache miss calls CDSE and returns CDSE result."""
        scene_id = "SCENE_CDSE_PRIMARY"
        meta = self._create_sample_metadata(scene_id)

        # Create dummy downloaded file
        temp_dl_file = os.path.join(self.temp_dir, f"{scene_id}.tif")
        with open(temp_dl_file, "wb") as f:
            f.write(self.dummy_raster)
        meta.file_path = temp_dl_file

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.return_value = meta

        mock_asf = MagicMock(spec=ASFAdapter)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        res = chain.retrieve_scene(scene_id)
        self.assertTrue(res.success)
        self.assertEqual(res.source_provider, "CDSE")
        self.assertEqual(res.scene_id, scene_id)
        mock_cdse.download_scene.assert_called_once()
        mock_asf.download_scene.assert_not_called()

    def test_03_cdse_success_saved_to_cache(self):
        """3. Test that successful CDSE download is stored in LocalSceneCache."""
        scene_id = "SCENE_CDSE_CACHED"
        meta = self._create_sample_metadata(scene_id)

        temp_dl_file = os.path.join(self.temp_dir, f"{scene_id}.tif")
        with open(temp_dl_file, "wb") as f:
            f.write(self.dummy_raster)
        meta.file_path = temp_dl_file

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.return_value = meta
        mock_asf = MagicMock(spec=ASFAdapter)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        res = chain.retrieve_scene(scene_id)
        self.assertTrue(res.success)

        # Verify it now exists in LocalSceneCache
        self.assertTrue(self.cache.has_scene(scene_id))
        cached_item = self.cache.get(scene_id)
        self.assertIsNotNone(cached_item)
        self.assertEqual(cached_item.scene_id, scene_id)

    def test_04_cache_miss_cdse_failure_asf_success(self):
        """4. Test fallback to ASF when CDSE fails."""
        scene_id = "SCENE_ASF_FALLBACK"
        meta = self._create_sample_metadata(scene_id)

        temp_dl_file = os.path.join(self.temp_dir, f"{scene_id}.tif")
        with open(temp_dl_file, "wb") as f:
            f.write(self.dummy_raster)
        meta.file_path = temp_dl_file

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.side_effect = RuntimeError("CDSE service unavailable (503)")

        mock_asf = MagicMock(spec=ASFAdapter)
        mock_asf.download_scene.return_value = meta

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        res = chain.retrieve_scene(scene_id)
        self.assertTrue(res.success)
        self.assertEqual(res.source_provider, "ASF")
        self.assertEqual(res.scene_id, scene_id)

        mock_cdse.download_scene.assert_called_once()
        mock_asf.download_scene.assert_called_once()

    def test_05_asf_success_saved_to_cache(self):
        """5. Test that successful ASF download is stored in LocalSceneCache."""
        scene_id = "SCENE_ASF_CACHED"
        meta = self._create_sample_metadata(scene_id)

        temp_dl_file = os.path.join(self.temp_dir, f"{scene_id}.tif")
        with open(temp_dl_file, "wb") as f:
            f.write(self.dummy_raster)
        meta.file_path = temp_dl_file

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.side_effect = Exception("CDSE network error")

        mock_asf = MagicMock(spec=ASFAdapter)
        mock_asf.download_scene.return_value = meta

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        res = chain.retrieve_scene(scene_id)
        self.assertTrue(res.success)

        # Verify entry in cache
        self.assertTrue(self.cache.has_scene(scene_id))

    def test_06_all_providers_failure(self):
        """6. Test structured failure response when cache, CDSE, and ASF all fail."""
        scene_id = "SCENE_TOTAL_FAILURE"

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.side_effect = RuntimeError("CDSE connection timeout")

        mock_asf = MagicMock(spec=ASFAdapter)
        mock_asf.download_scene.side_effect = RuntimeError("ASF granule not found (404)")

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        res = chain.retrieve_scene(scene_id)
        self.assertFalse(res.success)
        self.assertEqual(res.scene_id, scene_id)
        self.assertIsNone(res.source_provider)
        self.assertIsNone(res.metadata)
        self.assertIsNone(res.geotiff_path)
        self.assertIn("All providers failed", res.error_message)
        self.assertIn("CDSE connection timeout", res.error_message)
        self.assertIn("ASF granule not found", res.error_message)

    def test_07_source_provider_tags(self):
        """7. Test source_provider string tag accuracy."""
        # CACHE tag tested in test_01
        # CDSE tag tested in test_02
        # ASF tag tested in test_04
        pass

    def test_08_scene_id_propagation(self):
        """8. Test scene_id propagation throughout responses."""
        scene_id = "S1A_IW_GRDH_PROPAGATE_TEST"
        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.side_effect = Exception("err")
        mock_asf = MagicMock(spec=ASFAdapter)
        mock_asf.download_scene.side_effect = Exception("err")

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )
        res = chain.retrieve_scene(scene_id)
        self.assertEqual(res.scene_id, scene_id)

    def test_09_search_bbox_propagation(self):
        """9. Test query bbox propagation in search_scenes."""
        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.search_scenes.return_value = SceneSearchResult(
            query_bbox=self.bbox, total_count=1, scenes=[self._create_sample_metadata()], provider="CDSE"
        )
        mock_asf = MagicMock(spec=ASFAdapter)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        search_res = chain.search_scenes(bbox=self.bbox)
        self.assertEqual(search_res.query_bbox, self.bbox)
        self.assertEqual(search_res.provider, "CDSE")

    def test_10_search_time_range_propagation(self):
        """10. Test start/end time propagation to fallback search."""
        start = datetime(2023, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2023, 10, 15, 0, 0, 0, tzinfo=timezone.utc)

        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.search_scenes.side_effect = Exception("CDSE search down")

        mock_asf = MagicMock(spec=ASFAdapter)
        mock_asf.search_asf.return_value = SceneSearchResult(
            query_bbox=self.bbox,
            query_start=start,
            query_end=end,
            total_count=1,
            scenes=[self._create_sample_metadata()],
            provider="ASF",
        )

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        search_res = chain.search_scenes(bbox=self.bbox, start_time=start, end_time=end)
        self.assertEqual(search_res.provider, "ASF")
        self.assertEqual(search_res.query_start, start)
        self.assertEqual(search_res.query_end, end)

    def test_11_mock_offline_chain_execution(self):
        """11. Test mock/offline chain execution with real mock adapters."""
        mock_cdse = CDSEAdapter(mock_mode=True)
        mock_asf = ASFAdapter(mock_mode=True)

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        # First retrieval should hit CDSE in mock mode
        res1 = chain.retrieve_scene("S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertTrue(res1.success)
        self.assertEqual(res1.source_provider, "CDSE")

        # Second retrieval should hit LocalSceneCache
        res2 = chain.retrieve_scene("S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertTrue(res2.success)
        self.assertEqual(res2.source_provider, "CACHE")

    def test_12_provider_exceptions_do_not_crash_chain(self):
        """12. Test that various uncaught exceptions in providers are safely caught."""
        mock_cdse = MagicMock(spec=CDSEAdapter)
        mock_cdse.download_scene.side_effect = TypeError("Unexpected null pointer in parser")

        mock_asf = MagicMock(spec=ASFAdapter)
        mock_asf.download_scene.side_effect = ConnectionResetError("Connection reset by peer")

        chain = SceneRetrievalChain(
            cdse_adapter=mock_cdse, asf_adapter=mock_asf, cache=self.cache
        )

        # Must not raise unhandled exception; must return structured failure response
        res = chain.retrieve_scene("SCENE_UNCAUGHT_TEST")
        self.assertIsInstance(res, RetrievalResponse)
        self.assertFalse(res.success)
        self.assertIn("All providers failed", res.error_message)


if __name__ == "__main__":
    unittest.main()
