"""Pipeline orchestrator -- scene in, full contract set out.

Runs every stage, validates each output against its frozen contract, and where
a stage is unavailable falls back to the mock and *records that it did*. The
pipeline never halts and never lies about provenance: each stage lands in the
manifest with a status of

    ok       -- a real component produced and validated this
    mock     -- the owner's component is not wired in yet; mock served instead
    fallback -- the real component failed; a degraded path produced this
    failed   -- nothing usable (the run continues, the layer is absent)

The UI reads the manifest and badges every layer accordingly, which is what
keeps a demo honest when half the team's components are still in flight.

Stage ownership (see docs/PS26143_Team_Split_Handbook.md):
    detect        Indhu     -- real
    characterise  Nandha    -- stand-in until Engine A lands
    drift         Nandha    -- mock until Engine B lands (also needs Keerthana)
    attribution   Nandha    -- mock until Engine C lands (also needs Krishnan)

Usage:
    python -m backend.services.pipeline.run --scene contracts/mocks/scene_sigma0_db.tif \\
        --scene-meta contracts/mocks/scene_meta.json --run-id inv-001
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[4]
for p in (REPO_ROOT, REPO_ROOT / "1_indhu_main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from contracts.schemas import CONTRACTS  # noqa: E402

MOCKS = REPO_ROOT / "contracts" / "mocks"
RUNS = REPO_ROOT / "data" / "runs"


class Stage:
    """One pipeline stage and how it reports itself."""

    def __init__(self, name: str, owner: str, contract: Optional[str], output: str):
        self.name = name
        self.owner = owner
        self.contract = contract
        self.output = output
        self.status = "pending"
        self.detail = ""
        self.source = "unknown"
        self.seconds = 0.0
        self.warnings: List[str] = []

    def to_dict(self) -> dict:
        return {
            "stage": self.name, "owner": self.owner, "status": self.status,
            "output": self.output, "contract": self.contract,
            "source": self.source, "detail": self.detail,
            "seconds": round(self.seconds, 2), "warnings": self.warnings,
        }


def validate(contract: Optional[str], path: Path) -> Optional[str]:
    """Validate a produced file against its contract. Returns an error string."""
    if contract is None or contract not in CONTRACTS:
        return None
    model, _ = CONTRACTS[contract]
    try:
        model.model_validate_json(Path(path).read_text(encoding="utf-8"))
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:300]}"


def serve_mock(stage: Stage, out_dir: Path, reason: str) -> bool:
    """Copy the mock for a stage that could not run for real."""
    _, filename = CONTRACTS.get(stage.contract, (None, None))
    src = MOCKS / (filename or stage.output)
    if not src.exists():
        stage.status, stage.detail = "failed", f"{reason}; no mock at {src.name}"
        return False
    shutil.copy(src, out_dir / stage.output)
    stage.status, stage.source, stage.detail = "mock", "synthetic", reason
    return True


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


def stage_detect(stage: Stage, scene: Path, scene_id: str, meta: Optional[dict],
                 out_dir: Path, weights: Path, force_engine: Optional[str]) -> Optional[dict]:
    from backend.services.detection.service import detect

    resp = detect(scene, scene_id, out_dir, weights, meta, force_engine)
    stage.source = "real"
    stage.status = "ok" if resp.engine.value == "ml" else "fallback"
    stage.detail = (f"engine={resp.engine.value}, {len(resp.candidates)} candidate(s), "
                    f"confidence {resp.confidence}")
    warn_file = out_dir / "detect_warnings.json"
    if warn_file.exists():
        stage.warnings = json.loads(warn_file.read_text())
    return resp.model_dump(by_alias=True)


def stage_characterise(stage: Stage, scene: Path, out_dir: Path,
                       detect_result: dict, meta: Optional[dict]) -> bool:
    """Nandha's Engine A if present, else the coherent stand-in."""
    import numpy as np

    from backend.services.detection.service import read_scene
    from backend.services.pipeline.characterise_standin import characterise

    engine_a = REPO_ROOT / "2_nandha_engines" / "engines" / "characterise" / "spill_features.py"
    if engine_a.exists() and engine_a.stat().st_size > 2000:
        stage.detail = "Nandha's Engine A detected but not yet wired; using stand-in"

    import rasterio
    db, profile, valid = read_scene(scene)
    with rasterio.open(detect_result["mask_path"]) as src:
        mask = src.read(1)

    acquired = datetime.now(timezone.utc)
    if meta and meta.get("acquired_utc"):
        acquired = datetime.fromisoformat(meta["acquired_utc"].replace("Z", "+00:00"))

    payload = characterise(
        mask=mask, db=db, valid=valid, profile=profile,
        scene_id=detect_result["scene_id"], acquired_utc=acquired,
        model_version=detect_result["model_version"],
        engine=detect_result["engine"],
        mask_path=str(detect_result["mask_path"]).replace("\\", "/"),
    )
    for f in payload["features"]:
        f["properties"]["confidence"] = detect_result["confidence"]

    if not payload["features"]:
        stage.status, stage.detail = "failed", "no slick regions in the mask"
        return False

    (out_dir / stage.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    stage.source = "real"
    stage.status = "fallback"   # a stand-in is not Nandha's validated engine
    stage.detail = (f"stand-in for Engine A; {len(payload['features'])} slick(s), "
                    f"largest {payload['features'][0]['properties']['area_km2']} km2")
    return True


def stage_mocked(stage: Stage, out_dir: Path, reason: str) -> bool:
    return serve_mock(stage, out_dir, reason)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def run_pipeline(scene: Path, scene_meta: Optional[Path], run_id: str,
                 weights: Optional[Path] = None,
                 force_engine: Optional[str] = None) -> dict:
    t_start = time.time()
    out_dir = RUNS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = json.loads(Path(scene_meta).read_text()) if scene_meta else None
    scene_id = (meta or {}).get("scene_id") or Path(scene).stem
    if weights is None:
        from backend.services.detection.service import DEFAULT_WEIGHTS
        weights = DEFAULT_WEIGHTS

    stages = [
        Stage("detect", "Indhu", "detect", "detect_response.json"),
        Stage("characterise", "Nandha", "slick", "slick.geojson"),
        Stage("drift_hindcast", "Nandha", "origin_cloud", "origin_cloud.geojson"),
        Stage("drift_forecast", "Nandha", "forecast", "forecast.geojson"),
        Stage("attribution", "Nandha", "suspects", "suspects.json"),
    ]
    by_name = {s.name: s for s in stages}

    print(f"run {run_id}  scene {scene_id}\n" + "-" * 66)

    # --- detect -----------------------------------------------------------
    s = by_name["detect"]
    t0 = time.time()
    detect_result = None
    try:
        detect_result = stage_detect(s, Path(scene), scene_id, meta, out_dir,
                                     Path(weights), force_engine)
    except Exception as exc:
        s.status, s.detail = "failed", f"{type(exc).__name__}: {exc}"
    s.seconds = time.time() - t0

    # --- characterise -----------------------------------------------------
    s = by_name["characterise"]
    t0 = time.time()
    if detect_result is None:
        stage_mocked(s, out_dir, "detection failed upstream")
    else:
        try:
            if not stage_characterise(s, Path(scene), out_dir, detect_result, meta):
                stage_mocked(s, out_dir, s.detail or "characterisation produced nothing")
        except Exception as exc:
            stage_mocked(s, out_dir, f"stand-in failed ({type(exc).__name__}: {exc})")
    s.seconds = time.time() - t0

    # --- drift + attribution ---------------------------------------------
    # These need Keerthana's NetCDFs and Krishnan's parquet as well as Nandha's
    # engines, so for now they are served from mocks and badged as such.
    for name, why in (
        ("drift_hindcast", "Engine B not wired (needs currents.nc / wind.nc)"),
        ("drift_forecast", "Engine B not wired (needs currents.nc / wind.nc)"),
        ("attribution", "Engine C not wired (needs vessels.parquet)"),
    ):
        s = by_name[name]
        t0 = time.time()
        stage_mocked(s, out_dir, why)
        s.seconds = time.time() - t0

    # --- validate everything ---------------------------------------------
    for s in stages:
        produced = out_dir / s.output
        if s.status == "failed" or not produced.exists():
            continue
        err = validate(s.contract, produced)
        if err:
            s.warnings.append(f"CONTRACT VIOLATION: {err}")
            s.status = "failed"

    # copy the scene's own metadata alongside, so the UI has one folder to read
    if meta:
        (out_dir / "scene_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for extra in ("vessels.parquet", "provider_status.json"):
        src = MOCKS / extra
        if src.exists() and not (out_dir / extra).exists():
            shutil.copy(src, out_dir / extra)

    manifest = {
        "run_id": run_id,
        "scene_id": scene_id,
        "scene_path": str(scene).replace("\\", "/"),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_seconds": round(time.time() - t_start, 2),
        "stages": [s.to_dict() for s in stages],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    icon = {"ok": "OK  ", "fallback": "WARN", "mock": "MOCK", "failed": "FAIL"}
    for s in stages:
        print(f"  [{icon.get(s.status,'?')}] {s.name:<16s} {s.seconds:5.2f}s  {s.detail}")
        for w in s.warnings[:3]:
            print(f"         - {w}")
    real = sum(s.status in ("ok", "fallback") for s in stages)
    print("-" * 66)
    print(f"  {real}/{len(stages)} stages ran for real, "
          f"{sum(s.status == 'mock' for s in stages)} from mocks, "
          f"{sum(s.status == 'failed' for s in stages)} failed "
          f"({manifest['total_seconds']}s)")
    print(f"  -> {out_dir}")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", type=Path, default=MOCKS / "scene_sigma0_db.tif")
    ap.add_argument("--scene-meta", type=Path, default=MOCKS / "scene_meta.json")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--weights", type=Path, default=None)
    ap.add_argument("--engine", choices=["auto", "ml", "threshold_fallback"], default="auto")
    args = ap.parse_args(argv)

    run_id = args.run_id or f"inv-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    run_pipeline(args.scene, args.scene_meta, run_id, args.weights,
                 None if args.engine == "auto" else args.engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
