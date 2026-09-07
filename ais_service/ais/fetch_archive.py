"""Download MarineCadastre daily AIS archives (NOAA AISDataHandler).

`fetch-ais --source marinecadastre` parses a CSV that is already on disk; it
has never had a way to obtain one. This closes that gap so a real-AIS run can
be reproduced from a date rather than from a file somebody happened to keep.

Two details make this more than a `requests.get`:

* **The preceding day is not optional.** Sentinel-1 descending passes over the
  Gulf are acquired around 00:01-00:11 UTC, so a backward drift window from an
  acquisition lands almost entirely in the *previous* UTC day. Fetching only
  the acquisition date silently loses the hours that matter, and the run then
  reports "no vessel covers the origin" for a reason that is nothing to do
  with the data.
* **Resume.** These are ~330 MB each; a transfer that restarts from zero on
  every disconnection never finishes on a domestic link.

    python -m ais.fetch_archive --date 2023-01-08 --with-preceding-day
"""
from __future__ import annotations

import argparse
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import requests

BASE = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"
CHUNK = 1 << 20


def archive_url(day: date) -> str:
    return f"{BASE}/{day.year}/AIS_{day:%Y_%m_%d}.zip"


def download(day: date, out_dir: Path, timeout: int = 60,
             attempts: int = 6) -> Path:
    """Fetch one daily archive, resuming a partial file if one is present.

    A 330 MB transfer breaks often enough that a single attempt is not a
    strategy: the observed failure is `IncompleteRead` partway through, which
    leaves a perfectly good partial file. Each retry resumes from the byte
    already on disk, so a broken link costs seconds rather than the whole
    download.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"AIS_{day:%Y_%m_%d}.zip"

    if dest.exists():
        print(f"  {dest.name} already present ({dest.stat().st_size:,} B)")
        return dest

    last: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(day, dest, timeout)
        except (requests.RequestException, OSError) as exc:
            last = exc
            got = dest.with_suffix(".zip.part")
            have = got.stat().st_size if got.exists() else 0
            print(f"\n  attempt {attempt}/{attempts} failed after {have:,} B "
                  f"({type(exc).__name__}); resuming")
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"could not fetch {archive_url(day)} in {attempts} "
                       f"attempts; last error: {last}")


def _download_once(day: date, dest: Path, timeout: int) -> Path:
    """One transfer attempt, continuing from whatever is already on disk."""
    url = archive_url(day)
    part = dest.with_suffix(".zip.part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        if have and r.status_code != 206:
            # The server ignored the range and is sending the whole file again.
            # Appending would splice a second copy of the head onto the bytes we
            # already had -- a corrupt zip that only fails at ingest time.
            part.unlink(missing_ok=True)
            have = 0

        total = int(r.headers.get("Content-Length", 0)) + have
        mode = "ab" if have else "wb"
        done = have
        with open(part, mode) as fh:
            for chunk in r.iter_content(CHUNK):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {day} {done / 1e6:8.1f} / {total / 1e6:.1f} MB"
                          f"  ({100 * done / total:5.1f}%)", end="", flush=True)
    print()
    if total and part.stat().st_size != total:
        # A short file is a valid zip prefix, not a zip. Left as .part it
        # resumes; promoted to .zip it fails much later, at ingest.
        raise OSError(f"{part.name} is {part.stat().st_size:,} B, expected "
                      f"{total:,} B")
    part.replace(dest)
    return dest


def extract(zip_path: Path, out_dir: Path) -> List[Path]:
    """Unpack the CSV(s) inside a daily archive."""
    written = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.lower().endswith(".csv"):
                continue
            target = out_dir / Path(name).name
            if not target.exists():
                with z.open(name) as src, open(target, "wb") as dst:
                    while block := src.read(CHUNK):
                        dst.write(block)
            written.append(target)
    return written


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True, help="acquisition date, YYYY-MM-DD")
    ap.add_argument("--with-preceding-day", action="store_true",
                    help="also fetch D-1 -- required whenever the drift window "
                         "reaches back across midnight UTC")
    ap.add_argument("--out", default="data/ais/raw", help="download directory")
    ap.add_argument("--no-extract", action="store_true")
    args = ap.parse_args(argv)

    day = date.fromisoformat(args.date)
    days = [day - timedelta(days=1), day] if args.with_preceding_day else [day]
    out_dir = Path(args.out)

    csvs: List[Path] = []
    for d in days:
        print(f"fetching {archive_url(d)}")
        z = download(d, out_dir)
        if not args.no_extract:
            got = extract(z, out_dir)
            csvs.extend(got)
            for c in got:
                print(f"  extracted {c.name} ({c.stat().st_size:,} B)")

    print(f"\n{len(days)} archive(s) in {out_dir}")
    for c in csvs:
        print(f"  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
