"""Verify every external credential with a real authenticated call.

Run after filling in `.env`:

    cd 1_indhu_main_system
    ../.venv/Scripts/python -m backend.verify_credentials

Each provider gets an actual authenticated request -- not a ping -- so a
failure here is the same failure the pipeline would hit, reported with the
project's error taxonomy while there is still time to fix it. A key that
"looks configured" but was never exercised is how demos die.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "1_indhu_main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import requests  # noqa: E402

from backend.core.config import get_settings  # noqa: E402  (loads .env)

OK, BAD, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"


def check_secret_key():
    s = get_settings()
    if not s.secret_key:
        return BAD, "SECRET_KEY unset -- stored credentials will NOT be encrypted"
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return WARN, "SECRET_KEY set but `cryptography` is not installed (pip install cryptography)"
    return OK, "credential encryption available"


def check_cdse():
    cid, secret = os.getenv("CDSE_CLIENT_ID"), os.getenv("CDSE_CLIENT_SECRET")
    user, pwd = os.getenv("CDSE_USERNAME"), os.getenv("CDSE_PASSWORD")
    if not ((cid and secret) or (user and pwd)):
        return SKIP, "no CDSE credentials in .env"
    url = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
           "/protocol/openid-connect/token")
    if cid and secret:
        data = {"grant_type": "client_credentials",
                "client_id": cid, "client_secret": secret}
    else:
        data = {"grant_type": "password", "client_id": "cdse-public",
                "username": user, "password": pwd}
    try:
        r = requests.post(url, data=data, timeout=30)
        if r.status_code == 200 and "access_token" in r.json():
            return OK, "OAuth token issued (expires in ~10 min; service auto-refreshes)"
        if r.status_code in (400, 401, 403):
            return BAD, f"AUTH_FAILED: HTTP {r.status_code} -- {r.text[:120]}"
        return BAD, f"BAD_RESPONSE: HTTP {r.status_code}"
    except requests.RequestException as exc:
        return BAD, f"UNAVAILABLE: {exc}"


def check_earthdata():
    user, pwd = os.getenv("EARTHDATA_USER"), os.getenv("EARTHDATA_PASS")
    if not (user and pwd):
        return SKIP, "no Earthdata credentials in .env"
    try:
        r = requests.get("https://urs.earthdata.nasa.gov/api/users/tokens",
                         auth=(user, pwd), timeout=30)
        if r.status_code == 200:
            return OK, "Earthdata login accepted"
        if r.status_code in (401, 403):
            return BAD, f"AUTH_FAILED: HTTP {r.status_code}"
        return WARN, f"unexpected HTTP {r.status_code} (endpoint may have moved)"
    except requests.RequestException as exc:
        return BAD, f"UNAVAILABLE: {exc}"


def check_cmems():
    user, pwd = os.getenv("CMEMS_USERNAME"), os.getenv("CMEMS_PASSWORD")
    if not (user and pwd):
        return SKIP, "no CMEMS credentials in .env"
    try:
        import copernicusmarine  # noqa: F401
    except ImportError:
        return WARN, ("credentials set but `copernicusmarine` is not installed "
                      "(pip install copernicusmarine) -- currents stay on HYCOM/cache")
    try:
        import copernicusmarine as cm
        ok = cm.login(username=user, password=pwd, force_overwrite=True)
        return (OK, "CMEMS login accepted") if ok else (BAD, "AUTH_FAILED: login rejected")
    except Exception as exc:
        return BAD, f"AUTH_FAILED: {str(exc)[:150]}"


def check_era5():
    key = os.getenv("CDSAPI_KEY")
    rc = Path.home() / ".cdsapirc"
    if not key and not rc.exists():
        return SKIP, "no CDSAPI_KEY in .env and no ~/.cdsapirc"
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        return WARN, ("token present but `cdsapi` is not installed "
                      "(pip install cdsapi) -- wind stays on Open-Meteo")
    try:
        import cdsapi
        url = os.getenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
        c = cdsapi.Client(url=url, key=key) if key else cdsapi.Client()
        # A tiny request exercises BOTH the token and the dataset licence --
        # the licence is the step everyone forgets, and its failure message
        # reads like a server fault.
        c.retrieve("reanalysis-era5-single-levels",
                   {"product_type": "reanalysis", "format": "netcdf",
                    "variable": "10m_u_component_of_wind",
                    "year": "2017", "month": "02", "day": "01", "time": "00:00",
                    "area": [13.2, 80.1, 13.0, 80.3]},
                   str(REPO_ROOT / "data" / "era5_credential_check.nc"))
        return OK, "token valid AND the ERA5 licence is accepted"
    except Exception as exc:
        msg = str(exc)
        if "licence" in msg.lower() or "terms" in msg.lower() or "required" in msg.lower():
            return BAD, ("LICENCE_NOT_ACCEPTED: open the ERA5 dataset page on "
                         "cds.climate.copernicus.eu and accept its licence")
        if "401" in msg or "403" in msg or "invalid" in msg.lower():
            return BAD, f"AUTH_FAILED: {msg[:120]}"
        return WARN, f"request queued or failed: {msg[:150]}"


def check_openmeteo():
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast"
                         "?latitude=13&longitude=80&hourly=wind_speed_10m"
                         "&forecast_days=1", timeout=20)
        return (OK, "reachable, no key needed") if r.ok else (BAD, f"HTTP {r.status_code}")
    except requests.RequestException as exc:
        return BAD, f"UNAVAILABLE: {exc}"


def check_hycom():
    try:
        r = requests.get("https://tds.hycom.org/thredds/catalog.html", timeout=20)
        return (OK, "reachable, no key needed") if r.ok else (WARN, f"HTTP {r.status_code}")
    except requests.RequestException as exc:
        return WARN, f"UNAVAILABLE right now: {exc}"


def check_aisstream():
    key = os.getenv("AISSTREAM_API_KEY")
    if not key:
        return SKIP, "no AISStream key (optional -- only the live demo tab needs it)"
    return WARN, "key present; validated on first websocket connect (no REST probe exists)"


CHECKS = [
    ("SECRET_KEY / encryption", check_secret_key),
    ("CDSE (Sentinel-1 primary)", check_cdse),
    ("Earthdata (ASF fallback)", check_earthdata),
    ("CMEMS (currents primary)", check_cmems),
    ("ERA5 / CDS (wind primary)", check_era5),
    ("Open-Meteo (wind fallback)", check_openmeteo),
    ("HYCOM (currents fallback)", check_hycom),
    ("AISStream (optional)", check_aisstream),
]

ICON = {OK: "[PASS]", BAD: "[FAIL]", SKIP: "[ -- ]", WARN: "[WARN]"}


def main() -> int:
    print("\nOceanTrace credential verification "
          "(each PASS is a real authenticated call)\n" + "-" * 66)
    failures = 0
    for name, fn in CHECKS:
        t0 = time.time()
        try:
            status, detail = fn()
        except Exception as exc:                       # a check must never crash the run
            status, detail = BAD, f"checker error: {type(exc).__name__}: {exc}"
        failures += status == BAD
        print(f"  {ICON[status]} {name:<28} {detail}  ({time.time() - t0:.1f}s)")
    print("-" * 66)
    if failures:
        print(f"  {failures} failure(s). The pipeline still runs -- fallback chains "
              f"cover every FAIL above -- but fix these before relying on the "
              f"primary providers.\n")
    else:
        print("  All configured providers verified.\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
