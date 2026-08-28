"""Resumable dataset downloads (Zenodo / PANGAEA).

Trujillo ships as three Zenodo records totalling 40-60 GB. Part III is
fetched FIRST because it is small and becomes the untouched test harness --
every metric we report comes from it. Parts I-II are only ever needed by
`prepare_trujillo.py --discard`, which tiles each archive and deletes it
before moving on, so peak disk stays in the low tens of GB.

Usage:
    python -m ml.download --dataset trujillo --part 3
    python -m ml.download --dataset trujillo --part 1 --part 2
    python -m ml.download --dataset trujillo --part 3 --list   # inspect only
    python -m ml.download --dataset dartis
"""
from __future__ import annotations

import argparse
import hashlib
import time
import sys
from pathlib import Path

import requests

from ml.config import DATA_ROOT

ZENODO_API = "https://zenodo.org/api/records/{record_id}"

TRUJILLO_RECORDS = {
    1: "8346860",   # train/val part I
    2: "8253899",   # train/val part II
    3: "13761290",  # test split -- 150 oil / 150 look-alike / 150 no-oil
}

# DARTIS (Yang & Singha, DLR). PANGAEA serves a dataset landing page rather
# than a stable bulk API; if the layout changes the fallback is a manual
# download into data/raw/dartis/ -- the rest of the pipeline only cares that
# the files land there.
DARTIS_DOI = "10.1594/PANGAEA.980773"
DARTIS_LANDING = "https://doi.pangaea.de/10.1594/PANGAEA.980773"

CHUNK = 1 << 20  # 1 MiB


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def zenodo_files(record_id: str) -> list[dict]:
    """Return [{key, size, url, md5}] for a Zenodo record."""
    resp = requests.get(ZENODO_API.format(record_id=record_id), timeout=60)
    resp.raise_for_status()
    out = []
    for f in resp.json().get("files", []):
        checksum = f.get("checksum", "")  # "md5:abc123..."
        out.append({
            "key": f.get("key"),
            "size": int(f.get("size", 0)),
            "url": f.get("links", {}).get("self"),
            "md5": checksum.split(":", 1)[1] if ":" in checksum else None,
        })
    return out


def md5sum(path: Path) -> str:
    """MD5 with visible progress: hashing a 40 GB archive takes minutes,
    and a silent pause is indistinguishable from a hang from outside."""
    h = hashlib.md5()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
            done += len(block)
            if done % (512 * CHUNK) < CHUNK:
                pct = 100 * done / max(total, 1)
                print("\r  verifying " + path.name +
                      " ... {:.0f}%".format(pct), end="", flush=True)
    print("\r  verifying " + path.name + " ... 100%",
          end=" ", flush=True)
    return h.hexdigest()


def acquire_single_instance_lock() -> None:
    """Refuse to run beside another downloader.

    Two instances appending to the same .part file interleave their chunks --
    the file reaches the exact expected size and still fails its checksum,
    which is precisely how a 37.9 GB Part 1 was lost to a re-download. The
    lock makes the failure loud and immediate instead.
    """
    import atexit
    import os

    lock = DATA_ROOT / "raw" / "downloader.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            import psutil  # optional; fall back to trusting the lockfile
            alive = psutil.pid_exists(pid)
        except Exception:
            alive = True
        if alive:
            raise SystemExit(
                f"another downloader appears to be running (lock {lock}, "
                f"pid {lock.read_text().strip()}); refusing to double-write. "
                f"Delete the lockfile if that process is truly gone.")
    lock.write_text(str(os.getpid()))
    atexit.register(lambda: lock.unlink(missing_ok=True))


def download_file(url, dest: Path, expected_size=0, expected_md5=None,
                  max_attempts: int = 40, backoff: float = 5.0) -> Path:
    """Stream `url` to `dest`, resuming across connection failures.

    Zenodo drops long transfers regularly, and a 9 GB pull over a ~0.6 MB/s
    link takes hours -- so an unattended download WILL be interrupted, usually
    more than once. Each attempt resumes from the bytes already on disk via a
    Range request, so a failure costs seconds, not the whole transfer.

    Attempts only count as failures when no progress is made: as long as bytes
    keep arriving, the budget resets. That way a download that stalls for good
    gives up, while one that is merely slow and lossy runs to completion.
    """
    attempt = 0
    while True:
        have_before = 0
        part = dest.with_suffix(dest.suffix + ".part")
        if part.exists():
            have_before = part.stat().st_size
        try:
            return _download_once(url, dest, expected_size, expected_md5)
        except (requests.RequestException, IOError, ConnectionError) as exc:
            have_after = part.stat().st_size if part.exists() else 0
            progressed = have_after > have_before
            if progressed:
                attempt = 0          # made headway; the link is just lossy
            else:
                attempt += 1
            if attempt >= max_attempts:
                raise IOError(
                    f"{dest.name}: gave up after {max_attempts} attempts with no "
                    f"progress ({human(have_after)} downloaded). Re-run to resume."
                ) from exc
            wait = min(backoff * max(attempt, 1), 60.0)
            print(f"\n  {type(exc).__name__}: {str(exc)[:110]}")
            print(f"  resuming from {human(have_after)} in {wait:.0f}s "
                  f"(attempt {attempt}/{max_attempts})")
            time.sleep(wait)


