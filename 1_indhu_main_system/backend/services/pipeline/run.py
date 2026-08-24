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
from backend.services.pipeline import engines, normalise  # noqa: E402

MOCKS = REPO_ROOT / "contracts" / "mocks"
RUNS = REPO_ROOT / "data" / "runs"
METOCEAN_CACHE = REPO_ROOT / "4_keerthana_metocean_service" / "data" / "metocean"


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
    oil = [c for c in resp.candidates if c.class_.value == "oil"]
    look = [c for c in resp.candidates if c.class_.value == "lookalike"]
    stage.detail = (f"engine={resp.engine.value}, {len(oil)} oil + {len(look)} "
                    f"look-alike candidate(s), confidence {resp.confidence}")
    warn_file = out_dir / "detect_warnings.json"
    if warn_file.exists():
        stage.warnings = json.loads(warn_file.read_text())
    # Characterisation and drift consume raw_mask.tif, which still contains the
    # rejected regions. Say so rather than letting a fully-rejected scene flow
    # through to an origin cloud that looks like a real spill.
    if look and not oil:
        stage.warnings.append(
            "every candidate was rejected as a look-alike by the screening "
            "model; downstream drift still runs on the full mask, so treat "
            "this origin and its suspects as unconfirmed")
    return resp.model_dump(by_alias=True)


def stage_characterise(stage: Stage, scene: Path, out_dir: Path,
                       detect_result: dict, meta: Optional[dict]) -> bool:
    """Engine A for real; the stand-in only if the real engine cannot run."""
    scene_meta_path = out_dir / "scene_meta.json"
    if meta and not scene_meta_path.exists():
        scene_meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if scene_meta_path.exists():
        native = engine_dir(out_dir) / stage.output
        res = engines.characterise(
            mask=Path(detect_result["mask_path"]).resolve(),
            scene_meta=scene_meta_path.resolve(),
            out=native.resolve(),
            scene_db=Path(scene).resolve(),
            confidence=detect_result.get("confidence"))
        stage.seconds = res.seconds
        if res.ok:
            shutil.copy(native, out_dir / stage.output)
            normalise.normalise_file("slick", out_dir / stage.output,
                                     scene_meta=meta or {}, detect=detect_result)
            stage.status, stage.source = "ok", "real"
            stage.warnings = res.warnings
            n = len(json.loads((out_dir / stage.output).read_text())["features"])
            stage.detail = f"Engine A: {n} slick(s)"
            return True
        stage.warnings.append(f"Engine A failed ({res.error_class}): {res.detail}")

    return _characterise_standin(stage, scene, out_dir, detect_result, meta)


def _characterise_standin(stage: Stage, scene: Path, out_dir: Path,
                          detect_result: dict, meta: Optional[dict]) -> bool:
    """Local geometry fallback, used only when Engine A is unavailable."""
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
    stage.detail = (f"Engine A unavailable, used stand-in; "
                    f"{len(payload['features'])} slick(s), largest "
                    f"{payload['features'][0]['properties']['area_km2']} km2")
    return True


def stage_mocked(stage: Stage, out_dir: Path, reason: str) -> bool:
    return serve_mock(stage, out_dir, reason)


def engine_dir(out_dir: Path) -> Path:
    """Where engines read and write their native-shape files.

    The engines consume each other's output (B reads A's slick, C reads B's
    origin cloud), so their files must stay in the shape they expect. The
    contract-normalised copies live in the run root for the UI and for schema
    validation. Normalising in place broke Engine B the moment Engine A's
    output was converted -- hence two directories rather than one.
    """
    d = out_dir / "engine_native"
    d.mkdir(parents=True, exist_ok=True)
    return d


