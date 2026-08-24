"""Unit tests for Phase 1 Satellite Scene Service data models."""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

# Add module root to sys.path to enable direct importing
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from pydantic import ValidationError
from satellite.models import (
    GeoBoundingBox,
    ProviderHealth,
    RetrievalResponse,
    SceneMetadata,
    SceneSearchResult,
)


class TestSatelliteModels(unittest.TestCase):
    """Test suite for Phase 1 Pydantic data models."""

    def test_01_valid_geo_bounding_box(self):
        """1. Test creation of valid GeoBoundingBox."""
        bbox = GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1)
        self.assertEqual(bbox.min_lon, 2.5)
        self.assertEqual(bbox.min_lat, 51.5)
        self.assertEqual(bbox.max_lon, 3.2)
        self.assertEqual(bbox.max_lat, 52.1)

    def test_02_invalid_longitude(self):
        """2. Test that longitude outside [-180, 180] raises ValidationError."""
        with self.assertRaises(ValidationError):
            GeoBoundingBox(min_lon=-181.0, min_lat=10.0, max_lon=20.0, max_lat=30.0)
        with self.assertRaises(ValidationError):
            GeoBoundingBox(min_lon=10.0, min_lat=10.0, max_lon=185.0, max_lat=30.0)

    def test_03_invalid_latitude(self):
        """3. Test that latitude outside [-90, 90] raises ValidationError."""
        with self.assertRaises(ValidationError):
            GeoBoundingBox(min_lon=10.0, min_lat=-95.0, max_lon=20.0, max_lat=30.0)
        with self.assertRaises(ValidationError):
            GeoBoundingBox(min_lon=10.0, min_lat=10.0, max_lon=20.0, max_lat=95.0)

    def test_04_invalid_bbox_ordering(self):
        """4. Test that min > max raises ValidationError."""
        with self.assertRaises(ValidationError):
            GeoBoundingBox(min_lon=25.0, min_lat=10.0, max_lon=20.0, max_lat=30.0)
        with self.assertRaises(ValidationError):
            GeoBoundingBox(min_lon=10.0, min_lat=35.0, max_lon=20.0, max_lat=30.0)

    def test_05_bbox_list_conversion(self):
        """5. Test Bbox list conversion [W, S, E, N] and from_list."""
        bbox = GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1)
        coords = bbox.to_list()
        self.assertEqual(coords, [2.5, 51.5, 3.2, 52.1])

        # Test from_list
        recreated = GeoBoundingBox.from_list([2.5, 51.5, 3.2, 52.1])
        self.assertEqual(recreated, bbox)

        # Test WKT generation
        wkt = bbox.to_wkt()
        self.assertIn("POLYGON((2.5 51.5, 3.2 51.5, 3.2 52.1, 2.5 52.1, 2.5 51.5))", wkt)

    def test_06_valid_scene_metadata(self):
        """6. Test valid SceneMetadata creation."""
        meta = SceneMetadata(
            scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
            platform="Sentinel-1A",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=[2.5, 51.5, 3.2, 52.1],
            product_type="GRD",
            polarisation="VV+VH",
            orbit_direction="DESCENDING",
        )
        self.assertEqual(meta.scene_id, "S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertEqual(meta.platform, "Sentinel-1A")
        self.assertEqual(meta.product_type, "GRD")
        self.assertEqual(meta.polarisation, "VV+VH")
        self.assertEqual(meta.orbit_direction, "DESCENDING")
        self.assertEqual(meta.bbox_list, [2.5, 51.5, 3.2, 52.1])

    def test_07_utc_acquisition_time_validation(self):
        """7. Test UTC acquisition time validation."""
        # Naive datetime should automatically be set to UTC
        naive_dt = datetime(2023, 10, 12, 17, 25, 30)
        meta = SceneMetadata(
            scene_id="TEST_SCENE",
            acquisition_time=naive_dt,
            bbox=[0.0, 0.0, 1.0, 1.0],
        )
        self.assertIsNotNone(meta.acquisition_time.tzinfo)
        self.assertEqual(meta.acquisition_time.tzinfo, timezone.utc)

    def test_08_scene_metadata_json_serialization(self):
        """8. Test SceneMetadata JSON serialization."""
        meta = SceneMetadata(
            scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
            platform="Sentinel-1A",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=GeoBoundingBox(min_lon=2.5, min_lat=51.5, max_lon=3.2, max_lat=52.1),
        )
        dumped_json = meta.model_dump_json()
        self.assertIsInstance(dumped_json, str)
        parsed = json.loads(dumped_json)
        self.assertEqual(parsed["scene_id"], "S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertEqual(parsed["platform"], "Sentinel-1A")
        self.assertEqual(parsed["acquisition_time"], "2023-10-12T17:25:30Z")

    def test_09_scene_search_result_creation(self):
        """9. Test SceneSearchResult creation."""
        scene = SceneMetadata(
            scene_id="S1A_SCENE_1",
            acquisition_time=datetime(2023, 10, 12, 12, 0, 0, tzinfo=timezone.utc),
            bbox=[1.0, 50.0, 2.0, 51.0],
        )
        search_res = SceneSearchResult(
            query_bbox=GeoBoundingBox(min_lon=1.0, min_lat=50.0, max_lon=2.0, max_lat=51.0),
            query_start=datetime(2023, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
            query_end=datetime(2023, 10, 15, 0, 0, 0, tzinfo=timezone.utc),
            total_count=1,
            scenes=[scene],
            provider="CDSE",
        )
        self.assertEqual(search_res.total_count, 1)
        self.assertEqual(search_res.provider, "CDSE")
        self.assertEqual(len(search_res.scenes), 1)
        self.assertEqual(search_res.scenes[0].scene_id, "S1A_SCENE_1")

    def test_10_provider_health_creation(self):
        """10. Test ProviderHealth creation."""
        health = ProviderHealth(
            provider_name="CDSE",
            is_available=True,
            status="UP",
            latency_ms=125.4,
            details={"endpoint": "https://dataspace.copernicus.eu/odata/v1"},
        )
        self.assertEqual(health.provider_name, "CDSE")
        self.assertTrue(health.is_available)
        self.assertEqual(health.status, "UP")
        self.assertEqual(health.latency_ms, 125.4)

    def test_11_retrieval_response_creation(self):
        """11. Test RetrievalResponse creation."""
        meta = SceneMetadata(
            scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
            acquisition_time=datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
            bbox=[2.5, 51.5, 3.2, 52.1],
        )
        response = RetrievalResponse(
            success=True,
            scene_id="S1A_IW_GRDH_1SDV_20231012T172530",
            source_provider="CDSE",
            metadata=meta,
            geotiff_path="/data/cache/satellite/S1A_IW_GRDH_1SDV_20231012T172530.tif",
            error_message=None,
        )
        self.assertTrue(response.success)
        self.assertEqual(response.source_provider, "CDSE")
        self.assertIsNotNone(response.metadata)
        self.assertEqual(response.metadata.scene_id, "S1A_IW_GRDH_1SDV_20231012T172530")

    def test_12_contract_compatibility_mock_scene(self):
        """12. Test contract compatibility using existing contracts/mocks/mock_scene.json (read-only)."""
        project_root = os.path.abspath(os.path.join(module_root, ".."))
        mock_scene_path = os.path.join(project_root, "contracts", "mocks", "mock_scene.json")

        self.assertTrue(
            os.path.exists(mock_scene_path),
            f"Expected contract mock file at {mock_scene_path}",
        )

        with open(mock_scene_path, "r", encoding="utf-8") as f:
            raw_mock = json.load(f)

        # Validate mock data against SceneMetadata model
        scene_model = SceneMetadata.model_validate(raw_mock)
        self.assertEqual(scene_model.scene_id, "S1A_IW_GRDH_1SDV_20231012T172530")
        self.assertEqual(scene_model.platform, "Sentinel-1A")
        self.assertEqual(scene_model.bbox_list, [2.5, 51.5, 3.2, 52.1])
        self.assertEqual(
            scene_model.acquisition_time,
            datetime(2023, 10, 12, 17, 25, 30, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