def _download_once(url, dest: Path, expected_size=0, expected_md5=None) -> Path:
    """One download attempt, resuming a partial .part file if present."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    if dest.exists() and (not expected_size or dest.stat().st_size == expected_size):
        if expected_md5:
            print(f"  verifying {dest.name} ...", end=" ", flush=True)
            if md5sum(dest) == expected_md5:
                print("ok (cached)")
                return dest
            print("CHECKSUM MISMATCH -- re-downloading")
            dest.unlink()
        else:
            print(f"  {dest.name} already present, skipping")
            return dest

    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    mode = "ab" if have else "wb"
    if have:
        print(f"  resuming {dest.name} at {human(have)}")

    # (connect, read) timeouts. A stalled socket should surface in a minute so
    # the retry loop can resume, rather than hanging the whole transfer.
    with requests.get(url, headers=headers, stream=True, timeout=(30, 60)) as r:
        if have and r.status_code == 200:
            # Server ignored the Range header; restart rather than corrupt.
            have, mode = 0, "wb"
        r.raise_for_status()

        total = expected_size or (int(r.headers.get("Content-Length", 0)) + have)
        done = have
        with part.open(mode) as fh:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = done / total * 100
                    print(f"\r  {dest.name}  {human(done)}/{human(total)} ({pct:5.1f}%)",
                          end="", flush=True)
        print()

    if expected_size and part.stat().st_size != expected_size:
        raise IOError(f"{dest.name}: size mismatch, got {part.stat().st_size} "
                      f"expected {expected_size}. Re-run to resume.")
    if expected_md5:
        print(f"  verifying {dest.name} ...", end=" ", flush=True)
        got = md5sum(part)
        if got != expected_md5:
            raise IOError(f"{dest.name}: md5 {got} != expected {expected_md5}")
        print("ok")

    part.rename(dest)
    return dest


def download_trujillo(parts, list_only=False) -> None:
    for part in parts:
        record = TRUJILLO_RECORDS[part]
        dest_dir = DATA_ROOT / "raw" / "trujillo" / f"part{part}"
        print(f"\n=== Trujillo Part {part} (Zenodo record {record}) ===")
        try:
            files = zenodo_files(record)
        except requests.RequestException as exc:
            print(f"  ERROR reaching Zenodo: {exc}")
            print(f"  Manual fallback: https://zenodo.org/records/{record}")
            continue

        total = sum(f["size"] for f in files)
        print(f"  {len(files)} file(s), {human(total)} total -> {dest_dir}")
        for f in files:
            print(f"    {f['key'][:56]:<56s} {human(f['size']):>10s}")
        if list_only:
            continue
        if part in (1, 2):
            print(f"  NOTE: tile-and-discard this part before fetching the next:")
            print(f"        python -m ml.prepare_trujillo --part {part} --discard")
        for f in files:
            download_file(f["url"], dest_dir / f["key"], f["size"], f["md5"])


def download_dartis(list_only=False) -> None:
    dest_dir = DATA_ROOT / "raw" / "dartis"
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== DARTIS (PANGAEA {DARTIS_DOI}) ===")
    print(f"  landing page: {DARTIS_LANDING}")
    print(f"  destination : {dest_dir}")
    print("\n  PANGAEA exposes no stable bulk-download API for this record.")
    print("  Open the landing page, use 'Download dataset as zip', unpack into")
    print("  the destination above, then run: python -m ml.audit --dataset dartis")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=["trujillo", "dartis"], required=True)
    ap.add_argument("--part", type=int, action="append", choices=[1, 2, 3],
                    help="Trujillo part; repeatable. Default: 3 (test harness first).")
    ap.add_argument("--list", dest="list_only", action="store_true",
                    help="Show files and sizes without downloading.")
    args = ap.parse_args(argv)
    acquire_single_instance_lock()

    if args.dataset == "trujillo":
        download_trujillo(sorted(set(args.part or [3])), args.list_only)
    else:
        download_dartis(args.list_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