def grid_covers_bbox(path: Path, bbox: Optional[list]) -> Optional[bool]:
    """Does this forcing grid span the scene's footprint?

    Returns None when it cannot be determined (unreadable file, no bbox), which
    callers treat as "do not rule it out" rather than as a failure.

    Ranking forcing by time alone is not enough. A cache holding one Chennai
    grid will happily hand it to a Mediterranean scene: every candidate scores
    equally badly on time, the first one wins, and the drift engine then
    rejects it with BAD_GRID -- or worse, would drift the slick through the
    wrong ocean's currents.
    """
    if not bbox or len(bbox) != 4:
        return None
    try:
        import xarray as xr

        with xr.open_dataset(path) as ds:
            lon_name = next((n for n in ("lon", "longitude", "x") if n in ds.coords), None)
            lat_name = next((n for n in ("lat", "latitude", "y") if n in ds.coords), None)
            if not lon_name or not lat_name:
                return None
            lon = ds[lon_name].values
            lat = ds[lat_name].values
    except Exception:
        return None

    import numpy as _np

    lo0, la0, lo1, la1 = [float(v) for v in bbox]
    lon_min, lon_max = float(_np.nanmin(lon)), float(_np.nanmax(lon))
    lat_min, lat_max = float(_np.nanmin(lat)), float(_np.nanmax(lat))
    # Grids are commonly stored on 0..360; compare in the scene's convention.
    if lon_min >= 0.0 and lon_max > 180.0 and lo0 < 0.0:
        lon_min, lon_max = lon_min - 360.0, lon_max - 360.0
    return (lon_min <= lo0 and lon_max >= lo1
            and lat_min <= la0 and lat_max >= la1)


def resolve_metocean(meta: Optional[dict], out_dir: Path):
    """Locate currents.nc / wind.nc for this scene, or return (None, None).

    Search order is deliberate: anything already staged into the run directory
    wins, then Keerthana's per-scene cache, then her sample fixtures. Returning
    None is a legitimate answer -- the drift engine degrades to wind-only or
    zero-current mode, and the manifest records that it did rather than
    implying real forcing was used.
    """
    scene_id = (meta or {}).get("scene_id", "")
    acquired = (meta or {}).get("acquired_utc")
    bbox = (meta or {}).get("bbox")

    # Scene-specific directories first. Globbing the whole cache and taking the
    # first hit silently picked a DIFFERENT scene's forcing -- data for
    # 20170131 was used to drift a 20170202 scene, which is wrong in both time
    # and place and produced a forecast with negative forward coverage.
    candidates = [
        out_dir,
        REPO_ROOT / "data" / "metocean" / scene_id if scene_id else None,
        METOCEAN_CACHE / scene_id if scene_id else None,
        REPO_ROOT / "data" / "metocean",
        METOCEAN_CACHE,
        REPO_ROOT / "2_nandha_engines" / "samples" / "inputs",
    ]

    def best(pattern: str) -> Optional[Path]:
        """Prefer a grid that actually brackets the acquisition time."""
        found: List[Path] = []
        for d in [c for c in candidates if c and Path(c).is_dir()]:
            found.extend(sorted(Path(d).glob(f"**/{pattern}")))
            if found and Path(d).name == scene_id:
                break          # an exact scene match wins outright
        if not found:
            return None

        # Location first. A grid over the wrong sea is not a degraded input,
        # it is a wrong one, so it is discarded rather than ranked lower.
        # Grids we cannot read a footprint from are kept: unknown is not "no".
        elsewhere = [f for f in found if grid_covers_bbox(f, bbox) is False]
        found = [f for f in found if f not in elsewhere]
        if not found:
            return None

        if not acquired:
            return found[0].resolve()
        scored = []
        for f in found:
            before, after = forcing_coverage_hours([f], acquired)
            if before is None:
                continue
            # Rank by how much usable window the grid gives on both sides.
            scored.append((min(before, 24) + min(after, 24), before, after, f))
        if not scored:
            return found[0].resolve()
        scored.sort(reverse=True)
        return scored[0][3].resolve()

    return best("currents*.nc"), best("wind*.nc")


