"""Provider health probing and the fallback chain's circuit breaker.

Health is measured, not declared. Each probe makes a real request to the
provider's own endpoint and records latency and outcome; the monitoring page
renders that history. Providers that need credentials are reported as
UNCONFIGURED rather than FAILED when none are present -- those are different
problems with different fixes, and conflating them sends an operator hunting
for an outage that is really a missing key.

The circuit breaker exists so a dead primary does not cost every request a
timeout: after N consecutive failures the chain skips that member for a
cooldown, which is what makes the fallback fast rather than merely possible.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from backend.core.config import PROVIDER_BY_NAME, PROVIDERS, get_settings
from backend.core.security import resolve_credential
from backend.models.db import ApiProvider, record_call, utcnow

FAILURES_BEFORE_OPEN = 3
COOLDOWN_SECONDS = 120
PROBE_TIMEOUT = 12

# A cheap, unauthenticated endpoint per provider that proves the service is
# reachable. Deliberately not the download endpoint -- probing that would cost
# bandwidth on every health cycle.
PROBES: Dict[str, str] = {
    "CDSE": "https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$top=1",
    "ASF": "https://api.daac.asf.alaska.edu/services/search/param?platform=Sentinel-1&maxResults=1&output=json",
    "CMEMS": "https://data.marine.copernicus.eu/",
    "HYCOM": "https://tds.hycom.org/thredds/catalog.html",
    # /api/v2 is the RETIRED endpoint and 404s; the new CDS answers on /api.
    "ERA5": "https://cds.climate.copernicus.eu/api",
    "OpenMeteo": "https://api.open-meteo.com/v1/forecast?latitude=13&longitude=80&hourly=wind_speed_10m&forecast_days=1",
    # web.ais.dk (the AIS archive host) refuses TLS and times out on :80 from
    # here, so probing it reports an outage for a service that is only
    # unreachable on this path. dma.dk is the authority behind the archive
    # and is the honest reachability signal; the archive itself is bulk
    # download, exercised by ml/ais ingestion rather than by a health ping.
    "DMA": "https://dma.dk/",
    "MarineCadastre": "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/index.html",
}

# Providers that are local by nature -- there is nothing to reach over a network.
LOCAL_PROVIDERS = {"LocalCache", "SyntheticGenerator", "StaticCache"}

# Fields a provider needs. A provider may accept more than one valid
# combination -- CDSE takes either an account username/password (what the
# scene service actually uses, via the public `cdse-public` client) or an
# OAuth client pair. Modelling this as alternatives matters: checking only the
# OAuth pair reported CDSE as UNCONFIGURED on the monitoring page while the
# username/password path was working perfectly, which is worse than no status
# at all.
CREDENTIAL_ALTERNATIVES: Dict[str, List[List[str]]] = {
    "CDSE": [["CDSE_USERNAME", "CDSE_PASSWORD"],
             ["CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET"]],
    "ASF": [["EARTHDATA_USER", "EARTHDATA_PASS"],
            ["ASF_USERNAME", "ASF_PASSWORD"]],
    "CMEMS": [["CMEMS_USERNAME", "CMEMS_PASSWORD"]],
    "ERA5": [["CDSAPI_KEY"]],
    "AISStream": [["AISSTREAM_API_KEY"]],
}

# Flattened view, for the Key Management page's field listing.
CREDENTIAL_FIELDS: Dict[str, List[str]] = {
    provider: [f for combo in combos for f in combo]
    for provider, combos in CREDENTIAL_ALTERNATIVES.items()
}


def has_credentials(db, provider: str) -> bool:
    """True if ANY accepted credential combination is fully present."""
    combos = CREDENTIAL_ALTERNATIVES.get(provider, [])
    if not combos:
        return True
    return any(all(resolve_credential(db, provider, f) for f in combo)
               for combo in combos)


def missing_credentials(db, provider: str) -> List[str]:
    """The fields of the *closest* incomplete combination, for the UI message."""
    combos = CREDENTIAL_ALTERNATIVES.get(provider, [])
    best: List[str] = []
    for combo in combos:
        missing = [f for f in combo if not resolve_credential(db, provider, f)]
        if not missing:
            return []
        if not best or len(missing) < len(best):
            best = missing
    return best


def circuit_is_open(row: ApiProvider) -> bool:
    if row.circuit_open_until is None:
        return False
    until = row.circuit_open_until
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def trip_circuit(db, row: ApiProvider) -> None:
    row.circuit_open_until = utcnow() + timedelta(seconds=COOLDOWN_SECONDS)
    db.commit()


def probe(db, provider: str) -> dict:
    """Probe one provider and persist the outcome."""
    spec = PROVIDER_BY_NAME.get(provider, {})
    row = db.get(ApiProvider, provider)
    if row is None:
        return {"provider": provider, "status": "UNKNOWN",
                "detail": "provider is not registered"}

    row.has_credentials = has_credentials(db, provider)

    if provider in LOCAL_PROVIDERS:
        # A local path either exists or it does not; there is no service to ping.
        settings = get_settings()
        available = (settings.data_root / "scenes").exists() or provider != "LocalCache"
        row.status = "WORKING" if available else "DEGRADED"
        row.last_success_utc = utcnow() if available else row.last_success_utc
        row.last_error_class = "NONE" if available else "UNAVAILABLE"
        db.commit()
        return {"provider": provider, "status": row.status, "detail": "local, no network"}

    if spec.get("needs_credentials") and not row.has_credentials:
        # Not an outage. Say which one it is so nobody debugs the wrong thing.
        row.status = "UNCONFIGURED"
        row.last_error_class = "AUTH_FAILED"
        db.commit()
        return {"provider": provider, "status": "UNCONFIGURED",
                "detail": "missing credential(s): "
                          + ", ".join(missing_credentials(db, provider))}

    if circuit_is_open(row):
        return {"provider": provider, "status": row.status,
                "detail": "circuit open; skipping probe during cooldown"}

    url = PROBES.get(provider)
    if not url:
        return {"provider": provider, "status": row.status or "UNKNOWN",
                "detail": "no probe endpoint defined"}

    t0 = time.time()
    try:
        resp = requests.get(url, timeout=PROBE_TIMEOUT,
                            headers={"User-Agent": "OceanTrace/health"})
        latency = int((time.time() - t0) * 1000)
        # 401/403 means the service is up and refusing us -- an auth problem,
        # not an outage, and the operator fixes it on the Keys page.
        if resp.status_code in (401, 403):
            record_call(db, provider, url, "failed", latency, resp.status_code,
                        "AUTH_FAILED", "provider rejected the credentials")
            row.status = "DEGRADED"
            db.commit()
            return {"provider": provider, "status": "DEGRADED",
                    "detail": f"reachable but unauthorised (HTTP {resp.status_code})",
                    "latency_ms": latency}
        if resp.status_code in (202, 204):
            # CDS answers /api with 202 Accepted -- reachable and healthy.
            record_call(db, provider, url, "ok", latency, resp.status_code)
            row.circuit_open_until = None
            db.commit()
            return {"provider": provider, "status": "WORKING",
                    "detail": f"HTTP {resp.status_code}", "latency_ms": latency}
        if resp.status_code >= 500:
            record_call(db, provider, url, "failed", latency, resp.status_code,
                        "UNAVAILABLE", "server error")
        elif resp.status_code >= 400:
            record_call(db, provider, url, "failed", latency, resp.status_code,
                        "BAD_RESPONSE", "client error")
        else:
            record_call(db, provider, url, "ok", latency, resp.status_code)
            row.circuit_open_until = None
            db.commit()
            return {"provider": provider, "status": "WORKING",
                    "detail": f"HTTP {resp.status_code}", "latency_ms": latency}
    except requests.Timeout:
        latency = int((time.time() - t0) * 1000)
        record_call(db, provider, url, "failed", latency, None, "TIMEOUT",
                    f"no response within {PROBE_TIMEOUT}s")
    except requests.RequestException as exc:
        latency = int((time.time() - t0) * 1000)
        record_call(db, provider, url, "failed", latency, None, "UNAVAILABLE",
                    str(exc)[:300])

    db.refresh(row)
    if (row.consecutive_failures or 0) >= FAILURES_BEFORE_OPEN:
        trip_circuit(db, row)
    return {"provider": provider, "status": row.status,
            "detail": row.last_error_class, "latency_ms": row.last_latency_ms}


def probe_all(db, kinds: Optional[List[str]] = None) -> List[dict]:
    out = []
    for spec in PROVIDERS:
        if kinds and spec["kind"] not in kinds:
            continue
        out.append(probe(db, spec["name"]))
    return out


def active_member(db, chain: List[str]) -> str:
    """First chain member that is currently usable -- what is really serving.

    This is what the monitoring page highlights, and it is the honest answer to
    "where is this layer's data coming from right now".
    """
    for name in chain:
        row = db.get(ApiProvider, name)
        if row is None:
            continue
        if row.status in ("WORKING",) and not circuit_is_open(row):
            return name
    return chain[-1] if chain else "unknown"
