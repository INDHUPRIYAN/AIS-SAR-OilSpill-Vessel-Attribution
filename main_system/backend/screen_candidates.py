"""Detect-only screening of candidate scenes, before committing to one.

D1's reordering. The flagship needs a scene where three things happen to be
true at once: the footprint overlaps a MarineCadastre-covered box, the date has
an AIS archive, and the deployed segmenter actually finds oil in it. Discovering
the third only after downloading ~600 MB of AIS is the expensive way round.

So this runs detection and nothing else over each candidate, records what the
model genuinely returned, and stops. It is a measurement, not a search for a
scene that agrees with us:

* every candidate's result is written down, including the ones that find
  nothing -- a screening log that lists only the winner is a selection effect,
  not evidence;
* thresholds are never touched. The whole point is to learn what the deployed
  configuration does on unseen data, and a tuned threshold answers a different
  question;
* "no oil found" is a legitimate outcome for a scene and for the whole set. If
  no candidate detects anything, D1's fallback applies -- two honest artefacts
  instead of one overstated one.

    python -m backend.screen_candidates --scene a.tif --scene b.tif
    python -m backend.screen_candidates --dir data/scenes --limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _scene_meta_for(raster: Path) -> Optional[dict]:
    """The scene_meta beside a raster, if the acquisition wrote one."""
    for candidate in (raster.with_suffix(".json"),
                      raster.parent / "scene_meta.json"):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
    return None


def screen_one(raster: Path, out_root: Path) -> Dict[str, Any]:
    """Run detection on one scene. Never raises; a failure is a result."""
    from backend.services.detection.service import detect

    meta = _scene_meta_for(raster)
    scene_id = (meta or {}).get("scene_id") or raster.stem
    out_dir = out_root / scene_id
    out_dir.mkdir(parents=True, exist_ok=True)

    record: Dict[str, Any] = {
        "scene_id": scene_id,
        "raster": str(raster),
        "acquired_utc": (meta or {}).get("acquired_utc"),
        "bbox": (meta or {}).get("bbox"),
        "screened_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    started = time.perf_counter()
    try:
        response = detect(raster, scene_id, out_dir, scene_meta=meta)
    except Exception as exc:                       # noqa: BLE001 - a failure is data
        record.update(ok=False, error_class=type(exc).__name__,
                      error=str(exc)[:300], seconds=round(time.perf_counter() - started, 2))
        return record

    candidates = list(getattr(response, "candidates", []) or [])
    oil = [c for c in candidates if getattr(c, "is_oil", None) is not False]
    record.update({
        "ok": True,
        "seconds": round(time.perf_counter() - started, 2),
        "engine": getattr(response, "engine", None),
        "model_version": getattr(response, "model_version", None),
        "candidates": len(candidates),
        "oil_candidates": len(oil),
        "confidence": getattr(response, "confidence", None),
        # The measurement that decides the flagship. Recorded whichever way it
        # comes out; a screening log that only kept the winner would be a
        # selection effect rather than evidence.
        "detected": bool(oil),
    })
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scene", action="append", default=[],
                    help="raster to screen; repeatable")
    ap.add_argument("--dir", help="screen every .tif under this directory")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--out", default="dev_evidence/P12b",
                    help="where the screening log is written")
    args = ap.parse_args(argv)

    rasters: List[Path] = [Path(s) for s in args.scene]
    if args.dir:
        rasters.extend(sorted(Path(args.dir).rglob("*.tif"))[:args.limit])
    rasters = [r for r in rasters if r.exists()][:args.limit]

    if not rasters:
        print("no candidate rasters found", file=sys.stderr)
        return 2

    out_root = REPO_ROOT / args.out
    out_root.mkdir(parents=True, exist_ok=True)
    scratch = out_root / "_detect_scratch"

    results = []
    print(f"screening {len(rasters)} candidate scene(s), detection only\n")
    for raster in rasters:
        result = screen_one(raster, scratch)
        results.append(result)
        if result["ok"]:
            print(f"  {'OIL ' if result['detected'] else '--- '}"
                  f"{result['scene_id'][:52]:<54}"
                  f"{result['oil_candidates']:>3} oil / {result['candidates']:>3} cand"
                  f"  conf {result.get('confidence')}"
                  f"  {result['seconds']:>6.1f}s")
        else:
            print(f"  ERR  {result['scene_id'][:52]:<54}{result['error_class']}")

    detected = [r for r in results if r.get("detected")]
    log = {
        "screened_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "candidates_screened": len(results),
        "with_oil": len(detected),
        "results": results,
        "note": "Detection only. No threshold, gate or weight was changed. "
                "Scenes that found nothing are listed: a log of only the "
                "winner would be a selection effect, not evidence.",
    }
    (out_root / "screening_log.json").write_text(
        json.dumps(log, indent=2, default=str), encoding="utf-8")

    print(f"\n{len(detected)}/{len(results)} candidate(s) detected oil")
    if not detected:
        # Not an error exit: this is a real answer about real data, and D1
        # already says what to do with it.
        print("no candidate detected oil -- D1's fallback applies "
              "(two honest artefacts rather than one overstated one)")
    print(f"log: {out_root / 'screening_log.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
