"""Small file/config helpers shared by the engine CLIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .errors import missing_input


def require_file(path: str | Path, *, what: str) -> Path:
    """Return ``path`` as a Path, or raise a structured MISSING_INPUT."""
    p = Path(path)
    if not p.exists():
        raise missing_input(f"{what} not found: {p}", path=str(p), kind=what)
    if not p.is_file():
        raise missing_input(f"{what} is not a file: {p}", path=str(p), kind=what)
    return p


def read_json(path: str | Path, *, what: str = "JSON file") -> dict[str, Any]:
    p = require_file(path, what=what)
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise missing_input(f"{what} is not valid JSON: {p} ({exc})", path=str(p)) from exc


def read_yaml(path: str | Path, *, what: str = "YAML file") -> dict[str, Any]:
    p = require_file(path, what=what)
    try:
        with p.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise missing_input(f"{what} is not valid YAML: {p} ({exc})", path=str(p)) from exc


def write_json(path: str | Path, payload: Any, *, indent: int = 2) -> Path:
    """Write ``payload`` as UTF-8 JSON, creating parent directories as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=indent, ensure_ascii=False)
        fh.write("\n")
    return p
