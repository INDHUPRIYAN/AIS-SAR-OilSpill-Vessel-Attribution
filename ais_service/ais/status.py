"""Provider health for the AIS service, written as provider_status.json.

Probes the real bulk-archive endpoints (MarineCadastre / NOAA and the Danish
Maritime Authority) with short timeouts and emits a file matching the frozen
``ProviderStatusFile`` contract in contracts/schemas/tabular.py:

    { generated_utc, owner, providers: [ {provider, purpose, status,
      last_code, last_latency_ms, last_success_utc, last_failure_utc,
      last_error_class, chain, active_provider}, ... ] }

Offline or blocked networks degrade gracefully: the probe reports FAILED with
an error class from the shared taxonomy and hands active_provider to the
SyntheticGenerator, which needs no network at all. It never crashes.
"""

import datetime
import json
import time

CHAIN = ["MarineCadastre", "DMA", "SyntheticGenerator"]

PROBES = [
    {
        "provider": "MarineCadastre",
        "purpose": "historical real AIS, bulk CSV (US waters, NOAA)",
        "url": "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/",
    },
    {
        "provider": "DMA",
        "purpose": "historical real AIS, bulk CSV (Danish waters)",
        "url": "http://web.ais.dk/aisdata/",
    },
]

DEFAULT_TIMEOUT_S = 4.0


def _utc_now_z():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe_url(url, timeout_s=DEFAULT_TIMEOUT_S):
    """HEAD (falling back to GET) one endpoint.

    Returns (http_code or None, latency_ms or None, error_class) where
    error_class is one of the shared taxonomy values, "NONE" on success.
    """
    try:
        import requests
        from requests import exceptions as rex
    except Exception:                                   # pragma: no cover
        return _probe_url_urllib(url, timeout_s)

    t0 = time.monotonic()
    try:
        resp = requests.head(url, timeout=timeout_s, allow_redirects=True)
        if resp.status_code >= 400:                     # some hosts refuse HEAD
            resp = requests.get(url, timeout=timeout_s, stream=True,
                                allow_redirects=True)
            resp.close()
        latency_ms = int((time.monotonic() - t0) * 1000)
        code = int(resp.status_code)
        if code < 400:
            return code, latency_ms, "NONE"
        if code in (401, 403):
            return code, latency_ms, "AUTH_FAILED"
        if code == 429:
            return code, latency_ms, "RATE_LIMITED"
        if code >= 500:
            return code, latency_ms, "UNAVAILABLE"
        return code, latency_ms, "BAD_RESPONSE"
    except rex.Timeout:
        return None, int((time.monotonic() - t0) * 1000), "TIMEOUT"
    except rex.RequestException:
        return None, int((time.monotonic() - t0) * 1000), "UNAVAILABLE"
    except Exception:
        return None, None, "UNAVAILABLE"


def _probe_url_urllib(url, timeout_s=DEFAULT_TIMEOUT_S):  # pragma: no cover
    """Dependency-free fallback when `requests` is not installed."""
    import urllib.error
    import urllib.request

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status), int((time.monotonic() - t0) * 1000), "NONE"
    except urllib.error.HTTPError as e:
        latency = int((time.monotonic() - t0) * 1000)
        if e.code in (401, 403):
            return int(e.code), latency, "AUTH_FAILED"
        if e.code == 429:
            return int(e.code), latency, "RATE_LIMITED"
        return int(e.code), latency, "UNAVAILABLE" if e.code >= 500 else "BAD_RESPONSE"
    except Exception:
        return None, int((time.monotonic() - t0) * 1000), "TIMEOUT" \
            if (time.monotonic() - t0) >= timeout_s * 0.9 else "UNAVAILABLE"


def get_provider_status(write_path=None, timeout_s=DEFAULT_TIMEOUT_S,
                        probe=_probe_url):
    """Probe every AIS provider and return a ProviderStatusFile-shaped dict.

    `probe` is injectable for tests. If `write_path` is given the dict is also
    written there as provider_status.json. This function never raises for
    network reasons: an unreachable provider is reported as FAILED and the
    chain hands over to the SyntheticGenerator.
    """
    now = _utc_now_z()
    providers = []
    reachable = {}

    for spec in PROBES:
        try:
            code, latency_ms, err = probe(spec["url"], timeout_s)
        except Exception:                                # never crash on a probe
            code, latency_ms, err = None, None, "UNAVAILABLE"
        ok = err == "NONE"
        reachable[spec["provider"]] = ok
        providers.append({
            "provider": spec["provider"],
            "purpose": spec["purpose"],
            "status": "WORKING" if ok else "FAILED",
            "last_code": code,
            "last_latency_ms": latency_ms,
            "last_success_utc": now if ok else None,
            "last_failure_utc": None if ok else now,
            "last_error_class": err,
            "chain": list(CHAIN),
            "active_provider": None,   # filled below once all probes are known
        })

    # The synthetic generator is always available: it is the designed
    # ground-truth path (no public bulk AIS exists for Indian waters).
    reachable["SyntheticGenerator"] = True
    providers.append({
        "provider": "SyntheticGenerator",
        "purpose": "synthetic AIS with planted culprit (ground-truth path)",
        "status": "WORKING",
        "last_code": None,
        "last_latency_ms": 0,
        "last_success_utc": now,
        "last_failure_utc": None,
        "last_error_class": "NONE",
        "chain": list(CHAIN),
        "active_provider": None,
    })

    active = next((p for p in CHAIN if reachable.get(p)), "SyntheticGenerator")
    for p in providers:
        p["active_provider"] = active
        # a live provider that is not serving (because a higher chain member
        # is, or because it failed) is still WORKING by its own probe; only
        # mark DEGRADED when the whole chain has fallen through to synthetic
        if p["provider"] == active and active != CHAIN[0] \
                and p["status"] == "WORKING" and p["provider"] != "SyntheticGenerator":
            p["status"] = "DEGRADED"

    status_file = {
        "generated_utc": now,
        "owner": "krishnan (ais_service)",
        "providers": providers,
    }

    if write_path is not None:
        try:
            with open(write_path, "w", encoding="utf-8") as f:
                json.dump(status_file, f, indent=2)
        except OSError:
            pass                                        # status must never crash
    return status_file


def get_ais_sources_status():
    return get_provider_status()