def forcing_coverage_hours(paths, acquired_utc: Optional[str]):
    """(hours_before, hours_after) that the forcing grids cover around the scene.

    A drift run that walks off the end of its forcing grid fails with BAD_GRID
    rather than degrading, so the horizon is clamped to what the data actually
    supports. Returning (None, None) means "unknown" and the caller should not
    clamp.
    """
    if not acquired_utc:
        return None, None
    try:
        import xarray as xr

        acquired = datetime.fromisoformat(acquired_utc.replace("Z", "+00:00"))
        first, last = None, None
        for p in [x for x in paths if x]:
            with xr.open_dataset(str(p)) as ds:
                if "time" not in ds.coords and "time" not in ds.dims:
                    continue
                times = ds["time"].values
                t0 = datetime.fromisoformat(str(times[0])[:19]).replace(tzinfo=timezone.utc)
                t1 = datetime.fromisoformat(str(times[-1])[:19]).replace(tzinfo=timezone.utc)
                # Intersection across grids: drift needs BOTH to cover the span.
                first = t0 if first is None else max(first, t0)
                last = t1 if last is None else min(last, t1)
        if first is None:
            return None, None
        return ((acquired - first).total_seconds() / 3600.0,
                (last - acquired).total_seconds() / 3600.0)
    except Exception:
        return None, None


