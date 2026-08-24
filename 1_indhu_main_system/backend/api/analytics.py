"""Metrics, vessel tracks and replay support.

The metrics endpoint deliberately reports only what was measured. Where a
number does not exist yet it is returned as null with a reason, rather than
omitted or filled with a plausible-looking default -- a metrics page that
quietly invents a figure is worse than one with a gap in it, because the gap
is at least honest.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import get_settings

router = APIRouter()
settings = get_settings()
REPO_ROOT = settings.data_root.parent

TRAIN_RUNS = settings.data_root / "runs" / "training"
BENCHMARK = REPO_ROOT / "2_nandha_engines" / "benchmark" / "results.json"
SENSITIVITY = REPO_ROOT / "2_nandha_engines" / "benchmark" / "sensitivity.json"


def _relative(path: Optional[str]) -> Optional[str]:
    """Repo-relative form of a path, so responses carry no absolute local paths."""
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return Path(path).name


EARTH_R_KM = 6371.0088


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Geodesic distance between two WGS84 points.

    Vessel distances shown in the UI are computed here, point by point along
    the actual AIS track -- never estimated from pixels on a map.
    """
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(a))


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# vessel tracks
# --------------------------------------------------------------------------


# Deliberately NOT under /layers/{run_id}/{layer}: that route is a catch-all
# registered first, so it would swallow this path and 404 on an unknown
# layer name before this handler was ever reached.
@router.get("/runs/{run_id}/vessels_geojson")
def vessels_geojson(run_id: str, max_vessels: int = Query(200, le=2000)):
    """AIS tracks as GeoJSON LineStrings, one per MMSI.

    The contract stores vessels as parquet, which a browser cannot read. This
    converts on the way out rather than changing the contract -- parquet is the
    right format for the attribution engine, GeoJSON is the right format for a
    map, and neither should have to compromise for the other.
    """
    root = settings.runs_root.resolve()
    run_dir = (root / run_id).resolve()
    if not str(run_dir).startswith(str(root)):
        raise HTTPException(400, "invalid run id")

    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(500, "pandas unavailable")

    suspects: Dict[int, dict] = {}
    filtered: Dict[int, Optional[str]] = {}
    sus_file = run_dir / "suspects.json"
    if sus_file.exists():
        payload = _read(sus_file) or {}
        for s in payload.get("suspects", []):
            suspects[int(s["mmsi"])] = s
        filtered = {int(f["mmsi"]): f.get("reason")
                    for f in payload.get("filtered_out", [])}

    candidates = [run_dir / "vessels.parquet",
                  run_dir / "engine_native" / "vessels_generated.parquet",
                  run_dir / "engine_native" / "vessels.parquet"]
    candidates = [p for p in candidates if p.exists()]
    if not candidates:
        raise HTTPException(404, "no vessels.parquet in this run")

    # Pick the file attribution actually ran on, proven by MMSI coverage rather
    # than assumed by filename order. Older runs on disk carry more than one
    # vessel file and the wrong one renders a map where no suspect has a track.
    wanted = set(suspects) | set(filtered)
    src, df = None, None
    best = -1.0
    for cand in candidates:
        try:
            frame = pd.read_parquet(cand)
        except Exception:
            continue
        if not wanted:
            src, df = cand, frame
            break
        cover = len(wanted & {int(m) for m in frame["mmsi"].unique()}) / len(wanted)
        if cover > best:
            src, df, best = cand, frame, cover
        if cover == 1.0:
            break
    if df is None:
        raise HTTPException(404, "no readable vessels.parquet in this run")
    tcol = "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"
    df = df.sort_values(["mmsi", tcol])

    import pandas as _pd

    features: List[dict] = []
    for mmsi, grp in list(df.groupby("mmsi"))[:max_vessels]:
        coords = [[float(r.lon), float(r.lat)] for r in grp.itertuples()]
        if len(coords) < 2:
            continue
        m = int(mmsi)
        s = suspects.get(m)

        # Per-point context for exact replay: epoch times so the UI can place
        # the vessel at any instant, and speed/heading straight from AIS.
        tvals = _pd.to_datetime(grp[tcol], utc=True, errors="coerce")
        times = [int(x.timestamp()) if _pd.notna(x) else None for x in tvals]
        sog = ([round(float(x), 2) for x in grp["sog_kn"]]
               if "sog_kn" in grp else [])
        hdg_col = next((c for c in ("heading_deg", "cog_deg") if c in grp), None)
        headings = ([round(float(x), 1) for x in grp[hdg_col]] if hdg_col else [])

        # Distance travelled: haversine summed along the real point sequence.
        dist_km = sum(_haversine_km(coords[i][1], coords[i][0],
                                    coords[i + 1][1], coords[i + 1][0])
                      for i in range(len(coords) - 1))
        good_t = [x for x in times if x is not None]
        dur_h = (good_t[-1] - good_t[0]) / 3600.0 if len(good_t) >= 2 else None

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "mmsi": m,
                "times_epoch": times,
                "sog_kn": sog,
                "headings_deg": headings,
                "distance_km": round(dist_km, 2),
                "duration_h": round(dur_h, 2) if dur_h else None,
                "avg_speed_kn": round(float(_pd.Series(sog).mean()), 2) if sog else None,
                "max_speed_kn": round(float(max(sog)), 2) if sog else None,
                "rank": s.get("rank") if s else None,
                "total_score": s.get("total_score") if s else None,
                "vessel_name": (s or {}).get("vessel_name")
                               or (grp["vessel_name"].iloc[0] if "vessel_name" in grp else None),
                "vessel_type": (s or {}).get("vessel_type")
                               or (grp["vessel_type"].iloc[0] if "vessel_type" in grp else None),
                # A filtered vessel is evidence too -- the UI dims it and shows
                # WHY it was excluded, which is what makes the gating auditable.
                "filtered": m in filtered,
                "filter_reason": filtered.get(m),
                "source": str(grp["source"].iloc[0]) if "source" in grp else "unknown",
                "points": len(coords),
                "start_utc": str(grp[tcol].iloc[0]),
                "end_utc": str(grp[tcol].iloc[-1]),
            },
        })

    return {"type": "FeatureCollection",
            "metadata": {"run_id": run_id, "vessels": len(features),
                         "source_file": src.name,
                         "suspect_coverage": None if not wanted else round(best, 3),
                         "ranked": sum(1 for f in features if f["properties"]["rank"]),
                         "filtered": sum(1 for f in features if f["properties"]["filtered"])},
            "features": features}


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


