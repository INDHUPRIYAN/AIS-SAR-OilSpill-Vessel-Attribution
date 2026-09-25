"""Publish the current checkout to a Hugging Face Docker Space.

The Space builds docker/app.Dockerfile on Hugging Face's own builders, runs it
on the free CPU tier (2 vCPU, 16 GB RAM) and serves it over HTTPS at
https://<user>-<space>.hf.space. Only git-TRACKED files of the directories the
image needs are uploaded -- never .env, data/, weights or local scratch.

    python scripts/deploy/push_space.py --space IndhuPriyan/oceantrace
    python scripts/deploy/push_space.py --space IndhuPriyan/oceantrace \
        --configure --data-repo IndhuPriyan/oceantrace-data

--configure (first deploy, or to change settings) also writes the Space's
variables and secrets: the data bundle to boot from, the public evaluator
view, fresh session/vault secrets, and every provider key found in .env.
Used by .github/workflows/deploy-space.yml on every push to main.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Exactly what docker/app.Dockerfile COPYs.
INCLUDE = ("pyproject.toml", ".env.example", ".dockerignore", ".gitattributes",
           "contracts", "main_system", "analysis_engines", "scene_service",
           "metocean_service", "ais_service", "scripts", "docker/app-entrypoint.sh")
# Provider credentials copied from .env into Space secrets by --configure.
PROVIDER_KEYS = ("CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET", "CDSE_USERNAME", "CDSE_PASSWORD",
                 "EARTHDATA_USER", "EARTHDATA_PASS", "ASF_USERNAME", "ASF_PASSWORD",
                 "CMEMS_USERNAME", "CMEMS_PASSWORD", "CDSAPI_KEY", "CDSAPI_URL",
                 "AISSTREAM_API_KEY")

SPACE_README = """---
title: OceanTrace
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
startup_duration_timeout: 1h
pinned: true
short_description: SAR oil-spill detection with AIS vessel attribution
---

# OceanTrace — SIH 2026 · PS 26143

Sentinel-1 SAR oil-spill detection, drift hindcast and AIS vessel attribution.

This Space is built automatically from
[the GitHub repository]({github}) (commit `{sha}`).
Open the app directly at **https://{host}.hf.space** for the full-window view.
"""


def _tracked(paths) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=REPO_ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def _dotenv() -> dict:
    env = {}
    path = REPO_ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def stage(space: str, dest: Path) -> str:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    for rel in _tracked(INCLUDE):
        src = REPO_ROOT / rel
        if src.is_file():                      # tracked but deleted locally
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    shutil.copy2(REPO_ROOT / "docker" / "app.Dockerfile", dest / "Dockerfile")
    user, name = space.split("/", 1)
    host = f"{user}-{name}".lower().replace("_", "-").replace(".", "-")
    (dest / "README.md").write_text(SPACE_README.format(
        github="https://github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution",
        sha=sha, host=host), encoding="utf-8")
    return sha


def configure(api, space: str, data_repo: str, read_token: str | None) -> None:
    env = {**_dotenv(), **os.environ}
    api.add_space_variable(space, "OT_DATA_REPO", data_repo)
    # SIH judges open the link with no account; sign-in still works for RBAC.
    api.add_space_variable(space, "OT_PUBLIC_EVALUATOR", "true")
    token = read_token or env.get("HF_TOKEN")
    if not token:
        from huggingface_hub import get_token
        token = get_token()
    api.add_space_secret(space, "HF_TOKEN", token, description="reads the private data bundle")
    # Fresh per deployment: the bundle ships no stored credentials, so nothing
    # encrypted under the laptop's key needs to stay readable.
    api.add_space_secret(space, "SECRET_KEY", secrets.token_urlsafe(32))
    api.add_space_secret(space, "JWT_SECRET", secrets.token_urlsafe(32))
    copied = []
    for key in PROVIDER_KEYS:
        if env.get(key):
            api.add_space_secret(space, key, env[key])
            copied.append(key)
    print(f"[push_space] variables set; secrets: HF_TOKEN, SECRET_KEY, JWT_SECRET"
          + (", " + ", ".join(copied) if copied else " (no provider keys found in .env)"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--space", required=True, help="<user>/<space>, e.g. IndhuPriyan/oceantrace")
    p.add_argument("--configure", action="store_true", help="also set variables + secrets")
    p.add_argument("--data-repo", help="the private dataset from data_bundle.py (with --configure)")
    p.add_argument("--read-token", help="token the Space uses to read the bundle "
                                        "(default: HF_TOKEN / your login)")
    p.add_argument("--dry-run", action="store_true", help="stage only, print the file count")
    args = p.parse_args(argv)
    if args.configure and not args.data_repo:
        p.error("--configure needs --data-repo")

    # Staging copies TRACKED files: an uncommitted new module would be left out
    # and the Space would build code that exists nowhere else.
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *INCLUDE,
                            "docker/app.Dockerfile"], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty and not args.dry_run:
        print("[push_space] uncommitted changes in deployed paths -- commit first:\n"
              + dirty, file=sys.stderr)
        return 1

    from huggingface_hub import HfApi

    with tempfile.TemporaryDirectory(prefix="oceantrace-space-") as tmp:
        dest = Path(tmp)
        sha = stage(args.space, dest)
        n = sum(1 for f in dest.rglob("*") if f.is_file())
        print(f"[push_space] staged {n} files from {sha}")
        if args.dry_run:
            return 0
        api = HfApi()
        api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)
        if args.configure:
            configure(api, args.space, args.data_repo, args.read_token)
        # delete_patterns prunes files removed from the repo since the last push.
        api.upload_folder(repo_id=args.space, repo_type="space", folder_path=str(dest),
                          commit_message=f"Deploy {sha}", delete_patterns=["*", "**/*"])
    print(f"[push_space] pushed {sha} -> https://huggingface.co/spaces/{args.space}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
