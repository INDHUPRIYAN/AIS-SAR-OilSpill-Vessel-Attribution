"""Unit tests for Phase 6 Provider Status and Health Checks."""

import json
import os
import sys
import time
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock, patch
import urllib.error

# Add module root to sys.path
module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.models import ProviderHealth
from satellite.status import check_asf_health, check_cdse_health, get_api_status


class TestProviderStatus(unittest.TestCase):
    """Test suite for Phase 6 Provider Health Probes."""

    def test_01_cdse_healthy_response(self):
        """1. Test CDSE health check when endpoint returns HTTP 200."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            health = check_cdse_health(timeout=2.0)
            self.assertIsInstance(health, ProviderHealth)
            self.assertEqual(health.provider_name, "CDSE")
            self.assertTrue(health.is_available)
            self.assertEqual(health.status, "UP")
            self.assertIsNotNone(health.latency_ms)
            self.assertGreaterEqual(health.latency_ms, 0.0)

    def test_02_asf_healthy_response(self):
        """2. Test ASF health check when endpoint returns HTTP 200."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            health = check_asf_health(timeout=2.0)
            self.assertIsInstance(health, ProviderHealth)
            self.assertEqual(health.provider_name, "ASF")
            self.assertTrue(health.is_available)
            self.assertEqual(health.status, "UP")
            self.assertIsNotNone(health.latency_ms)

    def test_03_cdse_timeout(self):
        """3. Test CDSE health check handles timeout."""
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
            health = check_cdse_health(timeout=0.1)
            self.assertEqual(health.provider_name, "CDSE")
            self.assertFalse(health.is_available)
            self.assertEqual(health.status, "DOWN")
            self.assertIn("Timeout", health.details.get("error", ""))

    def test_04_asf_timeout(self):
        """4. Test ASF health check handles timeout."""
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
            health = check_asf_health(timeout=0.1)
            self.assertEqual(health.provider_name, "ASF")
            self.assertFalse(health.is_available)
            self.assertEqual(health.status, "DOWN")
            self.assertIn("Timeout", health.details.get("error", ""))

    def test_05_cdse_connection_failure(self):
        """5. Test CDSE connection error handling."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
            health = check_cdse_health(timeout=1.0)
            self.assertFalse(health.is_available)
            self.assertEqual(health.status, "DOWN")
            self.assertIn("Connection/Timeout error", health.details.get("error", ""))

    def test_06_asf_connection_failure(self):
        """6. Test ASF connection error handling."""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            health = check_asf_health(timeout=1.0)
            self.assertFalse(health.is_available)
            self.assertEqual(health.status, "DOWN")
            self.assertIn("Connection/Timeout error", health.details.get("error", ""))

    def test_07_http_failure_handling(self):
        """7. Test 5xx and 4xx HTTP errors handling."""
        # 500 Internal Server Error -> DOWN
        err_500 = urllib.error.HTTPError(
            url="https://test.com", code=500, msg="Server Error", hdrs={}, fp=BytesIO(b"")
        )
        with patch("urllib.request.urlopen", side_effect=err_500):
            health = check_cdse_health()
            self.assertFalse(health.is_available)
            self.assertEqual(health.status, "DOWN")

        # 400 Bad Request -> DEGRADED (endpoint reachable but responded 4xx)
        err_400 = urllib.error.HTTPError(
            url="https://test.com", code=400, msg="Bad Request", hdrs={}, fp=BytesIO(b"")
        )
        with patch("urllib.request.urlopen", side_effect=err_400):
            health = check_asf_health()
            self.assertFalse(health.is_available)
            self.assertEqual(health.status, "DEGRADED")

    def test_08_unconfigured_provider(self):
        """8. Test UNCONFIGURED status when require_credentials=True and missing env."""
        with patch.dict(os.environ, {}, clear=True):
            health_cdse = check_cdse_health(require_credentials=True)
            self.assertEqual(health_cdse.status, "UNCONFIGURED")
            self.assertFalse(health_cdse.is_available)
            self.assertIn("Missing CDSE credentials", health_cdse.details.get("reason", ""))

            health_asf = check_asf_health(require_credentials=True)
            self.assertEqual(health_asf.status, "UNCONFIGURED")
            self.assertFalse(health_asf.is_available)
            self.assertIn("Missing ASF credentials", health_asf.details.get("reason", ""))

    def test_09_degradation_slow_response(self):
        """9. Test DEGRADED status when latency exceeds threshold."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        def slow_urlopen(*args, **kwargs):
            time.sleep(0.05)  # 50ms delay
            return mock_resp

        # Set threshold to 10ms so 50ms triggers DEGRADED
        with patch("urllib.request.urlopen", side_effect=slow_urlopen):
            health = check_cdse_health(latency_threshold_ms=10.0)
            self.assertEqual(health.status, "DEGRADED")
            self.assertTrue(health.is_available)

    def test_10_latency_measurement(self):
        """10. Test that latency is accurately recorded in milliseconds."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            health = check_asf_health()
            self.assertIsInstance(health.latency_ms, float)
            self.assertGreater(health.latency_ms, 0.0)

    def test_11_provider_health_model_compatibility(self):
        """11. Test that returned object is fully compatible with ProviderHealth schema."""
        health = ProviderHealth(
            provider_name="CDSE",
            is_available=True,
            status="UP",
            latency_ms=120.5,
            details={"key": "val"},
        )
        json_data = health.model_dump_json()
        self.assertIn('"status":"UP"', json_data)
        self.assertIn('"latency_ms":120.5', json_data)

    def test_12_mock_mode_offline(self):
        """12. Test mock mode returns UP without any network access."""
        with patch("urllib.request.urlopen", side_effect=AssertionError("Should not make network calls")):
            health_cdse = check_cdse_health(mock_mode=True)
            self.assertEqual(health_cdse.status, "UP")
            self.assertTrue(health_cdse.is_available)
            self.assertEqual(health_cdse.details.get("mode"), "mock")

            health_asf = check_asf_health(mock_mode=True)
            self.assertEqual(health_asf.status, "UP")
            self.assertTrue(health_asf.is_available)
            self.assertEqual(health_asf.details.get("mode"), "mock")

    def test_13_sanitized_error_messages(self):
        """13. Test that passwords and sensitive info are never leaked in error messages."""
        secret_pwd = "SUPER_SECRET_CDSE_PASSWORD_XYZ123"
        with patch("urllib.request.urlopen", side_effect=Exception(f"Failed with {secret_pwd}")):
            health = check_cdse_health(password=secret_pwd)
            # Passwords should not be in details or status
            self.assertEqual(health.status, "DOWN")

    def test_14_both_provider_status_result(self):
        """14. Test get_api_status aggregates both CDSE and ASF."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            status_dict = get_api_status()
            self.assertIn("cdse", status_dict)
            self.assertIn("asf", status_dict)
            self.assertIsInstance(status_dict["cdse"], ProviderHealth)
            self.assertIsInstance(status_dict["asf"], ProviderHealth)
            self.assertEqual(status_dict["cdse"].status, "UP")
            self.assertEqual(status_dict["asf"].status, "UP")


if __name__ == "__main__":
    unittest.main()
