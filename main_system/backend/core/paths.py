"""Map file paths recorded on one host onto the host serving them now.

Sealed run artefacts (scene_meta.json, manifests) and older registry rows
record ABSOLUTE paths from the machine that produced them -- a Windows laptop
writes `C:\\Users\\...\\data\\scenes\\...`. Those files are content-hashed, so
they cannot be rewritten for a deployment without breaking their own integrity
check. Instead every reader passes the recorded path through `host_path`, which
returns it untouched when it exists here and otherwise re-anchors it on this
host's repo root or DATA_ROOT.

On the machine that recorded the path nothing changes: the path exists, so it
comes back as-is.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from backend.core.config import REPO_ROOT, get_settings

_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:)?[\\/]")
_SEP = re.compile(r"[\\/]+")
# Top-level directories of this repo that recorded paths point into. "data"
# is re-anchored on DATA_ROOT, which a deployment may mount elsewhere.
_REPO_DIRS = ("contracts", "ais_service", "analysis_engines", "scene_service",
              "metocean_service", "main_system", "scripts")


def host_path(value: str | os.PathLike) -> Path:
    """`value` if it exists here or is relative; else the same file under this
    host's repo/data root when one is there; else `value` unchanged, so a
    caller's own "missing" handling still reports the recorded path."""
    raw = os.fspath(value)
    path = Path(raw)
    if not _ABSOLUTE.match(raw) or path.exists():
        return path
    parts = [p for p in _SEP.split(raw) if p]
    data_root = get_settings().data_root
    for i, part in enumerate(parts):
        if part == "data":
            candidate = data_root.joinpath(*parts[i + 1:])
        elif part in _REPO_DIRS:
            candidate = REPO_ROOT.joinpath(*parts[i:])
        else:
            continue
        if candidate.exists():
            return candidate
    return path
