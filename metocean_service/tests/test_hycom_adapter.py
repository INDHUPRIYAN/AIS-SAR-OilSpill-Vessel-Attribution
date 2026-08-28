"""
Unit and Contract Tests for HYCOM Ocean Currents Fallback Adapter (Phase 4).
Tests 0..360 longitude conversion, variable renaming (water_u/v -> uo/vo),
dimensions, units (m/s), and structured error mappings.
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

from metocean.hycom_adapter import (
    HYCOMAdapter,
    HYCOM_OPENDAP_URL,
    get_hycom_bbox_360,
    normalize_hycom_dataset,
    validate_hycom_dataset,
)
from metocean.errors import (
    AuthFailedError,
    BadResponseError,
    NoDataForPeriodError,
    TimeoutError,
    UnavailableError,
    ValidationError,
)
from metocean.models import BBox, MetoceanRequest


class MockHYCOMDataset:
    """Mock xarray-like Dataset for testing HYCOM current normalization."""

    def __init__(self, data_vars=None, coords=None, dims=None, attrs=None):
        self.data_vars = data_vars or {
            "water_u": MagicMock(attrs={"units": "m/s", "long_name": "Eastward water velocity"}),
            "water_v": MagicMock(attrs={"units": "m/s", "long_name": "Northward water velocity"}),
        }
        self.coords = coords or {
            "latitude": MagicMock(values=[12.7, 13.0, 13.55]),
            "longitude": MagicMock(values=[79.9, 80.3, 80.75]),
            "time": MagicMock(values=["2017-01-31T00:00:00Z"]),
        }
        self.dims = dims or {"time": 1, "latitude": 3, "longitude": 3}
        self.attrs = attrs or {}

    def rename(self, rename_dict):
        new_vars = dict(self.data_vars)
        new_coords = dict(self.coords)
        new_dims = dict(self.dims)

        for old_k, new_k in rename_dict.items():
            if old_k in new_vars:
                new_vars[new_k] = new_vars.pop(old_k)
            if old_k in new_coords:
                new_coords[new_k] = new_coords.pop(old_k)
            if old_k in new_dims:
                new_dims[new_k] = new_dims.pop(old_k)

        return MockHYCOMDataset(data_vars=new_vars, coords=new_coords, dims=new_dims, attrs=self.attrs)

    def isel(self, index_dict):
        return self

    def squeeze(self, drop=True):
        return self

    def drop_vars(self, var_names):
        new_vars = {k: v for k, v in self.data_vars.items() if k not in var_names}
        return MockHYCOMDataset(data_vars=new_vars, coords=self.coords, dims=self.dims, attrs=self.attrs)

    def assign_coords(self, **kwargs):
        new_coords = dict(self.coords)
        new_coords.update(kwargs)
        return MockHYCOMDataset(data_vars=self.data_vars, coords=new_coords, dims=self.dims, attrs=self.attrs)

    def sortby(self, coord_name):
        return self

    def __getitem__(self, item):
        if item in self.data_vars:
            return self.data_vars[item]
        if item in self.coords:
            return self.coords[item]
        raise KeyError(item)

    def to_netcdf(self, path, **kwargs):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"CDF\x02mock_hycom_netcdf_data")
        return path


class TestHYCOMAdapter(unittest.TestCase):

    def setUp(self):
        self.test_bbox = [79.90, 12.70, 80.75, 13.55]
        self.request = MetoceanRequest(
            bbox=self.test_bbox,
            start="2017-01-31T00:00:00Z",
            end="2017-02-01T00:00:00Z",
            what="currents",
        )

    # Test 1 — Bbox to 0..360 longitude conversion
    def test_01_bbox_to_360_conversion(self):
        b1 = BBox(79.90, 12.70, 80.75, 13.55)
        self.assertEqual(get_hycom_bbox_360(b1), (79.90, 12.70, 80.75, 13.55))

        b2 = BBox(-10.0, 10.0, 20.0, 30.0)
        self.assertEqual(get_hycom_bbox_360(b2), (350.0, 10.0, 20.0, 30.0))

    # Test 2 — Normalization of variables (water_u, water_v -> uo, vo)
    def test_02_normalize_variables(self):
        raw_ds = MockHYCOMDataset()
        norm_ds = normalize_hycom_dataset(raw_ds)
        self.assertIn("uo", norm_ds.data_vars)
        self.assertIn("vo", norm_ds.data_vars)

    # Test 3 — Normalization of dimensions (time, lat, lon)
    def test_03_normalize_dimensions(self):
        raw_ds = MockHYCOMDataset()
        norm_ds = normalize_hycom_dataset(raw_ds)
        self.assertIn("lat", norm_ds.coords)
        self.assertIn("lon", norm_ds.coords)
        self.assertIn("time", norm_ds.coords)

    # Test 4 — Units validation (m/s)
    def test_04_units_validation(self):
        norm_ds = normalize_hycom_dataset(MockHYCOMDataset())
        self.assertEqual(norm_ds["uo"].attrs["units"], "m/s")
        self.assertEqual(norm_ds["vo"].attrs["units"], "m/s")

    # Test 5 — Longitude conversion back from 0..360 to -180..+180
    def test_05_normalize_longitude_back(self):
        raw_ds = MockHYCOMDataset(
            coords={
                "latitude": MagicMock(values=[12.7, 13.0]),
                "longitude": MagicMock(values=[350.0, 355.0]),
                "time": MagicMock(values=["2017-01-31T00:00:00Z"]),
            }
        )
        norm_ds = normalize_hycom_dataset(raw_ds)
        self.assertIn("lon", norm_ds.coords)

    # Test 6 — Timeout error mapping
    def test_06_timeout_mapping(self):
        mock_client = MagicMock()
        mock_client.open_dataset.side_effect = Exception("Read timed out on OPeNDAP DODS connection")
        adapter = HYCOMAdapter(client=mock_client)

        with self.assertRaises(TimeoutError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "TIMEOUT")
        self.assertEqual(ctx.exception.provider, "hycom")

    # Test 7 — Server unavailable mapping
    def test_07_unavailable_mapping(self):
        mock_client = MagicMock()
        mock_client.open_dataset.side_effect = Exception("503 Service Unavailable: THREDDS server down")
        adapter = HYCOMAdapter(client=mock_client)

        with self.assertRaises(UnavailableError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "UNAVAILABLE")
        self.assertEqual(ctx.exception.provider, "hycom")

    # Test 8 — No data / 404 error mapping
    def test_08_no_data_mapping(self):
        mock_client = MagicMock()
        mock_client.open_dataset.side_effect = Exception("404 Not Found: Requested HYCOM experiment catalog out of bounds")
        adapter = HYCOMAdapter(client=mock_client)

        with self.assertRaises(NoDataForPeriodError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "NO_DATA_FOR_PERIOD")
        self.assertEqual(ctx.exception.provider, "hycom")

    # Test 9 — Successful fetch with mock client
    def test_09_successful_fetch_mock_client(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "currents.nc"
            mock_client = MagicMock()
            mock_client.open_dataset.return_value = MockHYCOMDataset()
            adapter = HYCOMAdapter(client=mock_client)

            saved_path = adapter.fetch_data(self.request, output_path=str(out_file))
            self.assertTrue(Path(saved_path).exists())
            self.assertEqual(saved_path, str(out_file.resolve()))


if __name__ == "__main__":
    unittest.main()
