"""Build and publish the demo data bundle a deployment boots from.

`data/` on the demo laptop is ~36 GB, most of it training corpora and raw
downloads that serving the system never reads. This stages ONLY what the
running app needs -- the registry DB, every sealed run, the scene rasters the
registered investigations point at, the AIS store, hindcast output, thumbnails
-- plus the two ONNX weights, into one folder, and uploads it to a PRIVATE
Hugging Face dataset repo. The container pulls it at boot
(scripts/deploy/fetch_data.py).

    python scripts/deploy/data_bundle.py build              # -> .deploy/bundle
    python scripts/deploy/data_bundle.py upload --repo <user>/oceantrace-data

Nothing under data/runs is modified: run artefacts are content-hashed, and the
server re-anchors the absolute paths they recorded (backend/core/paths.py).
Only the COPY of the database is rewritten, so registry rows point at the
container's layout (/app/...) rather than at this laptop.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_DIR = REPO_ROOT / "main_system" / "backend" / "services" / "detection" / "weights"
WEIGHTS = ("screen.onnx", "segment.onnx")
TARGET_REPO_ROOT = "/app"          # repo root inside the container image

# Whole trees the app reads at runtime (relative to data/).
TREES = ("hindcast", "metocean", "cache/sar_thumbs", "ais/store", "ais/real",
         "scenes/world", "scenes/varitest", "samples_preview")
# Inside a scene directory, keep the calibrated raster the app renders and the
# small sidecars; skip the raw SAFE product, zips and source COGs (re-derivable,
# and several GB).
SCENE_KEEP = {"scene_sigma0_db.tif"}
SCENE_SMALL_BYTES = 50 * 1024 * 1024
SCENE_SKIP_SUFFIXES = (".zip", ".tiff", ".safe")

# Registry columns that hold paths from the recording host.
PATH_COLUMNS = (("investigations", "scene_path"), ("investigations", "scene_meta_path"),
                ("runs", "manifest_path"), ("hindcast_particle_snapshots", "parquet_path"))
JSON_COLUMNS = (("jobs", "inputs_json"), ("reports", "body_json"))


def _rebaser(src_repo: Path):
    """str -> str: a path under this checkout becomes the same path under
    /app with forward slashes; anything else is returned unchanged."""
    root = str(src_repo).replace("\\", "/").rstrip("/").lower() + "/"

    def rebase(value: str) -> str:
        norm = value.replace("\\", "/")
        if not norm.lower().startswith(root):
            return value
        return TARGET_REPO_ROOT + "/" + norm[len(root):]

    return rebase


def _walk(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    return obj


def _place(src: Path, dst: Path) -> int:
    """Hard-link when possible (same volume, no 9 GB duplicate), else copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return src.stat().st_size


def _place_tree(src: Path, dst: Path) -> tuple[int, int]:
    files = size = 0
    for f in src.rglob("*"):
        if f.is_file():
            size += _place(f, dst / f.relative_to(src))
            files += 1
    return files, size


def _referenced_files(db: Path, data: Path) -> set[Path]:
    """Every file under data/ that a registered investigation points at."""
    out: set[Path] = set()
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT scene_path, scene_meta_path FROM investigations").fetchall()
    finally:
        con.close()
    for row in rows:
        for value in row:
            if not value:
                continue
            parts = [p for p in re.split(r"[\\/]+", value) if p]
            if "data" not in parts:
                continue
            rel = Path(*parts[parts.index("data") + 1:])
            if (data / rel).is_file():
                out.add(rel)
    return out


def _scene_files(data: Path) -> list[Path]:
    """The renderable part of every scene directory under data/scenes."""
    keep = []
    scenes = data / "scenes"
    for f in scenes.iterdir():
        if f.is_file() and f.suffix == ".json":
            keep.append(f.relative_to(data))
    for d in scenes.iterdir():
        if not d.is_dir() or d.name in ("world", "varitest", "s2"):
            continue
        for f in d.iterdir():                     # top level only: skips *.SAFE/
            if not f.is_file() or f.name.lower().endswith(SCENE_SKIP_SUFFIXES):
                continue
            if f.name in SCENE_KEEP or f.stat().st_size <= SCENE_SMALL_BYTES:
                keep.append(f.relative_to(data))
    return keep


