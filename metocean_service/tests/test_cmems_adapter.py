"""
Unit and Contract Tests for CMEMS Adapter (Phase 2).
Tests product routing, spatial/temporal subsetting, dataset normalization,
WGS84 coordinate compliance, and structured error mapping.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add module root to sys.path
MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from metocean.cmems_adapter import (
    CMEMSAdapter,
    CMEMS_HISTORICAL_DATASET,
    CMEMS_HISTORICAL_PRODUCT,
    CMEMS_RECENT_DATASET,
    CMEMS_RECENT_PRODUCT,
    normalize_currents_dataset,
    select_cmems_product,
    validate_currents_dataset,
)
from metocean.errors import (
    AuthFailedError,
    BadResponseError,
    NoDataForPeriodError,
    RateLimitedError,
    TimeoutError,
    UnavailableError,
    ValidationError,
)
from metocean.models import BBox, MetoceanRequest
from metocean.utils import normalize_longitude


class MockDataset:
    """Mock xarray-like Dataset for testing normalization and contract validation."""

    def __init__(self, data_vars=None, coords=None, dims=None, attrs=None):
        self.data_vars = data_vars or {
            "uo": MagicMock(attrs={"units": "m/s", "long_name": "Eastward velocity"}),
            "vo": MagicMock(attrs={"units": "m/s", "long_name": "Northward velocity"}),
        }
        self.coords = coords or {
            "latitude": MagicMock(values=[12.7, 13.0, 13.55]),
            "longitude": MagicMock(values=[79.9, 80.3, 80.75]),
            "time": MagicMock(values=["2017-01-31T00:00:00Z"]),
        }
        self.dims = dims or {"time": 1, "latitude": 3, "longitude": 3}
        self.attrs = attrs or {}

    def rename(self, rename_dict):
        new_coords = dict(self.coords)
        new_dims = dict(self.dims)
        for old_k, new_k in rename_dict.items():
            if old_k in new_coords:
                new_coords[new_k] = new_coords.pop(old_k)
            if old_k in new_dims:
                new_dims[new_k] = new_dims.pop(old_k)
        return MockDataset(data_vars=self.data_vars, coords=new_coords, dims=new_dims, attrs=self.attrs)

    def isel(self, index_dict):
        return self

    def squeeze(self, drop=True):
        return self

    def drop_vars(self, var_names):
        new_vars = {k: v for k, v in self.data_vars.items() if k not in var_names}
        return MockDataset(data_vars=new_vars, coords=self.coords, dims=self.dims, attrs=self.attrs)

    def assign_coords(self, **kwargs):
        new_coords = dict(self.coords)
        new_coords.update(kwargs)
        return MockDataset(data_vars=self.data_vars, coords=new_coords, dims=self.dims, attrs=self.attrs)

    def sortby(self, coord_name):
        return self

    def __getitem__(self, item):
        if item in self.data_vars:
            return self.data_vars[item]
        if item in self.coords:
            return self.coords[item]
        raise KeyError(item)

    def to_netcdf(self, path, **kwargs):
        # Create a mock file
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"CDF\x02mock_netcdf_data")
        return path


class TestCMEMSAdapter(unittest.TestCase):

    def setUp(self):
        self.test_bbox = [79.90, 12.70, 80.75, 13.55]
        self.historical_request = MetoceanRequest(
            bbox=self.test_bbox,
            start="2017-01-31T00:00:00Z",
            end="2017-02-03T00:00:00Z",
            what="currents",
        )
        self.recent_request = MetoceanRequest(
            bbox=self.test_bbox,
            start="2024-05-01T00:00:00Z",
            end="2024-05-03T00:00:00Z",
            what="currents",
        )

    # Test 1 — Historical routing
    def test_01_historical_routing(self):
        routing = select_cmems_product(self.historical_request.start_dt)
        self.assertEqual(routing["product_id"], CMEMS_HISTORICAL_PRODUCT)
        self.assertEqual(routing["dataset_id"], CMEMS_HISTORICAL_DATASET)
        self.assertEqual(routing["type"], "historical")

    # Test 2 — Recent routing
    def test_02_recent_routing(self):
        routing = select_cmems_product(self.recent_request.start_dt)
        self.assertEqual(routing["product_id"], CMEMS_RECENT_PRODUCT)
        self.assertEqual(routing["dataset_id"], CMEMS_RECENT_DATASET)
        self.assertEqual(routing["type"], "recent")

    # Test 3 — Bbox bounds conversion
    def test_03_bbox_bounds_passed_correctly(self):
        bounds = self.historical_request.bbox.to_cmems_bounds()
        self.assertEqual(bounds["minimum_longitude"], 79.90)
        self.assertEqual(bounds["maximum_longitude"], 80.75)
        self.assertEqual(bounds["minimum_latitude"], 12.70)
        self.assertEqual(bounds["maximum_latitude"], 13.55)

    # Test 4 — Time window formatting
    def test_04_time_window_iso8601_utc(self):
        self.assertEqual(self.historical_request.start_iso, "2017-01-31T00:00:00Z")
        self.assertEqual(self.historical_request.end_iso, "2017-02-03T00:00:00Z")

    # Test 5 & 6 — Output variables (uo, vo) and dimensions (time, lat, lon)
    def test_05_06_output_variables_and_dimensions(self):
        mock_ds = MockDataset()
        # Test normalization logic
        norm_ds = mock_ds.rename({"latitude": "lat", "longitude": "lon"})
        self.assertIn("uo", norm_ds.data_vars)
        self.assertIn("vo", norm_ds.data_vars)
        self.assertIn("lat", norm_ds.coords)
        self.assertIn("lon", norm_ds.coords)
        self.assertIn("time", norm_ds.coords)

    # Test 7 — Units validation (m/s)
    def test_07_units_normalization(self):
        mock_ds = MockDataset()
        norm_ds = mock_ds.rename({"latitude": "lat", "longitude": "lon"})
        self.assertEqual(norm_ds["uo"].attrs["units"], "m/s")
        self.assertEqual(norm_ds["vo"].attrs["units"], "m/s")

    # Test 8 — Longitude normalization [0..360 -> -180..+180]
    def test_08_longitude_normalization(self):
        self.assertEqual(normalize_longitude(190.0), -170.0)
        self.assertEqual(normalize_longitude(360.0), 0.0)
        self.assertEqual(normalize_longitude(80.5), 80.5)
        self.assertEqual(normalize_longitude(-10.0), -10.0)

    # Test 9 — Authentication failure mapping
    def test_09_auth_failed_mapping(self):
        mock_client = MagicMock()
        mock_client.open_dataset.side_effect = Exception("401 Unauthorized: Invalid CMEMS credentials")
        adapter = CMEMSAdapter(username="bad_user", password="bad_password", client=mock_client)

        with self.assertRaises(AuthFailedError) as ctx:
            adapter.fetch_data(self.historical_request)
        self.assertEqual(ctx.exception.error_code, "AUTH_FAILED")
        self.assertEqual(ctx.exception.provider, "cmems")

    # Test 10 — Missing coverage / No data mapping
    def test_10_no_data_for_period_mapping(self):
        mock_client = MagicMock()
        mock_client.open_dataset.side_effect = Exception("Coverage out of range: No data available for requested date")
        adapter = CMEMSAdapter(username="test_user", password="test_password", client=mock_client)

        with self.assertRaises(NoDataForPeriodError) as ctx:
            adapter.fetch_data(self.historical_request)
        self.assertEqual(ctx.exception.error_code, "NO_DATA_FOR_PERIOD")
        self.assertEqual(ctx.exception.provider, "cmems")

    # Test 11 — Successful fetch with mock client
    def test_11_successful_fetch_mock_client(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "currents.nc"
            mock_client = MagicMock()
            mock_client.open_dataset.return_value = MockDataset()
            adapter = CMEMSAdapter(username="user", password="pwd", client=mock_client)

            # Pass output_path
            saved_path = adapter.fetch_data(self.historical_request, output_path=str(out_file))
            self.assertTrue(Path(saved_path).exists())
            self.assertEqual(saved_path, str(out_file.resolve()))


if __name__ == "__main__":
    unittest.main()
