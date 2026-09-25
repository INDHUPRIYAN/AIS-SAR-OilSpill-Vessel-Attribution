"""Pull the demo data bundle into DATA_ROOT at container start.

Run by docker/app-entrypoint.sh. Does nothing unless OT_DATA_REPO is set, so a
laptop, CI, or a VM that bind-mounts its own data/ is unaffected.

    OT_DATA_REPO      <user>/<dataset> built by scripts/deploy/data_bundle.py
    OT_DATA_REVISION  branch/tag/commit to pin (default: main)
    HF_TOKEN          read token -- the bundle repo is private

A marker records which bundle revision is on disk, so a restart on persistent
storage (a VM volume) skips the download AND keeps whatever the running system
wrote to its database since. On an ephemeral host (a free HF Space) every cold
start re-downloads and the demo comes back in its published state.

Exit 0 = data present (or nothing to do); 1 = download failed. The entrypoint
treats 1 as a warning: the API still boots and reports what is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_DIR = REPO_ROOT / "main_system" / "backend" / "services" / "detection" / "weights"
MARKER = ".bundle-revision"


def main() -> int:
    data_root = Path(os.getenv("DATA_ROOT", str(REPO_ROOT / "data")))
    repo = os.getenv("OT_DATA_REPO", "").strip()
    if repo:
        status = _download(repo, data_root)
        if status:
            return status
    else:
        print("[fetch_data] OT_DATA_REPO not set; using whatever is in DATA_ROOT.")
    _install_weights(data_root)
    return 0


def _download(repo: str, data_root: Path) -> int:
    data_root.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import HfApi, snapshot_download

    token = os.getenv("HF_TOKEN") or None
    revision = os.getenv("OT_DATA_REVISION", "main")
    try:
        sha = HfApi().repo_info(repo, repo_type="dataset", revision=revision, token=token).sha
    except Exception as exc:                                  # noqa: BLE001
        print(f"[fetch_data] cannot read {repo}@{revision}: {type(exc).__name__}: {exc}")
        return 1

    marker = data_root / MARKER
    if marker.is_file() and marker.read_text().strip() == sha \
            and (data_root / "oceantrace.db").is_file():
        print(f"[fetch_data] bundle {sha[:10]} already on disk; skipping download.")
    else:
        started = time.monotonic()
        print(f"[fetch_data] downloading {repo}@{sha[:10]} into {data_root} ...")
        try:
            snapshot_download(repo_id=repo, repo_type="dataset", revision=sha,
                              local_dir=str(data_root), token=token, max_workers=16)
        except Exception as exc:                              # noqa: BLE001
            print(f"[fetch_data] download failed: {type(exc).__name__}: {exc}")
            return 1
        marker.write_text(sha)
        print(f"[fetch_data] done in {time.monotonic() - started:.0f}s")
    return 0


def _install_weights(data_root: Path) -> None:
    """Weights travel inside the bundle (downloaded or bind-mounted); put them
    where /detect reads them. get_weights.sh then hash-verifies them."""
    staged = data_root / "_weights"
    if staged.is_dir():
        WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
        for onnx in staged.glob("*.onnx"):
            target = WEIGHTS_DIR / onnx.name
            if not target.exists() or target.stat().st_size != onnx.stat().st_size:
                shutil.copy2(onnx, target)
                print(f"[fetch_data] installed weight {onnx.name}")

    manifest = data_root / "BUNDLE.json"
    if manifest.is_file():
        info = json.loads(manifest.read_text(encoding="utf-8"))
        print(f"[fetch_data] bundle built {info.get('built_utc')} from code "
              f"{info.get('code_git_sha')}: {info.get('files')} files")


if __name__ == "__main__":
    sys.exit(main())
