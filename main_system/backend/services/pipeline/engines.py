"""Adapters onto the other developers' components.

Each service lives in its own directory with its own dependencies and its own
CLI. The main system invokes those CLIs as subprocesses rather than importing
them, for three reasons that matter in this project specifically:

  * their modules assume their own directory is the working directory (several
    of Nandha's tests use relative paths), so a subprocess with `cwd` set is
    the honest way to call them;
  * Engine B's OpenDrift backend needs a conda environment the main system does
    not have -- a subprocess can point at a different interpreter, an import
    cannot;
  * a component that segfaults or hangs takes down a subprocess, not the API
    server.

Every call returns a StageResult carrying the contract file, which stage
actually ran, and any warnings -- so the orchestrator can badge a degraded
result rather than pretending it was clean.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[4]

ENGINES_DIR = REPO_ROOT / "analysis_engines"
SATELLITE_DIR = REPO_ROOT / "scene_service"
METOCEAN_DIR = REPO_ROOT / "metocean_service"
AIS_DIR = REPO_ROOT / "ais_service"

# Default timeout per component call. A drift run over a large grid is the slow
# one; everything else is seconds.
DEFAULT_TIMEOUT = 900


@dataclass
class StageResult:
    ok: bool
    output: Optional[Path] = None
    engine_used: str = "unknown"
    warnings: List[str] = field(default_factory=list)
    error_class: Optional[str] = None
    detail: str = ""
    seconds: float = 0.0
    stdout: str = ""


def python_for(component_dir: Path) -> str:
    """Interpreter to run a component with.

    A component may ship its own environment (Engine B's OpenDrift conda env is
    the expected case). If one exists beside the component, prefer it; the
    project venv is the fallback. This is what lets the drift engine use
    OpenDrift without forcing GDAL into the main system's environment.
    """
    for candidate in (component_dir / ".venv" / "Scripts" / "python.exe",
                      component_dir / ".venv" / "bin" / "python",
                      REPO_ROOT / ".venv" / "Scripts" / "python.exe",
                      REPO_ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def run_component(cwd: Path, args: List[str], timeout: int = DEFAULT_TIMEOUT,
                  expect: Optional[Path] = None) -> StageResult:
    """Invoke a component CLI and interpret the result.

    Success is judged on the contract file existing, not only on the exit code:
    a component that exits 0 without producing its output has not succeeded,
    and one that warns on stderr while writing a valid file has.
    """
    t0 = time.time()
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    try:
        proc = subprocess.run(
            [python_for(cwd), *args], cwd=str(cwd), env=env,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return StageResult(False, error_class="TIMEOUT", seconds=time.time() - t0,
                           detail=f"component exceeded {timeout}s")
    except Exception as exc:
        return StageResult(False, error_class="UNAVAILABLE",
                           seconds=time.time() - t0,
                           detail=f"{type(exc).__name__}: {exc}")

    elapsed = time.time() - t0
    combined = (proc.stdout or "") + (proc.stderr or "")
    produced = expect is not None and Path(expect).exists()

    if produced:
        return StageResult(
            True, Path(expect), engine_used=_engine_from_output(combined),
            warnings=_warnings_from_output(combined), seconds=elapsed,
            detail=f"exit {proc.returncode}", stdout=combined[-4000:])

    # The engines print a JSON status object; pull the real message out of it
    # rather than reporting whatever the last line happened to be (which is
    # usually just a closing brace).
    detail = _structured_error(combined) or ""
    if not detail:
        tail = [l for l in (proc.stderr or proc.stdout or "").strip().splitlines()
                if l.strip() and l.strip() not in "{}[]"]
        detail = tail[-1][:200] if tail else f"exit {proc.returncode}, no output"
    return StageResult(
        False, error_class=_error_class(combined), seconds=elapsed,
        detail=detail, stdout=combined[-4000:])


def _structured_error(text: str) -> Optional[str]:
    """Engines emit {"ok": false, "error": {"error_class", "message"}}."""
    start = text.find("{")
    while start != -1:
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            msg = err.get("message", "")
            missing = (err.get("detail") or {}).get("missing")
            return f"{msg}" + (f" (missing: {missing})" if missing else "")
        return None
    return None


def _engine_from_output(text: str) -> str:
    """Which backend a component reports having used, e.g. openoil vs euler."""
    for key in ("openoil", "oceandrift", "euler", "threshold_fallback", "ml"):
        if key in text.lower():
            return key
    return "primary"


def _warnings_from_output(text: str) -> List[str]:
    out = []
    for line in text.splitlines():
        low = line.lower()
        if any(w in low for w in ("warning:", "warn ", "falling back", "fallback",
                                  "degraded", "using euler")):
            cleaned = line.strip()
            if cleaned and cleaned not in out:
                out.append(cleaned[:200])
    return out[:6]


def _error_class(text: str) -> str:
    """Map component output onto the project's shared error taxonomy."""
    low = text.lower()
    for needle, cls in (
        ("missing_input", "MISSING_INPUT"), ("bad_grid", "BAD_GRID"),
        ("empty_mask", "EMPTY_MASK"),
        ("no_vessels_in_window", "NO_VESSELS_IN_WINDOW"),
        ("auth", "AUTH_FAILED"), ("timeout", "TIMEOUT"),
        ("no such file", "MISSING_INPUT"), ("filenotfound", "MISSING_INPUT"),
        ("modulenotfound", "UNAVAILABLE"), ("importerror", "UNAVAILABLE"),
    ):
        if needle in low:
            return cls
    return "BAD_RESPONSE"


