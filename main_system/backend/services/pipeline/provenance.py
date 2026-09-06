"""Artefact content hashes, idempotency keys and the immutability guard.

Design doc v2 sections 10, 12 and 14:

    "Immutability rule: a completed run's artefacts are never overwritten. A
    re-run creates a new run_id referencing the same scene. Record a content
    hash per artefact. This is what makes a two-year-old investigation
    reproducible."                                                      (§12)

    "Every stage is idempotent -- keyed on sha256(inputs + params). Retries are
    safe."                                                          (§10 inv 1)

    "Immutable artefacts with content hashes in the run record."          (§14)

Why this is load-bearing rather than paperwork: §1 says the retrospective case
"may support an enforcement action against a named operator". If a defence
lawyer asks whether the GeoJSON shown in court is the one the pipeline produced
in 2026, the answer has to be a hash comparison, not a shrug. Everything here
exists to make :func:`verify_run` able to answer that question years later.

Three separate ideas, deliberately in one small module:

``sha256_file`` / ``artefact_record``
    What was produced. Recorded in ``manifest.json`` under ``artefacts``.

``idempotency_key``
    What produced it. ``sha256(stage + inputs + params)`` over a canonical JSON
    encoding, so the same stage over the same inputs yields the same key on any
    machine, in any dict order. This is the key §5's job message carries and the
    one a retry is safe under.

``verify_run`` / ``assert_writable``
    That it has not changed since. ``verify_run`` re-hashes and reports;
    ``assert_writable`` refuses to let a completed run's directory be written
    into a second time.

Hashes are of file *bytes*, not of parsed content. Two GeoJSON files that differ
only in key order are different artefacts here, and that is correct -- byte-level
reproducibility is what §3 asks for ("reproducible years later, byte for byte").
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: Files that are part of the reproducible record of a run. Everything else in
#: a run directory (status.json, engine scratch dirs, warning side-cars) is
#: working state and is deliberately NOT hashed -- status.json is rewritten
#: several times during a run by design, and hashing it would mean every run
#: failed its own verification.
ARTEFACT_FILES = (
    "scene_meta.json",
    "detect_response.json",
    "raw_mask.tif",
    "slick.geojson",
    "origin_cloud.geojson",
    "forecast.geojson",
    "vessels.parquet",
    "suspects.json",
)

MANIFEST_NAME = "manifest.json"
_CHUNK = 1 << 20


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def sha256_file(path: os.PathLike | str) -> str:
    """Streaming SHA-256 of a file's bytes.

    Streamed rather than read whole: a run's ``raw_mask.tif`` for a full IW GRDH
    scene is hundreds of megabytes (§3, "scenes are huge"), and this runs at the
    end of every single run.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def artefact_record(path: Path, root: Optional[Path] = None) -> Dict[str, Any]:
    """One artefact's identity: relative name, size, content hash."""
    rel = path.name if root is None else str(path.relative_to(root)).replace("\\", "/")
    return {
        "file": rel,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def collect_artefacts(run_dir: Path,
                      files: Iterable[str] = ARTEFACT_FILES) -> List[Dict[str, Any]]:
    """Hash every artefact a run actually produced.

    Missing files are skipped, not recorded as absent: a degraded run that never
    got an AIS layer has no vessels.parquet, and §10 invariant 4 ("partial
    results are always shown") means that is a normal outcome, not an error.
    """
    run_dir = Path(run_dir)
    return [artefact_record(run_dir / name, run_dir)
            for name in files if (run_dir / name).is_file()]


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    """Make a value hashable in a way that does not depend on incidental order.

    Paths become POSIX strings so a Windows run and a Linux run of the same
    inputs agree; datetimes become UTC ISO-8601 with a Z, per Standing Rule 1.
    """
    if isinstance(value, Path):
        return str(value).replace("\\", "/")
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float):
        # 1.0 and 1 must not produce different keys, and 0.1+0.2 must not
        # produce a different key from 0.30000000000000004 on another machine.
        return round(value, 9)
    return value


