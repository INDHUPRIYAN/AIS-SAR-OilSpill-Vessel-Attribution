"""
Unit and Contract Tests for Metocean Fallback Chain, Circuit Breaker, and Provider Status (Phase 5).
Tests independent chains per data type, graceful degradation, circuit breaker state machine,
and provider_status.json telemetry.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Add module root to sys.path
MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from metocean.cache import MetoceanCache
from metocean.chain import MetoceanChain
from metocean.errors import (
    AuthFailedError,
    LicenceNotAcceptedError,
    NoDataForPeriodError,
    RateLimitedError,
    TimeoutError,
    UnavailableError,
)
from metocean.models import BBox, MetoceanRequest, MetoceanResponse
from metocean.status import CircuitBreaker, CircuitBreakerState, ProviderStatusTracker


class TestMetoceanChain(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.tmp_dir.name)

        self.test_bbox = [79.90, 12.70, 80.75, 13.55]
        self.request_both = MetoceanRequest(
            bbox=self.test_bbox,
            start="2017-01-31T00:00:00Z",
            end="2017-02-01T00:00:00Z",
            what="both",
            output_dir=str(self.output_dir),
        )

        self.mock_cmems = MagicMock()
        self.mock_hycom = MagicMock()
        self.mock_era5 = MagicMock()
        self.mock_openmeteo = MagicMock()
        self.mock_cache = MagicMock()
        self.status_tracker = ProviderStatusTracker(output_dir=self.output_dir)

        # Default: all adapters succeed returning dummy file paths
        self.mock_cmems.fetch_data.return_value = str(self.output_dir / "currents_cmems.nc")
        self.mock_hycom.fetch_data.return_value = str(self.output_dir / "currents_hycom.nc")
        self.mock_era5.fetch_data.return_value = str(self.output_dir / "wind_era5.nc")
        self.mock_openmeteo.fetch_data.return_value = str(self.output_dir / "wind_openmeteo.nc")
        self.mock_cache.get_currents.return_value = None
        self.mock_cache.get_wind.return_value = None
        self.mock_cache.get_static_fallback.return_value = None

        self.chain = MetoceanChain(
            cmems_adapter=self.mock_cmems,
            hycom_adapter=self.mock_hycom,
            era5_adapter=self.mock_era5,
            openmeteo_adapter=self.mock_openmeteo,
            cache=self.mock_cache,
            status_tracker=self.status_tracker,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    # Test 1 — CMEMS success -> CMEMS active
    def test_01_cmems_success(self):
        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        res = self.chain.fetch_currents(req)
        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "CMEMS")
        self.assertEqual(self.mock_cmems.fetch_data.call_count, 1)
        self.assertEqual(self.mock_hycom.fetch_data.call_count, 0)

    # Test 2 & 3 — CMEMS failure -> HYCOM attempted & succeeds -> HYCOM active
    def test_02_03_cmems_failure_hycom_success(self):
        self.mock_cmems.fetch_data.side_effect = AuthFailedError("CMEMS 401 Unauthorized", provider="cmems")
        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        res = self.chain.fetch_currents(req)

        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "HYCOM")
        self.assertEqual(self.mock_cmems.fetch_data.call_count, 1)
        self.assertEqual(self.mock_hycom.fetch_data.call_count, 1)

    # Test 4 — CMEMS + HYCOM failure -> cache attempted
    def test_04_cmems_hycom_failure_cache_attempted(self):
        self.mock_cmems.fetch_data.side_effect = TimeoutError("CMEMS timeout", provider="cmems")
        self.mock_hycom.fetch_data.side_effect = UnavailableError("HYCOM server 503", provider="hycom")
        self.mock_cache.get_static_fallback.return_value = str(self.output_dir / "static_currents.nc")

        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        res = self.chain.fetch_currents(req)

        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "StaticCache")
        self.mock_cache.get_static_fallback.assert_called_with("currents", None)

    # Test 5 — ERA5 failure -> Open-Meteo attempted & succeeds
    def test_05_era5_failure_openmeteo_success(self):
        self.mock_era5.fetch_data.side_effect = LicenceNotAcceptedError("CDS license required", provider="era5")
        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="wind")
        res = self.chain.fetch_wind(req)

        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "OpenMeteo")
        self.assertEqual(self.mock_era5.fetch_data.call_count, 1)
        self.assertEqual(self.mock_openmeteo.fetch_data.call_count, 1)

    # Test 6 — ERA5 + Open-Meteo failure -> cache attempted
    def test_06_era5_openmeteo_failure_cache_attempted(self):
        self.mock_era5.fetch_data.side_effect = TimeoutError("CDS timeout", provider="era5")
        self.mock_openmeteo.fetch_data.side_effect = RateLimitedError("Open-Meteo 429", provider="openmeteo")
        self.mock_cache.get_static_fallback.return_value = str(self.output_dir / "static_wind.nc")

        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="wind")
        res = self.chain.fetch_wind(req)

        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "StaticCache")

    # Test 7 — Both live providers fail and no cache -> structured degraded result
    def test_07_all_fail_structured_degraded(self):
        self.mock_cmems.fetch_data.side_effect = UnavailableError("CMEMS down")
        self.mock_hycom.fetch_data.side_effect = UnavailableError("HYCOM down")
        self.mock_era5.fetch_data.side_effect = UnavailableError("ERA5 down")
        self.mock_openmeteo.fetch_data.side_effect = UnavailableError("OpenMeteo down")
        self.mock_cache.get_static_fallback.return_value = None

        response = self.chain.fetch_metocean(self.request_both)
        self.assertEqual(response.status, "failed")
        self.assertTrue(response.metadata["degraded"])
        self.assertEqual(response.providers_used.get("currents"), "DEGRADED")
        self.assertEqual(response.providers_used.get("wind"), "DEGRADED")

    # Test 8 — Circuit breaker opens after configured failures
    def test_08_circuit_breaker_opens(self):
        cb = CircuitBreaker("TEST_CB", failure_threshold=2, cooldown_seconds=10.0)
        self.assertEqual(cb.state, CircuitBreakerState.CLOSED)

        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreakerState.CLOSED)

        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreakerState.OPEN)
        self.assertFalse(cb.can_execute())

    # Test 9 — Open circuit skips provider immediately
    def test_09_open_circuit_skips_provider(self):
        cb = self.status_tracker.circuit_breakers["CMEMS"]
        cb.state = CircuitBreakerState.OPEN
        cb.last_failure_time = time.time()  # Not cooled down yet

        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        res = self.chain.fetch_currents(req)

        # CMEMS was skipped without calling fetch_data; HYCOM was called directly
        self.assertEqual(self.mock_cmems.fetch_data.call_count, 0)
        self.assertEqual(self.mock_hycom.fetch_data.call_count, 1)
        self.assertEqual(res["provider"], "HYCOM")

    # Test 10 — Cooldown allows HALF_OPEN test
    def test_10_cooldown_allows_half_open(self):
        cb = CircuitBreaker("TEST_CB", failure_threshold=1, cooldown_seconds=0.1)
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreakerState.OPEN)
        self.assertFalse(cb.can_execute())

        # Wait for cooldown
        time.sleep(0.15)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, CircuitBreakerState.HALF_OPEN)

    # Test 11 — Successful HALF_OPEN request closes circuit
    def test_11_successful_half_open_closes_circuit(self):
        cb = CircuitBreaker("TEST_CB", failure_threshold=1, cooldown_seconds=0.1)
        cb.state = CircuitBreakerState.HALF_OPEN
        cb.record_success()
        self.assertEqual(cb.state, CircuitBreakerState.CLOSED)
        self.assertEqual(cb.failure_count, 0)

    # Test 12 — Provider status records failure
    def test_12_provider_status_records_failure(self):
        self.mock_cmems.fetch_data.side_effect = AuthFailedError("Invalid token", provider="cmems")
        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        self.chain.fetch_currents(req)

        cmems_status = self.status_tracker.get_provider_status("CMEMS")
        self.assertEqual(cmems_status["last_error_class"], "AUTH_FAILED")
        self.assertIsNotNone(cmems_status["last_failure_utc"])

    # Test 13 — Provider status records active fallback provider
    def test_13_provider_status_records_active_provider(self):
        self.mock_cmems.fetch_data.side_effect = TimeoutError("Timeout", provider="cmems")
        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        self.chain.fetch_currents(req)

        hycom_status = self.status_tracker.get_provider_status("HYCOM")
        self.assertEqual(hycom_status["status"], "HEALTHY")
        self.assertEqual(hycom_status["active_provider"], "HYCOM")

    # Test 14 — UTC timestamps are recorded
    def test_14_utc_timestamps_recorded(self):
        req = MetoceanRequest(bbox=self.test_bbox, start="2017-01-31T00:00:00Z", end="2017-02-01T00:00:00Z", what="currents")
        self.chain.fetch_currents(req)

        status = self.status_tracker.get_provider_status("CMEMS")
        success_utc = status.get("last_success_utc")
        self.assertIsNotNone(success_utc)
        self.assertTrue(success_utc.endswith("Z"))

    # Test 15 — No credentials appear in status or JSON output
    def test_15_no_credentials_in_status_json(self):
        status_file = self.output_dir / "provider_status.json"
        self.status_tracker.save_to_json(status_file)

        with open(status_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Verify no secret keywords exist in dumped JSON
        for secret_kw in ["password", "secret", "token", "apiKey", "bearer"]:
            self.assertNotIn(f'"{secret_kw}"', content.lower())


if __name__ == "__main__":
    unittest.main()