@router.get("/metrics")
def metrics():
    """Everything the Analytics page renders, measured only."""
    seg = _read(TRAIN_RUNS / "metrics.json")
    screen = _read(TRAIN_RUNS / "screen_metrics.json")
    bench = _read(BENCHMARK)
    sens = _read(SENSITIVITY)

    out: Dict[str, Any] = {
        "segmentation": None,
        "screening": None,
        "attribution": None,
        "drift": {
            # There is no ground-truth drift dataset, so no accuracy figure can
            # honestly be quoted. Saying so is the point.
            "accuracy_reported": False,
            "note": "Drift output is a probability cloud with an uncertainty "
                    "ellipse. No accuracy is claimed: no ground-truth drift "
                    "dataset exists for these scenes.",
        },
        "notes": [],
    }

    if seg:
        # The split's own index.json is the authority on how the test set was
        # built. metrics.json records the numbers; only the split records
        # whether the split is trustworthy, and that caveat has to reach the UI.
        split_meta: Dict[str, Any] = {}
        split_path = seg.get("test_split")
        if split_path:
            split_meta = ((_read(Path(split_path) / "index.json") or {}).get("meta") or {})

        half = (seg.get("results") or {}).get("0.5", {})
        overall = half.get("overall", {})
        per_kind = half.get("per_kind", {})
        oil = per_kind.get("oil", {})
        out["segmentation"] = {
            "model": "U-Net · ResNet-34 encoder",
            # Relative, so the API never leaks an absolute local path.
            "test_split": _relative(seg.get("test_split")),
            "dataset": split_meta.get("dataset"),
            "part": split_meta.get("part"),
            "scenes": split_meta.get("scenes"),
            "oil_tiles": split_meta.get("oil_tiles"),
            "negative_tiles": split_meta.get("negative_tiles"),
            "poc_holdout": bool(split_meta.get("poc_holdout")),
            "config_fingerprint": seg.get("config_fingerprint"),
            "checkpoint_epoch": seg.get("checkpoint_epoch"),
            "test_tiles": seg.get("test_tiles"),
            "db_range": seg.get("db_range"),
            "threshold": 0.5,
            "oil_tile_iou": oil.get("iou"),
            "oil_tile_precision": oil.get("precision"),
            "oil_tile_recall": oil.get("recall"),
            "overall_iou": overall.get("iou"),
            "overall_precision": overall.get("precision"),
            "overall_recall": overall.get("recall"),
            "no_oil_tiles": half.get("no_oil_tiles"),
            "no_oil_firing": half.get("no_oil_tiles_with_false_detection"),
            "no_oil_firing_rate": half.get("scene_level_false_positive_rate"),
            "sweep": [
                {"threshold": float(t),
                 "overall_iou": (v.get("overall") or {}).get("iou"),
                 "oil_iou": ((v.get("per_kind") or {}).get("oil") or {}).get("iou"),
                 "no_oil_firing_rate": v.get("scene_level_false_positive_rate")}
                for t, v in sorted((seg.get("results") or {}).items(), key=lambda x: float(x[0]))
            ],
            "pixel_accuracy_note":
                "Pixel accuracy is deliberately not reported: sea dominates every "
                "SAR tile, so an all-sea prediction scores above 99% and means nothing.",
        }
        warning = split_meta.get("WARNING")
        if split_meta.get("poc_holdout"):
            # The split's own WARNING already says why; repeating it here would
            # only make the banner longer, not more honest.
            out["notes"].append(
                str(warning) if warning else
                "Segmentation metrics come from a POC holdout carved out of "
                "Trujillo Part III itself, not an untouched test split. "
                "Re-measure before quoting these as test results."
            )
        elif warning:
            out["notes"].append(str(warning))

    if screen:
        out["screening"] = {
            "model": "YOLO11n · 1 class (oil)",
            "map50": screen.get("map50"),
            "map50_95": screen.get("map50_95"),
            "precision": screen.get("precision"),
            "recall": screen.get("recall"),
            "background_images": screen.get("background_images"),
            "background_false_positives": screen.get("background_false_positives"),
            "background_fp_rate": screen.get("background_fp_rate"),
            "conf_threshold": screen.get("conf_threshold"),
            "note": "Look-alike patches carry no annotations in DARTIS, so they are "
                    "background negatives rather than a second class. The number "
                    "that matters is how often the model fires on one.",
        }

    if bench and isinstance(bench.get("summary"), dict):
        s = bench["summary"]
        out["attribution"] = {
            "scenarios": s.get("scenarios"),
            "top1": s.get("top1"),
            "top3": s.get("top3"),
            "top1_rate": s.get("top1_rate"),
            "top3_rate": s.get("top3_rate"),
            "mean_rank_when_placed": s.get("mean_rank_when_placed"),
            "culprit_filtered_by_gates": s.get("culprit_filtered_by_gates"),
            "engine_errors": s.get("engine_errors"),
            "by_tier": s.get("by_tier"),
            "by_behaviour": s.get("by_behaviour"),
            "note": "Synthetic benchmark with a planted culprit. No real-world "
                    "attribution ground truth exists, which is precisely why this "
                    "harness was built.",
        }

    if sens:
        out["weight_sensitivity"] = sens

    return out


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------


@router.get("/replay/runs")
def replay_runs():
    """Completed runs that can be replayed with no network at all.

    A run qualifies when every contract file it produced is on disk. This is
    the demo's last line of defence: if every provider is down and the GPU is
    missing, the investigation still renders from these files.
    """
    root = settings.runs_root
    if not root.exists():
        return {"runs": []}

    required = ["manifest.json", "slick.geojson", "origin_cloud.geojson", "suspects.json"]
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        present = [f for f in required if (d / f).exists()]
        if len(present) < len(required):
            continue
        manifest = _read(d / "manifest.json") or {}
        stages = manifest.get("stages", [])
        out.append({
            "run_id": d.name,
            "scene_id": manifest.get("scene_id"),
            "generated_utc": manifest.get("generated_utc"),
            "seconds": manifest.get("total_seconds"),
            "stages_real": sum(s.get("status") in ("ok", "fallback") for s in stages),
            "stages_total": len(stages),
            "files": sorted(p.name for p in d.glob("*") if p.is_file()),
            "replayable": True,
        })
    return {"runs": out, "count": len(out)}
