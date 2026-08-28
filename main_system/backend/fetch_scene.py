"""Download a Sentinel-1 product from CDSE, with the token-refresh wrapper.

The CDSE access token lives about ten minutes. A 1 GB product takes longer
than that on most links, so a naive download dies partway with a 401 that
looks like bad credentials rather than an expired session. This refreshes the
token whenever it is close to expiry and re-issues the ranged request from the
byte already on disk, so the transfer survives both expiry and disconnection.

    python -m backend.fetch_scene --product-id <uuid> --out data/scenes
    python -m backend.fetch_scene --search --bbox 80.10 12.90 80.55 13.35 \
        --start 2017-01-25 --end 2017-02-15
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import requests  # noqa: E402

from backend.core.config import get_settings  # noqa: E402  (loads .env)

TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE"
             "/protocol/openid-connect/token")
CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD = CATALOGUE + "({product_id})/$value"
CHUNK = 1 << 20


class TokenManager:
    """Issues and refreshes CDSE bearer tokens.

    Refreshes at 60 s remaining rather than on expiry, because a request that
    starts with 5 seconds of validity left still dies mid-transfer.
    """

    def __init__(self) -> None:
        get_settings()
        self.username = os.getenv("CDSE_USERNAME")
        self.password = os.getenv("CDSE_PASSWORD")
        if not (self.username and self.password):
            raise SystemExit("CDSE_USERNAME / CDSE_PASSWORD not set in .env")
        self._token: Optional[str] = None
        self._expires_at = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        resp = requests.post(TOKEN_URL, timeout=30, data={
            "client_id": "cdse-public", "grant_type": "password",
            "username": self.username, "password": self.password})
        if resp.status_code != 200:
            raise SystemExit(f"AUTH_FAILED: {resp.status_code} {resp.text[:200]}")
        payload = resp.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 600))
        return self._token

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}"}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def search(tm: TokenManager, bbox, start: str, end: str, product: str = "GRDH",
           limit: int = 20) -> list:
    W, S, E, N = bbox
    aoi = (f"POLYGON(({W} {S},{E} {S},{E} {N},{W} {N},{W} {S}))")
    query = (
        f"{CATALOGUE}?$filter=Collection/Name eq 'SENTINEL-1' and "
        f"contains(Name,'{product}') and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{aoi}') and "
        f"ContentDate/Start gt {start}T00:00:00.000Z and "
        f"ContentDate/Start lt {end}T00:00:00.000Z"
        f"&$top={limit}&$orderby=ContentDate/Start")
    resp = requests.get(query, headers=tm.headers(), timeout=90)
    resp.raise_for_status()
    return resp.json().get("value", [])


def _get_following_redirects(url: str, headers: dict, max_hops: int = 6):
    """GET that keeps the Authorization header across hosts.

    CDSE redirects `catalogue.dataspace.copernicus.eu` ->
    `download.dataspace.copernicus.eu`, and `requests` strips Authorization on
    cross-host redirects as a security measure. Correct in general, fatal here:
    the final request arrives anonymous and CDSE answers 401, which looks
    exactly like an expired token and is not fixed by refreshing one.

    So redirects are followed by hand, re-attaching the header each hop. Only
    dataspace.copernicus.eu hosts are trusted with the token -- following a
    redirect to an arbitrary host with a bearer token would leak it.
    """
    from urllib.parse import urlparse

    current = url
    for _ in range(max_hops):
        resp = requests.get(current, headers=headers, stream=True,
                            timeout=(30, 120), allow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get("Location")
        resp.close()
        if not location:
            return resp
        host = urlparse(location).hostname or ""
        if not host.endswith("dataspace.copernicus.eu"):
            raise IOError(f"refusing to send credentials to untrusted host {host}")
        current = location
    raise IOError(f"too many redirects starting at {url}")


def download(tm: TokenManager, product_id: str, name: str, out_dir: Path,
             max_attempts: int = 40) -> Path:
    """Stream a product to disk, resuming and refreshing the token as needed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{name}.zip"
    part = dest.with_suffix(".zip.part")
    if dest.exists():
        print(f"  already downloaded: {dest.name} ({human(dest.stat().st_size)})")
        return dest

    url = DOWNLOAD.format(product_id=product_id)
    attempt = 0
    while attempt < max_attempts:
        have = part.stat().st_size if part.exists() else 0
        headers = tm.headers()
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"  resuming at {human(have)}")

        try:
            with _get_following_redirects(url, headers) as r:
                if r.status_code == 401:
                    # Token expired mid-transfer: force a refresh and retry the
                    # ranged request rather than treating it as a credential
                    # failure -- this is the trap the handbook calls out.
                    print("  401 -- refreshing token and resuming")
                    tm._expires_at = 0
                    attempt += 1
                    continue
                if have and r.status_code == 200:
                    have = 0        # server ignored Range; restart cleanly
                r.raise_for_status()

                total = int(r.headers.get("Content-Length", 0)) + have
                done, t0, last = have, time.time(), time.time()
                with part.open("ab" if have else "wb") as fh:
                    for chunk in r.iter_content(CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 5:
                            rate = (done - have) / max(time.time() - t0, 1e-6)
                            pct = f"{done / total * 100:5.1f}%" if total else "  ?  "
                            print(f"  {human(done)}/{human(total)} ({pct}) "
                                  f"{rate / 1024**2:.2f} MB/s", flush=True)
                            last = time.time()
            part.rename(dest)
            print(f"  complete: {dest} ({human(dest.stat().st_size)})")
            return dest
        except (requests.RequestException, IOError) as exc:
            after = part.stat().st_size if part.exists() else 0
            if after > have:
                attempt = 0          # progress was made; the link is just lossy
            else:
                attempt += 1
            print(f"  {type(exc).__name__}: {str(exc)[:120]}")
            print(f"  retry {attempt}/{max_attempts} in 10s")
            time.sleep(10)

    raise SystemExit(f"gave up after {max_attempts} attempts with no progress")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product-id")
    ap.add_argument("--name")
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--bbox", nargs=4, type=float,
                    default=[80.10, 12.90, 80.55, 13.35])
    ap.add_argument("--start", default="2017-01-25")
    ap.add_argument("--end", default="2017-02-15")
    ap.add_argument("--product", default="GRDH")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "scenes")
    args = ap.parse_args(argv)

    tm = TokenManager()

    if args.search or not args.product_id:
        results = search(tm, args.bbox, args.start, args.end, args.product)
        if not results:
            print("no products matched")
            return 1
        results.sort(key=lambda p: p.get("ContentLength", 0))
        for p in results:
            print(f"  {p['ContentDate']['Start'][:19]}  "
                  f"{p.get('ContentLength', 0) / 1024**3:5.2f} GB  {p['Name'][:70]}")
        if args.search:
            return 0
        target = results[0]
        args.product_id, args.name = target["Id"], target["Name"]

    name = args.name or args.product_id
    print(f"\ndownloading {name}\n  id {args.product_id}")
    dest = download(tm, args.product_id, name, args.out)
    (args.out / f"{name}.meta.json").write_text(
        json.dumps({"product_id": args.product_id, "name": name,
                    "path": str(dest), "provider_used": "CDSE"}, indent=2),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