def idempotency_key(stage: str, inputs: Optional[Dict[str, Any]] = None,
                    params: Optional[Dict[str, Any]] = None) -> str:
    """``sha256(stage + inputs + params)`` -- §10 invariant 1, §5's job message.

    Two calls with the same stage, the same input URIs and the same parameters
    produce the same key regardless of dict ordering, path separator, or float
    representation. A worker that has already completed this key can skip the
    work and return the recorded artefact, which is what makes a retry safe.
    """
    payload = json.dumps(
        {"stage": stage,
         "inputs": _canonical(inputs or {}),
         "params": _canonical(params or {})},
        sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(payload.encode("utf-8"))


# --------------------------------------------------------------------------
# verification and the immutability guard
# --------------------------------------------------------------------------


class RunImmutable(RuntimeError):
    """Raised when something tries to write into a completed run's directory."""


def verify_run(run_dir: os.PathLike | str) -> Dict[str, Any]:
    """Re-hash a completed run's artefacts and compare against its manifest.

    Returns ``{"ok": bool, "checked": int, "problems": [...], "run_id": ...}``.
    Rather than raising, because the caller is usually an API endpoint or an
    operator running a spot check, and "three files changed, here they are" is
    more useful than a traceback.

    Four things can go wrong, and they mean different things:

      * ``no manifest``            -- the run never completed
      * ``manifest has no hashes`` -- produced before this module existed; not a
                                      tamper signal, just an unverifiable run
      * ``missing``                -- an artefact was deleted
      * ``changed``                -- an artefact was overwritten. This is the
                                      one that invalidates an investigation.
    """
    run_dir = Path(run_dir)
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"ok": False, "checked": 0, "run_id": run_dir.name,
                "problems": [f"no manifest at {MANIFEST_NAME}; run never completed"]}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = manifest.get("artefacts") or []
    if not recorded:
        return {"ok": False, "checked": 0, "run_id": manifest.get("run_id", run_dir.name),
                "problems": ["manifest records no artefact hashes; this run "
                             "predates content hashing and cannot be verified"],
                "unverifiable": True}

    problems: List[str] = []
    for entry in recorded:
        path = run_dir / entry["file"]
        if not path.is_file():
            problems.append(f"missing: {entry['file']}")
            continue
        actual = sha256_file(path)
        if actual != entry.get("sha256"):
            problems.append(
                f"changed: {entry['file']} "
                f"(recorded {str(entry.get('sha256'))[:12]}..., "
                f"now {actual[:12]}...)")

    # An artefact that appeared after the run finished is also a change to the
    # record, even though nothing recorded was touched.
    extra = [name for name in ARTEFACT_FILES
             if (run_dir / name).is_file()
             and name not in {e["file"] for e in recorded}]
    problems += [f"added after the run completed: {name}" for name in extra]

    return {"ok": not problems, "checked": len(recorded),
            "run_id": manifest.get("run_id", run_dir.name),
            "generated_utc": manifest.get("generated_utc"),
            "problems": problems}


def is_complete(run_dir: os.PathLike | str) -> bool:
    """True once a run has written its manifest -- i.e. its artefacts are frozen."""
    return (Path(run_dir) / MANIFEST_NAME).is_file()


def assert_writable(run_dir: os.PathLike | str) -> None:
    """Refuse to reuse a completed run's directory. §12's immutability rule.

    The failure this prevents is quiet and expensive: re-running an existing
    ``run_id`` would overwrite the artefacts an earlier investigation was
    concluded from, and the manifest hashes would be rewritten to match, so the
    substitution would leave no trace. A re-run gets a new ``run_id``.
    """
    run_dir = Path(run_dir)
    if is_complete(run_dir):
        raise RunImmutable(
            f"run '{run_dir.name}' has already completed and its artefacts are "
            f"immutable (design doc section 12). Start a new run_id against the same "
            f"scene instead of overwriting this one.")


def seal(run_dir: os.PathLike | str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the artefact hashes to a finished manifest, in place.

    Called once, at the end of :func:`backend.services.pipeline.run.run_pipeline`,
    after every stage has written its output. Also records a hash *of* the
    artefact list, so that tampering with both a file and its manifest entry
    still shows up against a run id quoted in a report.
    """
    run_dir = Path(run_dir)
    artefacts = collect_artefacts(run_dir)
    manifest["artefacts"] = artefacts
    manifest["artefact_digest"] = sha256_bytes(
        json.dumps(artefacts, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    manifest["immutable"] = True
    return manifest
