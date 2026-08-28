"""
Unit and Contract Tests for ECMWF ERA5 Wind Adapter (Phase 3).
Tests CDS API payload formatting, area conversion, u10/v10 variable normalization,
dimensions, units, and structured error mappings.
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

from metocean.era5_adapter import (
    ERA5Adapter,
    ERA5_DATASET_NAME,
    ERA5_WIND_VARIABLES,
    build_cds_request_payload,
    normalize_wind_dataset,
    validate_wind_dataset,
)
from metocean.errors import (
    AuthFailedError,
    BadResponseError,
    LicenceNotAcceptedError,
    NoDataForPeriodError,
    RateLimitedError,
    TimeoutError,
    UnavailableError,
    ValidationError,
)
from metocean.models import BBox, MetoceanRequest


class MockWindDataset:
    """Mock xarray-like Dataset for testing ERA5 wind normalization."""

    def __init__(self, data_vars=None, coords=None, dims=None, attrs=None):
        self.data_vars = data_vars or {
            "u10": MagicMock(attrs={"units": "m/s", "long_name": "10 metre U wind component"}),
            "v10": MagicMock(attrs={"units": "m/s", "long_name": "10 metre V wind component"}),
        }
        self.coords = coords or {
            "latitude": MagicMock(values=[12.7, 13.0, 13.55]),
            "longitude": MagicMock(values=[79.9, 80.3, 80.75]),
            "valid_time": MagicMock(values=["2017-01-31T00:00:00Z"]),
        }
        self.dims = dims or {"valid_time": 1, "latitude": 3, "longitude": 3}
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

        return MockWindDataset(data_vars=new_vars, coords=new_coords, dims=new_dims, attrs=self.attrs)

    def isel(self, index_dict):
        return self

    def squeeze(self, drop=True):
        return self

    def drop_vars(self, var_names):
        new_vars = {k: v for k, v in self.data_vars.items() if k not in var_names}
        return MockWindDataset(data_vars=new_vars, coords=self.coords, dims=self.dims, attrs=self.attrs)

    def assign_coords(self, **kwargs):
        new_coords = dict(self.coords)
        new_coords.update(kwargs)
        return MockWindDataset(data_vars=self.data_vars, coords=new_coords, dims=self.dims, attrs=self.attrs)

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
            f.write(b"CDF\x02mock_wind_netcdf_data")
        return path


class TestERA5Adapter(unittest.TestCase):

    def setUp(self):
        self.test_bbox = [79.90, 12.70, 80.75, 13.55]
        self.request = MetoceanRequest(
            bbox=self.test_bbox,
            start="2017-01-31T00:00:00Z",
            end="2017-02-01T00:00:00Z",
            what="wind",
        )

    # Test 1 — CDS Area Formatting: [North, West, South, East]
    def test_01_cds_area_formatting(self):
        payload = build_cds_request_payload(self.request)
        self.assertEqual(payload["area"], [13.55, 79.90, 12.70, 80.75])

    # Test 2 — Variables requested from CDS
    def test_02_cds_variables(self):
        payload = build_cds_request_payload(self.request)
        self.assertIn("10m_u_component_of_wind", payload["variable"])
        self.assertIn("10m_v_component_of_wind", payload["variable"])

    # Test 3 — Time slicing elements in payload
    def test_03_cds_time_elements(self):
        payload = build_cds_request_payload(self.request)
        self.assertIn("2017", payload["year"])
        self.assertIn("01", payload["month"])
        self.assertIn("00:00", payload["time"])

    # Test 4 — Normalization of variables (u10, v10)
    def test_04_normalize_variables(self):
        raw_ds = MockWindDataset(
            data_vars={
                "10m_u_component_of_wind": MagicMock(attrs={"units": "m/s"}),
                "10m_v_component_of_wind": MagicMock(attrs={"units": "m/s"}),
            }
        )
        norm_ds = normalize_wind_dataset(raw_ds)
        self.assertIn("u10", norm_ds.data_vars)
        self.assertIn("v10", norm_ds.data_vars)

    # Test 5 — Normalization of dimensions (time, lat, lon)
    def test_05_normalize_dimensions(self):
        raw_ds = MockWindDataset()
        norm_ds = normalize_wind_dataset(raw_ds)
        self.assertIn("lat", norm_ds.coords)
        self.assertIn("lon", norm_ds.coords)
        self.assertIn("time", norm_ds.coords)

    # Test 6 — Units validation (m/s)
    def test_06_units_validation(self):
        norm_ds = normalize_wind_dataset(MockWindDataset())
        self.assertEqual(norm_ds["u10"].attrs["units"], "m/s")
        self.assertEqual(norm_ds["v10"].attrs["units"], "m/s")

    # Test 7 — Authentication failure mapping
    def test_07_auth_failed_mapping(self):
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = Exception("401 Unauthorized: Missing or invalid CDS API key")
        adapter = ERA5Adapter(key="invalid_key", client=mock_client)

        with self.assertRaises(AuthFailedError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "AUTH_FAILED")
        self.assertEqual(ctx.exception.provider, "era5")

    # Test 8 — Licence / Terms not accepted mapping
    def test_08_licence_not_accepted_mapping(self):
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = Exception("Licence terms for 'reanalysis-era5-single-levels' not accepted.")
        adapter = ERA5Adapter(client=mock_client)

        with self.assertRaises(LicenceNotAcceptedError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "LICENCE_NOT_ACCEPTED")
        self.assertEqual(ctx.exception.provider, "era5")

    # Test 9 — Timeout error mapping
    def test_09_timeout_mapping(self):
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = Exception("Request timed out in CDS queue after 300s")
        adapter = ERA5Adapter(client=mock_client)

        with self.assertRaises(TimeoutError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "TIMEOUT")
        self.assertEqual(ctx.exception.provider, "era5")

    # Test 10 — Service unavailable mapping
    def test_10_unavailable_mapping(self):
        mock_client = MagicMock()
        mock_client.retrieve.side_effect = Exception("503 Service Unavailable: CDS backend server down")
        adapter = ERA5Adapter(client=mock_client)

        with self.assertRaises(UnavailableError) as ctx:
            adapter.fetch_data(self.request)
        self.assertEqual(ctx.exception.error_code, "UNAVAILABLE")
        self.assertEqual(ctx.exception.provider, "era5")

    # Test 11 — Successful fetch with mock client
    def test_11_successful_fetch_mock_client(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "wind.nc"
            mock_client = MagicMock()
            mock_client.retrieve.return_value = MockWindDataset()
            adapter = ERA5Adapter(client=mock_client)

            saved_path = adapter.fetch_data(self.request, output_path=str(out_file))
            self.assertTrue(Path(saved_path).exists())
            self.assertEqual(saved_path, str(out_file.resolve()))


if __name__ == "__main__":
    unittest.main()