def _prepare_db(src: Path, dst: Path, scrub_passwords: bool) -> dict:
    """Consistent copy (sqlite backup API, safe while the app is running),
    then rewrite recorded paths for the container layout."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    rebase = _rebaser(REPO_ROOT)
    stats = {}
    con = sqlite3.connect(dst)
    try:
        for table, col in PATH_COLUMNS:
            rows = con.execute(f"SELECT rowid, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall()
            changed = [(rebase(v), rid) for rid, v in rows if rebase(v) != v]
            con.executemany(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", changed)
            stats[f"{table}.{col}"] = len(changed)
        for table, col in JSON_COLUMNS:
            rows = con.execute(f"SELECT rowid, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall()
            changed = []
            for rid, text in rows:
                try:
                    new = json.dumps(_walk(json.loads(text), rebase))
                except (TypeError, ValueError):
                    continue
                if new != text and json.loads(new) != json.loads(text):
                    changed.append((new, rid))
            con.executemany(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", changed)
            stats[f"{table}.{col}"] = len(changed)
        # Stored provider credentials are encrypted with THIS host's SECRET_KEY
        # and useless anywhere else; a deployment sets its own keys.
        stats["api_keys_dropped"] = con.execute("DELETE FROM api_keys").rowcount
        if scrub_passwords:
            stats["passwords_scrubbed"] = con.execute(
                "UPDATE users SET password_hash = ''").rowcount
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    return stats


def build(args) -> int:
    data = Path(args.data).resolve()
    out = Path(args.out).resolve()
    db = data / "oceantrace.db"
    if not db.is_file():
        print(f"no registry at {db}", file=sys.stderr)
        return 1
    for name in WEIGHTS:
        if not (WEIGHTS_DIR / name).is_file():
            print(f"missing weight {WEIGHTS_DIR / name}", file=sys.stderr)
            return 1
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    files = size = 0
    runs = data / "runs"
    for run in sorted(runs.iterdir()):
        if run.is_dir() and run.name != "training":
            n, s = _place_tree(run, out / "runs" / run.name)
            files, size = files + n, size + s
    for tree in TREES:
        if (data / tree).is_dir():
            n, s = _place_tree(data / tree, out / tree)
            files, size = files + n, size + s
    for rel in sorted(set(_scene_files(data)) | _referenced_files(db, data)):
        size += _place(data / rel, out / rel)
        files += 1
    for name in WEIGHTS:
        size += _place(WEIGHTS_DIR / name, out / "_weights" / name)
        files += 1

    db_stats = _prepare_db(db, out / "oceantrace.db", args.scrub_passwords)
    size += (out / "oceantrace.db").stat().st_size
    files += 1

    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = None
    manifest = {"built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "code_git_sha": sha, "files": files, "bytes": size,
                "db_rewrites": db_stats}
    (out / "BUNDLE.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print(f"\nbundle ready: {out}  ({size / 1e9:.2f} GB, {files} files)")
    return 0


def upload(args) -> int:
    from huggingface_hub import HfApi

    out = Path(args.out).resolve()
    if not (out / "BUNDLE.json").is_file():
        print(f"no bundle at {out}; run `build` first", file=sys.stderr)
        return 1
    api = HfApi()
    # Always private: the bundle carries account hashes and the trained weights.
    api.create_repo(args.repo, repo_type="dataset", private=True, exist_ok=True)
    info = api.repo_info(args.repo, repo_type="dataset")
    if not info.private:
        print(f"{args.repo} is PUBLIC; refusing to upload the bundle there", file=sys.stderr)
        return 1
    # Resumable and parallel: an interrupted upload continues where it stopped.
    api.upload_large_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(out),
                            ignore_patterns=[".cache/**"])
    print(f"uploaded {out} -> https://huggingface.co/datasets/{args.repo}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="stage the bundle locally")
    b.add_argument("--data", default=str(REPO_ROOT / "data"))
    b.add_argument("--out", default=str(REPO_ROOT / ".deploy" / "bundle"))
    b.add_argument("--scrub-passwords", action="store_true",
                   help="blank every password hash (only the public evaluator view works)")
    u = sub.add_parser("upload", help="push the bundle to a private HF dataset")
    u.add_argument("--repo", required=True, help="e.g. IndhuPriyan/oceantrace-data")
    u.add_argument("--out", default=str(REPO_ROOT / ".deploy" / "bundle"))
    args = p.parse_args(argv)
    return build(args) if args.cmd == "build" else upload(args)


if __name__ == "__main__":
    sys.exit(main())
