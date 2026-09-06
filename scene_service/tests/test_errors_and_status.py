"""Tests for the typed provider error taxonomy and provider_status.json writer."""

import json
import os
import sys
import unittest
from pathlib import Path

module_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if module_root not in sys.path:
    sys.path.insert(0, module_root)

from satellite.errors import (  # noqa: E402
    AuthFailedError,
    BadResponseError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitedError,
    UnavailableError,
    classify_http_status,
    error_class_of,
)
from satellite.models import ProviderHealth  # noqa: E402
from satellite.status import (  # noqa: E402
    build_provider_status_payload,
    write_provider_status_file,
)


class TestTypedErrors(unittest.TestCase):
    def test_01_subclasses_remain_runtime_errors(self):
        """Existing `except RuntimeError` call sites must keep working."""
        for cls in (AuthFailedError, ProviderTimeoutError, RateLimitedError,
                    UnavailableError, BadResponseError):
            self.assertTrue(issubclass(cls, RuntimeError))
            self.assertTrue(issubclass(cls, ProviderError))

    def test_02_error_class_values_match_frozen_taxonomy(self):
        self.assertEqual(AuthFailedError("x").error_class, "AUTH_FAILED")
        self.assertEqual(ProviderTimeoutError("x").error_class, "TIMEOUT")
        self.assertEqual(RateLimitedError("x").error_class, "RATE_LIMITED")
        self.assertEqual(UnavailableError("x").error_class, "UNAVAILABLE")
        self.assertEqual(BadResponseError("x").error_class, "BAD_RESPONSE")

    def test_03_classify_http_status(self):
        self.assertIsInstance(classify_http_status(401, "x"), AuthFailedError)
        self.assertIsInstance(classify_http_status(403, "x"), AuthFailedError)
        self.assertIsInstance(classify_http_status(429, "x"), RateLimitedError)
        self.assertIsInstance(classify_http_status(408, "x"), ProviderTimeoutError)
        self.assertIsInstance(classify_http_status(500, "x"), UnavailableError)
        self.assertIsInstance(classify_http_status(503, "x"), UnavailableError)
        self.assertIsInstance(classify_http_status(404, "x"), BadResponseError)

    def test_04_error_class_of_untyped_exceptions(self):
        self.assertEqual(error_class_of(TimeoutError("t")), "TIMEOUT")
        self.assertEqual(error_class_of(ValueError("v")), "UNAVAILABLE")
        self.assertEqual(error_class_of(RateLimitedError("r")), "RATE_LIMITED")

    def test_05_provider_attribution(self):
        err = BadResponseError("broken json", "CDSE")
        self.assertEqual(err.provider, "CDSE")
        self.assertEqual(str(err), "broken json")


def _mock_status_map(cdse_up=True, asf_up=True):
    return {
        "cdse": ProviderHealth(
            provider_name="CDSE",
            is_available=cdse_up,
            status="UP" if cdse_up else "DOWN",
            latency_ms=42.5,
            details={"status_code": 200} if cdse_up else {"error": "Connection/Timeout error: timed out"},
        ),
        "asf": ProviderHealth(
            provider_name="ASF",
            is_available=asf_up,
            status="UP" if asf_up else "DOWN",
            latency_ms=55.0,
            details={"status_code": 200} if asf_up else {"error": "HTTP 503"},
        ),
    }


class TestProviderStatusFile(unittest.TestCase):
    def test_06_payload_matches_frozen_contract(self):
        repo_root = os.path.abspath(os.path.join(module_root, ".."))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from contracts.schemas.tabular import ProviderStatusFile

        payload = build_provider_status_payload(_mock_status_map())
        parsed = ProviderStatusFile.model_validate(payload)  # raises on breach
        self.assertEqual(len(parsed.providers), 2)
        self.assertEqual(parsed.providers[0].active_provider, "CDSE")
        for p in parsed.providers:
            self.assertIn("CDSE", p.chain)
            self.assertIn("LocalCache", p.chain)

    def test_07_active_provider_falls_back(self):
        payload = build_provider_status_payload(_mock_status_map(cdse_up=False))
        for p in payload["providers"]:
            self.assertEqual(p["active_provider"], "ASF")
        cdse = payload["providers"][0]
        self.assertEqual(cdse["status"], "FAILED")
        self.assertEqual(cdse["last_error_class"], "TIMEOUT")
        self.assertIsNone(cdse["last_success_utc"])
        self.assertIsNotNone(cdse["last_failure_utc"])

    def test_08_all_down_serves_local_cache(self):
        payload = build_provider_status_payload(_mock_status_map(False, False))
        for p in payload["providers"]:
            self.assertEqual(p["active_provider"], "LocalCache")
        asf = payload["providers"][1]
        self.assertEqual(asf["last_error_class"], "UNAVAILABLE")
        self.assertEqual(asf["last_code"], 503)

    def test_09_write_provider_status_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "provider_status.json"
            out = write_provider_status_file(path, _mock_status_map())
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("generated_utc", data)
            self.assertTrue(data["generated_utc"].endswith("Z"))
            self.assertEqual({p["provider"] for p in data["providers"]}, {"CDSE", "ASF"})


if __name__ == "__main__":
    unittest.main()