def origin_summary(origin_native: Path):
    """(lat, lon, window_start, window_end) from the engine's origin cloud."""
    try:
        payload = json.loads(Path(origin_native).read_text(encoding="utf-8"))
    except Exception:
        return None
    window, pts = None, []
    pts_by_step: dict = {}
    for f in payload.get("features", []):
        props = f.get("properties", {})
        kind = props.get("kind") or props.get("feature_type")
        if kind == "origin_window":
            window = {**props, "coordinates": f["geometry"].get("coordinates")}
        elif kind not in ("confidence_ellipse", "ellipse"):
            c = f["geometry"].get("coordinates")
            if isinstance(c, list) and len(c) >= 2:
                pts.append((float(c[0]), float(c[1])))
                # The engine's native schema writes hours-into-the-past as
                # `timestep_h`; the published contract writes `step_index`.
                # This reads the native file, so accept both.
                step = props.get("step_index", props.get("timestep_h"))
                if step is not None:
                    pts_by_step.setdefault(int(round(float(step))), []).append(
                        (float(c[0]), float(c[1])))
    # Plant point = the particle cloud at the MIDDLE of the origin window,
    # not the mean over the whole backtrack. The whole-cloud mean sits far up
    # the drift corridor where the density is thin; a culprit planted there
    # scores lower proximity than random traffic crossing the dense centre,
    # and attribution then correctly ranks the wrong vessel first.
    mid_pts = []
    if pts_by_step:
        steps = sorted(pts_by_step)
        # The step matching the MIDDLE OF THE ORIGIN WINDOW, not the middle of
        # the whole backtrack: the window can be far shorter than the 24 h
        # backtrack (Chennai: 7 h window, 24 h cloud), and a culprit planted at
        # the backtrack midpoint sails through water the cloud only reaches
        # hours outside the window -- the gates then correctly reject it.
        mid_step = steps[len(steps) // 2]
        w = window or {}
        try:
            from datetime import datetime

            ws = datetime.fromisoformat(str(w.get("start_utc")).replace("Z", "+00:00"))
            we = datetime.fromisoformat(str(w.get("end_utc")).replace("Z", "+00:00"))
            back_h = (we - ws).total_seconds() / 7200.0   # half-window, hours
            mid_step = min(max(steps), max(min(steps), round(back_h)))
        except Exception:
            pass
        mid_pts = pts_by_step.get(mid_step, [])
    use = mid_pts or pts
    if window and window.get("coordinates") and not use:
        lon, lat = window["coordinates"][0], window["coordinates"][1]
    elif use:
        lon = sum(p[0] for p in use) / len(use)
        lat = sum(p[1] for p in use) / len(use)
    else:
        return None
    w = window or {}
    return {"lat": lat, "lon": lon,
            "window_start_utc": w.get("start_utc"),
            "window_end_utc": w.get("end_utc"),
            "peak_utc": w.get("peak_utc")}


def vessels_cover_origin(vessels: Path, summary: dict,
                         radius_deg: float = 0.35) -> bool:
    """Does this AIS file plausibly contain the vessel that caused this spill?

    Requires at least one report inside a box around the computed origin AND
    inside the origin time window. Both must hold: a vessel in the right place
    a day later is not a suspect, and neither is one in the right hour a
    hundred kilometres away.
    """
    try:
        import pandas as pd

        df = pd.read_parquet(vessels)
        tcol = "timestamp_utc" if "timestamp_utc" in df.columns else "timestamp"
        near = df[(df["lat"].sub(summary["lat"]).abs() < radius_deg)
                  & (df["lon"].sub(summary["lon"]).abs() < radius_deg)]
        if near.empty:
            return False
        start = pd.Timestamp(summary["window_start_utc"])
        end = pd.Timestamp(summary["window_end_utc"])
        ts = pd.to_datetime(near[tcol], utc=True)
        if start.tzinfo is None:
            start, end = start.tz_localize("UTC"), end.tz_localize("UTC")
        return bool(((ts >= start) & (ts <= end)).any())
    except Exception:
        return False


def ensure_vessels(out_dir: Path, origin_native: Path, meta: Optional[dict],
                   stage: "Stage") -> Optional[Path]:
    """Real AIS if we have it for this scene; otherwise synthesise it here.

    Real AIS does not exist for Indian waters, which is why the synthetic
    generator is mandatory infrastructure rather than a fallback. The important
    part is that the culprit is planted at the origin THIS run computed -- a
    pre-generated file describes a different event, and attribution then
    correctly reports that no vessel went anywhere near the cloud.
    """
    existing = resolve_vessels(out_dir)
    summary = origin_summary(origin_native)
    if summary is None or not summary.get("window_start_utc"):
        return existing

    # Judge the file on whether it actually covers this origin, not on where it
    # came from. A pre-generated parquet is real data about a DIFFERENT event;
    # its vessels never enter the cloud this run computed, and attribution then
    # returns NO_VESSELS_IN_WINDOW, which reads like a bug but is correct.
    if existing is not None and vessels_cover_origin(existing, summary):
        return existing
    if existing is not None:
        stage.warnings.append(
            f"{Path(existing).name} does not cover the computed origin window; "
            f"generating AIS for this origin instead")

    # The culprit's course must run along the slick's own axis -- that is what
    # the trajectory gate measures. With kinematically honest cog values a
    # culprit sailing across the axis is (correctly) filtered out.
    axis_deg = None
    try:
        sl = json.loads((out_dir / "slick.geojson").read_text(encoding="utf-8"))
        axis_deg = sl["features"][0]["properties"].get("orientation_deg")
    except Exception:
        pass

    culprit = {
        "origin": {"lat": summary["lat"], "lon": summary["lon"],
                   "window_start_utc": summary["window_start_utc"],
                   "window_end_utc": summary["window_end_utc"]},
        "axis_deg": axis_deg,
        "behaviour": {"slowdown": True, "ais_gap_minutes": 47},
    }
    culprit_path = engine_dir(out_dir) / "culprit.json"
    culprit_path.write_text(json.dumps(culprit, indent=2), encoding="utf-8")

    bbox = (meta or {}).get("bbox") or [
        summary["lon"] - 0.25, summary["lat"] - 0.25,
        summary["lon"] + 0.25, summary["lat"] + 0.25]
    start = summary["window_start_utc"]
    end = (meta or {}).get("acquired_utc") or summary["window_end_utc"]

    # Fleet identity varies per scene, so two incidents never share vessel
    # names or MMSIs -- while staying reproducible for the same scene.
    import zlib

    fleet_seed = zlib.crc32(str((meta or {}).get("scene_id", "")).encode())

    out = engine_dir(out_dir) / "vessels_generated.parquet"
    res = engines.generate_ais(bbox=bbox, start=start, end=end, out=out.resolve(),
                               culprit_json=culprit_path.resolve(), n_vessels=40,
                               fleet_seed=fleet_seed)
    if res.ok:
        stage.warnings.append(
            f"synthesised AIS around the computed origin "
            f"({summary['lat']:.3f}, {summary['lon']:.3f}) -- flagged SYNTHETIC")
        return out
    stage.warnings.append(f"AIS generation failed ({res.error_class}): {res.detail}")
    return existing


def engine_native_vessels(vessels: Path, out_dir: Path) -> Path:
    """Rewrite vessels.parquet into the column names the engine expects.

    The contract names the time column `timestamp_utc`; the attribution engine
    reads `timestamp`, per the developer handbook. Same data, same divergence
    as the GeoJSON schemas -- translated at the boundary rather than forcing
    either side to change.
    """
    try:
        import pandas as pd

        df = pd.read_parquet(vessels)
        renames = {"timestamp_utc": "timestamp", "draught_m": "draft_m",
                   "interpolated": "gap_flag"}
        for src, dst in renames.items():
            if src in df.columns and dst not in df.columns:
                df[dst] = df[src]
        native = engine_dir(out_dir) / "vessels.parquet"
        df.to_parquet(native, index=False)
        return native
    except Exception:
        return Path(vessels)


def resolve_vessels(out_dir: Path) -> Optional[Path]:
    """Locate vessels.parquet, preferring real AIS over synthetic.

    Krishnan's service writes into its own output directory; the contract mock
    is the last resort so the pipeline still produces a ranked list when no AIS
    has been fetched yet.
    """
    for candidate in (
        out_dir / "vessels.parquet",
        REPO_ROOT / "data" / "ais" / "vessels.parquet",
        REPO_ROOT / "5_krishnan_ais_service" / "test_output" / "vessels.parquet",
        MOCKS / "vessels.parquet",
    ):
        if Path(candidate).exists():
            return Path(candidate).resolve()
    hits = sorted((REPO_ROOT / "data" / "ais").glob("**/vessels.parquet")) \
        if (REPO_ROOT / "data" / "ais").is_dir() else []
    return hits[0].resolve() if hits else None


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

    # --- drift (hindcast + forecast) --------------------------------------
    slick_native = engine_dir(out_dir) / "slick.geojson"
    currents, wind = resolve_metocean(meta, out_dir)
    acquired_utc = (meta or {}).get("acquired_utc")

    # Forcing is chosen PER DIRECTION. A hindcast needs coverage before the
    # acquisition, a forecast needs coverage after it, and a grid can be
    # excellent for one and useless for the other -- the currents cache here
    # runs to the acquisition hour and stops, which is ideal for backtracking
    # and worthless going forward. Judging both runs on the same intersection
    # let a backward-only grid silently truncate the forecast to nothing.
    MIN_USEFUL_H = 6.0
    coverage = {}
    for label, path in (("currents", currents), ("wind", wind)):
        if path is not None:
            before, after = forcing_coverage_hours([path], acquired_utc)
            coverage[label] = (path, before, after)

    def forcing_for(mode: str):
        """(currents, wind, hours_available, notes) usable for this direction."""
        idx = 1 if mode == "hindcast" else 2
        chosen, notes, spans = {}, [], []
        for label, (path, before, after) in coverage.items():
            span = (before, after)[idx - 1]
            if span is None:
                chosen[label] = path
                continue
            if span < MIN_USEFUL_H:
                notes.append(
                    f"{label} not used for {mode}: covers {span:.1f}h "
                    f"{'before' if mode == 'hindcast' else 'after'} acquisition")
                continue
            chosen[label] = path
            spans.append(span)
        available = min(spans) if spans else None
        if "currents" not in chosen and "wind" in chosen:
            notes.append(f"{mode}: WIND-ONLY drift (documented degraded mode) -- "
                         f"no currents grid covers this direction")
        return chosen.get("currents"), chosen.get("wind"), available, notes

    for name, mode, hours in (("drift_hindcast", "hindcast", 24),
                              ("drift_forecast", "forecast", 24)):
        s = by_name[name]
        if not slick_native.exists():
            stage_mocked(s, out_dir, "no slick to drift")
            continue

        mode_currents, mode_wind, available, notes = forcing_for(mode)
        s.warnings.extend(notes)
        if mode_currents is None and mode_wind is None:
            stage_mocked(s, out_dir, f"no forcing grid covers the {mode} window")
            continue

        # Clamp to the forcing we actually have. Walking off the end of the
        # grid raises BAD_GRID and loses the stage entirely; a shorter run that
        # says so is far more useful than no run at all.
        clamped = hours
        if available is not None and available < hours:
            clamped = max(int(available), 1)
            s.warnings.append(
                f"{mode} shortened {hours}h -> {clamped}h: forcing only covers "
                f"{available:.1f}h {'before' if mode == 'hindcast' else 'after'} "
                f"acquisition")
        hours = clamped
        native = engine_dir(out_dir) / s.output
        res = engines.drift(slick=slick_native.resolve(),
                            out=native.resolve(),
                            currents=mode_currents, wind=mode_wind,
                            mode=mode, hours=hours)
        s.seconds = res.seconds
        if res.ok:
            forcing = {"currents": mode_currents.name if mode_currents else None,
                       "wind": mode_wind.name if mode_wind else None,
                       "engine": res.engine_used, "hours": hours}
            shutil.copy(native, out_dir / s.output)
            normalise.normalise_file(
                "origin_cloud" if mode == "hindcast" else "forecast",
                out_dir / s.output, scene_meta=meta or {}, forcing=forcing)
            s.source = "real"
            s.engine_used = res.engine_used
            # The Euler integrator is the guaranteed path, not the primary one;
            # say so rather than presenting it as a full OpenDrift run.
            s.status = "ok" if res.engine_used in ("openoil", "oceandrift") else "fallback"
            s.warnings = res.warnings
            kind = ("currents+wind" if mode_currents and mode_wind
                    else "wind-only" if mode_wind else "currents-only")
            s.detail = f"Engine B [{res.engine_used}] {mode} {hours}h, {kind}"
        else:
            s.warnings.append(f"Engine B failed ({res.error_class}): {res.detail}")
            stage_mocked(s, out_dir, f"Engine B failed: {res.error_class}")

    # --- attribution ------------------------------------------------------
    s = by_name["attribution"]
    origin_native = engine_dir(out_dir) / "origin_cloud.geojson"
    vessels_path = ensure_vessels(out_dir, origin_native, meta, s)
    if origin_native.exists() and vessels_path is not None:
        native = engine_dir(out_dir) / s.output
        vessels_native = engine_native_vessels(Path(vessels_path), out_dir)
        res = engines.attribution(origin=origin_native.resolve(),
                                  vessels=vessels_native.resolve(),
                                  out=native.resolve(),
                                  slick=slick_native.resolve() if slick_native.exists() else None,
                                  investigation_id=run_id)
        s.seconds = res.seconds
        if res.ok:
            shutil.copy(native, out_dir / s.output)
            normalise.normalise_file("suspects", out_dir / s.output,
                                     scene_meta=meta or {}, run_id=run_id)
            s.source = "real"
            s.status = "ok"
            s.warnings = res.warnings
            payload = json.loads((out_dir / s.output).read_text(encoding="utf-8"))
            n = len(payload.get("suspects", []))
            top = payload["suspects"][0] if n else None
            s.detail = (f"Engine C: {n} suspect(s)"
                        + (f", top MMSI {top['mmsi']} score {top['total_score']:.2f}"
                           if top else ""))
        else:
            s.warnings.append(f"Engine C failed ({res.error_class}): {res.detail}")
            stage_mocked(s, out_dir, f"Engine C failed: {res.error_class}")
    else:
        why = ("no origin cloud" if not origin_native.exists()
               else "no vessels.parquet available")
        stage_mocked(s, out_dir, why)

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
    # Publish the vessel file the run ACTUALLY attributed against. This used to
    # fall back to the mock whenever the run dir had no vessels.parquet, which
    # planted 20 unrelated mock vessels into a run whose attribution had used a
    # different 40-vessel set -- the suspects then matched nothing on the map,
    # and a run reporting 5/5 real stages carried a mock file in it.
    if vessels_path is not None and Path(vessels_path).exists():
        dest = out_dir / "vessels.parquet"
        if Path(vessels_path).resolve() != dest.resolve():
            shutil.copy(vessels_path, dest)

    src = MOCKS / "provider_status.json"
    if src.exists() and not (out_dir / "provider_status.json").exists():
        shutil.copy(src, out_dir / "provider_status.json")

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
