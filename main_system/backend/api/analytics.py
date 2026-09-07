"""Metrics, vessel tracks and replay support.

The metrics endpoint deliberately reports only what was measured. Where a
number does not exist yet it is returned as null with a reason, rather than
omitted or filled with a plausible-looking default -- a metrics page that
quietly invents a figure is worse than one with a gap in it, because the gap
is at least honest.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import get_settings

router = APIRouter()
settings = get_settings()
REPO_ROOT = settings.data_root.parent

TRAIN_RUNS = settings.data_root / "runs" / "training"
BENCHMARK = REPO_ROOT / "analysis_engines" / "benchmark" / "results.json"
SENSITIVITY = REPO_ROOT / "analysis_engines" / "benchmark" / "sensitivity.json"

# The metrics page must describe the checkpoint that actually ships, so the
# weights directory the detection service loads from is the only identity
# source here. `data/runs/training/metrics.json` is a training-time scratch
# file that still holds the superseded POC epoch, and reading it is what made
# this endpoint advertise a model nobody deploys (audit M-05).
WEIGHTS_DIR = REPO_ROOT / "main_system" / "backend" / "services" / "detection" / "weights"
SEGMENT_ONNX = WEIGHTS_DIR / "segment.onnx"
SCREEN_ONNX = WEIGHTS_DIR / "screen.onnx"
EVAL_DIR = REPO_ROOT / "docs" / "eval"


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


@lru_cache(maxsize=4)
def _deployed_checkpoint(weights: str) -> Optional[Dict[str, Any]]:
    """Identity of the ONNX file the detection service actually loads.

    The exported weights carry their own provenance in ONNX metadata_props
    (``model_version``, ``checkpoint_epoch``, ``config_fingerprint``), so the
    shipped artefact is its own source of truth -- there is no side-file to
    drift out of sync with it. Cached because the bytes cannot change while
    the process is alive, and the sha256 reads ~98 MB.
    """
    path = Path(weights)
    if not path.exists():
        return None
    raw = path.read_bytes()
    info: Dict[str, Any] = {
        "file": _relative(str(path)),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "name": None,
        "epoch": None,
        "config_fingerprint": None,
    }
    try:
        import onnx

        props = {kv.key: kv.value
                 for kv in onnx.load(str(path), load_external_data=False).metadata_props}
        info["name"] = props.get("model_version")
        info["config_fingerprint"] = props.get("config_fingerprint")
        epoch = props.get("checkpoint_epoch")
        info["epoch"] = int(epoch) if epoch and str(epoch).isdigit() else None
    except Exception:
        # A hash with no identity is still more than the stale file gave us.
        pass
    return info


def _deployed_segmentation_eval(name: Optional[str]) -> Optional[dict]:
    """The held-out evaluation belonging to the deployed checkpoint.

    Keyed by the checkpoint's own ``model_version`` so a re-export cannot
    silently keep quoting the previous model's numbers. Returns None rather
    than falling back to any other file: an absent evaluation is reported as
    a gap (see the module docstring), never papered over.
    """
    if not name:
        return None
    return _read(EVAL_DIR / f"{name}_holdout.json")


# --------------------------------------------------------------------------
# vessel tracks
# --------------------------------------------------------------------------


# Deliberately NOT under /layers/{run_id}/{layer}: that route is a catch-all
# registered first, so it would swallow this path and 404 on an unknown
# layer name before this handler was ever reached.
def _null_if_nan(value, digits: int):
    """Round a float, or return None when it is not a number.

    Absent beats fabricated: a missing AIS field must serialise as null, never
    as 0. Real archives are full of these -- heading is optional in the AIS
    standard and roughly a third of the flagship's rows omit it.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return round(number, digits)