# --------------------------------------------------------------------------
# Engine A/B/C -- Nandha
# --------------------------------------------------------------------------


def characterise(mask: Path, scene_meta: Path, out: Path,
                 scene_db: Optional[Path] = None,
                 confidence: Optional[float] = None) -> StageResult:
    args = ["-m", "engines.characterise", "--mask", str(mask),
            "--scene-meta", str(scene_meta), "--out", str(out)]
    if scene_db:
        args += ["--scene-db", str(scene_db)]
    if confidence is not None:
        args += ["--confidence", str(confidence)]
    return run_component(ENGINES_DIR, args, expect=out)


def drift(slick: Path, out: Path, currents: Optional[Path] = None,
          wind: Optional[Path] = None, mode: str = "hindcast",
          hours: int = 24, engine: str = "auto") -> StageResult:
    args = ["-m", "engines.drift", "--slick", str(slick), "--out", str(out),
            "--mode", mode, "--hours", str(hours), "--engine", engine]
    if currents:
        args += ["--currents", str(currents)]
    if wind:
        args += ["--wind", str(wind)]
    return run_component(ENGINES_DIR, args, expect=out)


def generate_ais(bbox, start: str, end: str, out: Path,
                 culprit_json: Path, n_vessels: int = 40,
                 seed: int = 1337, fleet_seed: int = None) -> StageResult:
    """Synthesise AIS traffic around a computed origin, with a planted culprit.

    This is the designed path for the Indian-waters headline scene, where no
    real AIS exists. The culprit is planted at the origin the DRIFT ENGINE
    actually computed, not at a fixed location -- otherwise the vessel and the
    origin cloud describe two unrelated events and attribution correctly finds
    nothing. Output carries source="synthetic" so the UI badges it truthfully.
    """
    args = ["-m", "ais.cli", "generate-ais",
            "--bbox", *[str(v) for v in bbox],
            "--start", start, "--end", end,
            "--n-vessels", str(n_vessels),
            "--culprit-json", str(culprit_json),
            "--seed", str(seed), "--out", str(out)]
    if fleet_seed is not None:
        args += ["--fleet-seed", str(fleet_seed)]
    return run_component(AIS_DIR, args, expect=out)


def attribution(origin: Path, vessels: Path, out: Path,
                slick: Optional[Path] = None,
                investigation_id: Optional[str] = None) -> StageResult:
    args = ["-m", "engines.attribution", "--origin", str(origin),
            "--vessels", str(vessels), "--out", str(out)]
    if slick:
        args += ["--slick", str(slick)]
    if investigation_id:
        args += ["--investigation-id", investigation_id]
    return run_component(ENGINES_DIR, args, expect=out)
