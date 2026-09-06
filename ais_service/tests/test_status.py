"""status.py: real probing logic with graceful offline degradation.

No network is touched: the probe function is injected. The emitted file must
validate against the frozen ProviderStatusFile contract either way.
"""

import json

from ais.status import CHAIN, get_provider_status
from contracts.schemas.tabular import ProviderStatusFile


def _probe_ok(url, timeout_s):
    return 200, 42, "NONE"


def _probe_down(url, timeout_s):
    return None, None, "UNAVAILABLE"


def _probe_crash(url, timeout_s):
    raise OSError("network stack on fire")


def test_all_working_validates_and_serves_primary(tmp_path):
    out = tmp_path / "provider_status.json"
    status = get_provider_status(write_path=out, probe=_probe_ok)
    ProviderStatusFile.model_validate(status)
    assert {p["provider"] for p in status["providers"]} == set(CHAIN)
    for p in status["providers"]:
        assert p["active_provider"] == "MarineCadastre"
        assert p["chain"][0] == "MarineCadastre"
    assert out.is_file()
    ProviderStatusFile.model_validate(json.loads(out.read_text()))


def test_offline_degrades_to_synthetic_generator(tmp_path):
    out = tmp_path / "provider_status.json"
    status = get_provider_status(write_path=out, probe=_probe_down)
    ProviderStatusFile.model_validate(status)
    by_name = {p["provider"]: p for p in status["providers"]}
    assert by_name["MarineCadastre"]["status"] == "FAILED"
    assert by_name["MarineCadastre"]["last_error_class"] == "UNAVAILABLE"
    assert by_name["MarineCadastre"]["last_failure_utc"] is not None
    assert by_name["DMA"]["status"] == "FAILED"
    assert by_name["SyntheticGenerator"]["status"] == "WORKING"
    for p in status["providers"]:
        assert p["active_provider"] == "SyntheticGenerator"
    assert out.is_file()


def test_probe_exception_never_crashes():
    status = get_provider_status(probe=_probe_crash)
    ProviderStatusFile.model_validate(status)
    by_name = {p["provider"]: p for p in status["providers"]}
    assert by_name["MarineCadastre"]["status"] == "FAILED"
    assert by_name["SyntheticGenerator"]["status"] == "WORKING"


def test_owner_and_generated_utc_present():
    status = get_provider_status(probe=_probe_ok)
    assert "krishnan" in status["owner"]
    assert status["generated_utc"].endswith("Z")


def test_unwritable_path_does_not_crash(tmp_path):
    bad = tmp_path / "no_such_dir" / "provider_status.json"
    status = get_provider_status(write_path=bad, probe=_probe_ok)
    ProviderStatusFile.model_validate(status)