@router.get("/runs/{run_id}/vessels_geojson")
def vessels_geojson(run_id: str, max_vessels: int = Query(200, le=2000),
                    bbox: Optional[str] = Query(
                        None, description="lon_min,lat_min,lon_max,lat_max -- "
                                          "keep only tracks touching this box"),
                    zoom: Optional[int] = Query(
                        None, ge=0, le=22,
                        description="thin track POINTS at low zoom; vessels are "
                                    "never dropped by this")):
    """AIS tracks as GeoJSON LineStrings, one per MMSI.

    The contract stores vessels as parquet, which a browser cannot read. This
    converts on the way out rather than changing the contract -- parquet is the
    right format for the attribution engine, GeoJSON is the right format for a
    map, and neither should have to compromise for the other.

    **Decimation rule, and what it must never do.** `zoom` thins the number of
    POINTS along each track, because at zoom 4 a 300-point line and a 20-point
    line are the same three pixels. It never removes a vessel. A ranked suspect
    that vanished when the analyst zoomed out would be a map that disagrees
    with the ranking beside it, and the first casualty of viewport culling is
    always the thing you were looking for. `bbox` culls whole tracks, but a
    suspect whose track touches the box is kept in full.
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

    box = None
    if bbox:
        try:
            parts = [float(v) for v in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            box = (min(parts[0], parts[2]), min(parts[1], parts[3]),
                   max(parts[0], parts[2]), max(parts[1], parts[3]))
        except ValueError:
            raise HTTPException(422, "bbox must be lon_min,lat_min,lon_max,lat_max")

    # Points kept per track at a given zoom. Below zoom 6 a whole track is a
    # few pixels wide, so the shape is carried by a handful of vertices; by
    # zoom 11 the full track is worth drawing.
    stride_for = {0: 24, 4: 12, 6: 6, 8: 3, 10: 2}
    stride = 1
    if zoom is not None:
        for threshold, value in sorted(stride_for.items()):
            if zoom >= threshold:
                stride = value
        stride = 1 if zoom >= 11 else stride

    features: List[dict] = []
    kept, culled = 0, 0
    for mmsi, grp in list(df.groupby("mmsi"))[:max_vessels]:
        coords = [[float(r.lon), float(r.lat)] for r in grp.itertuples()]
        if len(coords) < 2:
            continue

        m_int = int(mmsi)
        is_ranked = m_int in suspects
        if box is not None and not is_ranked:
            # A ranked suspect is never culled by the viewport: the map must
            # not disagree with the ranking printed beside it.
            touches = any(box[0] <= c[0] <= box[2] and box[1] <= c[1] <= box[3]
                          for c in coords)
            if not touches:
                culled += 1
                continue
        kept += 1
        m = int(mmsi)
        s = suspects.get(m)

        # Per-point context for exact replay: epoch times so the UI can place
        # the vessel at any instant, and speed/heading straight from AIS.
        tvals = _pd.to_datetime(grp[tcol], utc=True, errors="coerce")
        times = [int(x.timestamp()) if _pd.notna(x) else None for x in tvals]
        # NaN means the vessel did not transmit that field -- AIS sends 511 for
        # "heading unavailable" and the ingest turns it into NaN. It reaches
        # JSON as null, which is the true statement; `float('nan')` is not
        # valid JSON and made this endpoint fail outright on real AIS (29,679
        # of the flagship's 86,830 rows carry no heading). A zero would have
        # been worse than the crash: it reads as "pointing due north".
        sog = ([_null_if_nan(x, 2) for x in grp["sog_kn"]]
               if "sog_kn" in grp else [])
        hdg_col = next((c for c in ("heading_deg", "cog_deg") if c in grp), None)
        headings = ([_null_if_nan(x, 1) for x in grp[hdg_col]] if hdg_col else [])

        # Distance travelled: haversine summed along the real point sequence.
        dist_km = sum(_haversine_km(coords[i][1], coords[i][0],
                                    coords[i + 1][1], coords[i + 1][0])
                      for i in range(len(coords) - 1))
        good_t = [x for x in times if x is not None]
        dur_h = (good_t[-1] - good_t[0]) / 3600.0 if len(good_t) >= 2 else None

        # Thin the drawn geometry only. `distance_km` and `dur_h` above were
        # computed from every point, so a decimated track still reports the
        # distance the vessel actually travelled rather than the length of the
        # simplified line -- the number and the picture must not disagree.
        drawn = coords
        drawn_times = times
        if stride > 1 and len(coords) > 2 * stride:
            drawn = coords[::stride]
            drawn_times = times[::stride]
            # Always keep the last fix: dropping it moves the end of a track,
            # which is the point an analyst is usually looking at.
            if drawn[-1] != coords[-1]:
                drawn.append(coords[-1])
                drawn_times.append(times[-1])

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": drawn},
            "properties": {
                "mmsi": m,
                "times_epoch": drawn_times,
                # Stated so a client can tell a thinned line from a short
                # track. Silence here would make a decimated map look like a
                # vessel that only transmitted twice.
                "points_drawn": len(drawn),
                "points_total": len(coords),
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
                         "filtered": sum(1 for f in features if f["properties"]["filtered"]),
                         # What decimation did, so a thinned map is never
                         # mistaken for a sparse one.
                         "viewport": {"bbox": box, "zoom": zoom,
                                      "point_stride": stride,
                                      "tracks_culled_by_bbox": culled,
                                      "ranked_never_culled": True}},
            "features": features}


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


@router.get("/metrics")
def metrics():
    """Everything the Analytics page renders, measured only."""
    seg_ckpt = _deployed_checkpoint(str(SEGMENT_ONNX))
    screen_ckpt = _deployed_checkpoint(str(SCREEN_ONNX))
    seg = _deployed_segmentation_eval((seg_ckpt or {}).get("name"))
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
            # The learned residual was trained and then evaluated *negative*
            # (held-out: better on 0 of 6 trajectories), so it is disabled in
            # the shipped configuration. The hindcast is physics, and this
            # block is what stops the UI implying otherwise.
            "status": "experimental",
            "applied": False,
            "evaluated": "negative",
            "experiment": "drift-residual-mlp-20260906",
            "note": "Drift output is a probability cloud with an uncertainty "
                    "ellipse. No accuracy is claimed: no ground-truth drift "
                    "dataset exists for these scenes.",
            "ml_note": "A learned drift residual exists but is EXPERIMENTAL and "
                       "disabled: held-out evaluation improved 0 of 6 trajectories, "
                       "so the deployed hindcast is physics only (Engine B).",
        },
        "notes": [],
    }

    if seg_ckpt and not seg:
        out["notes"].append(
            f"No held-out evaluation found for the deployed checkpoint "
            f"'{seg_ckpt.get('name')}' at docs/eval/{seg_ckpt.get('name')}_holdout.json. "
            f"Segmentation metrics are reported as absent rather than substituted "
            f"from another checkpoint.")

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
            # The checkpoint that ships, read from the ONNX file the detection
            # service loads -- not from any training-time scratch file.
            "checkpoint": seg_ckpt,
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
            # Both scopes are published side by side on purpose. The oil-tile
            # figures cover only the 512 tiles that contain oil; the overall
            # figures include the 5,248 background tiles where the model can
            # fire falsely. Quoting the oil-tile IoU alone reads ~29% better
            # than the model is, so the UI must show the pair.
            "oil_tile_iou": oil.get("iou"),
            "oil_tile_precision": oil.get("precision"),
            "oil_tile_recall": oil.get("recall"),
            "oil_tile_f1": oil.get("f1"),
            "overall_iou": overall.get("iou"),
            "overall_precision": overall.get("precision"),
            "overall_recall": overall.get("recall"),
            "overall_f1": overall.get("f1"),
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
            "checkpoint": screen_ckpt,
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
