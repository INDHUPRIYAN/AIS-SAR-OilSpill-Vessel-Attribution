"""Pipeline orchestrator -- scene in, full contract set out.

Runs every stage and validates each output against its frozen contract. The
pipeline never halts and never manufactures a result: each stage lands in the
manifest with a status of

    ok       -- a real component produced and validated this
    fallback -- the real component failed; a degraded REAL path produced this
    failed   -- nothing usable (the run continues, the layer is absent)

`mock` is a legacy status found only in runs sealed before 2026-09-20, when an
unavailable stage was served a static file from contracts/mocks/. No new run
writes it.

The UI reads the manifest and badges every layer accordingly, which is what
keeps a demo honest when half the team's components are still in flight.

Stage ownership (see docs/PS26143_Team_Split_Handbook.md):
    detect        Indhu     -- real
    characterise  Nandha    -- stand-in until Engine A lands
    drift         Nandha    -- Engine B
    attribution   Nandha    -- Engine C

Usage:
    python -m backend.services.pipeline.run --scene contracts/mocks/scene_sigma0_db.tif \\
        --scene-meta contracts/mocks/scene_meta.json --run-id inv-001
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[4]
for p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from contracts.schemas import CONTRACTS  # noqa: E402
from backend.services.pipeline import ais_index, engines, footprint_crop, normalise, provenance  # noqa: E402

MOCKS = REPO_ROOT / "contracts" / "mocks"
RUNS = REPO_ROOT / "data" / "runs"
METOCEAN_CACHE = REPO_ROOT / "metocean_service" / "data" / "metocean"


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
        self.engine_used: Optional[str] = None   # set from structured output
        # Where the BYTES came from (sensor | synthetic | cached), distinct from
        # `source`, which records which code path ran (real | fallback | mock).
        self.data_source = "unknown"
        self.seconds = 0.0
        self.warnings: List[str] = []
        # sha256(stage + inputs + params) -- design doc §10 invariant 1. Set by
        # `key()` as each stage learns what it is actually running against, so
        # the manifest records not just what a stage produced but what it was
        # asked to produce. A retry with the same key is safe to skip.
        self.idempotency_key: Optional[str] = None

    def key(self, inputs: Optional[dict] = None, params: Optional[dict] = None) -> str:
        self.idempotency_key = provenance.idempotency_key(self.name, inputs, params)
        return self.idempotency_key

    def to_dict(self) -> dict:
        return {
            "stage": self.name, "owner": self.owner, "status": self.status,
            # The backend that actually produced this layer (e.g. "euler", "ml"),
            # read from structured output -- never inferred from log text.
            "engine_used": self.engine_used,
            "data_source": self.data_source,
            "output": self.output, "contract": self.contract,
            "source": self.source, "detail": self.detail,
            "seconds": round(self.seconds, 2), "warnings": self.warnings,
            "idempotency_key": self.idempotency_key,
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


def stage_unavailable(stage: Stage, out_dir: Path, reason: str) -> bool:
    """Record that a stage produced nothing, and write nothing.

    This used to copy a static file from contracts/mocks/ into the run, so a
    clean scene with no oil in it received a fabricated slick off Chennai, an
    origin cloud and a ranked suspect list, all badged MOCK. A label does not
    make manufactured geometry acceptable inside a real run: a stage that did
    not run has no output, and the UI renders the absence.
    """
    stale = out_dir / stage.output
    if stale.exists() and not stage.output.endswith(".tif"):
        stale.unlink()
    stage.status, stage.source, stage.detail = "failed", "none", reason
    return False


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


def stage_detect(stage: Stage, scene: Path, scene_id: str, meta: Optional[dict],
                 out_dir: Path, weights: Path, force_engine: Optional[str]) -> Optional[dict]:
    from backend.services.detection.service import detect

    screened = engine_dir(out_dir) / SCREENED_MASK
    screened.unlink(missing_ok=True)
    resp = detect(scene, scene_id, out_dir, weights, meta, force_engine,
                  screened_out=screened)
    stage.source = "real"
    stage.engine_used = resp.engine.value
    stage.status = "ok" if resp.engine.value == "ml" else "fallback"
    oil = [c for c in resp.candidates if c.class_.value == "oil"]
    look = [c for c in resp.candidates if c.class_.value == "lookalike"]
    stage.detail = (f"engine={resp.engine.value}, {len(oil)} oil + {len(look)} "
                    f"look-alike candidate(s), confidence {resp.confidence}")
    warn_file = out_dir / "detect_warnings.json"
    if warn_file.exists():
        stage.warnings = json.loads(warn_file.read_text())
    # With oil AND look-alikes, characterisation measures the screened mask
    # (see characterise_mask_for). With look-alikes only there is no oil to
    # keep, so the full mask still flows downstream -- say so rather than
    # letting a fully-rejected scene become an origin cloud that looks real.
    if look and not oil:
        stage.warnings.append(
            "every candidate was rejected as a look-alike by the screening "
            "model; downstream drift still runs on the full mask, so treat "
            "this origin and its suspects as unconfirmed")
    return resp.model_dump(by_alias=True)


SCREENED_MASK = "screened_mask.tif"


def characterise_mask_for(out_dir: Path, detect_result: dict) -> Path:
    """The mask characterisation should measure.

    The oil-only mask when detection wrote one (the screen split the regions),
    otherwise raw_mask.tif. Before this, every region the screen rejected was
    characterised too, and because drift seeds from the largest slick, the
    hindcast and the attribution could trace a look-alike -- which is what the
    flagship run did.
    """
    screened = engine_dir(out_dir) / SCREENED_MASK
    return screened if screened.exists() else Path(detect_result["mask_path"])


def stage_characterise(stage: Stage, scene: Path, out_dir: Path,
                       detect_result: dict, meta: Optional[dict]) -> bool:
    """Engine A for real; the stand-in only if the real engine cannot run."""
    scene_meta_path = out_dir / "scene_meta.json"
    if meta and not scene_meta_path.exists():
        scene_meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if scene_meta_path.exists():
        native = engine_dir(out_dir) / stage.output
        # Full scenes are cropped to the detection footprint (+ sea margin)
        # before Engine A, which otherwise loads the whole raster.
        mask_src = characterise_mask_for(out_dir, detect_result)
        if mask_src.name == SCREENED_MASK:
            stage.warnings.append(
                "measured the screened mask: regions the screening model rejected "
                "as look-alikes are not characterised, drifted or attributed "
                "(they remain in raw_mask.tif and detect_response.json)")
        mask_in, scene_in, crop_note = footprint_crop.crop_for_engine_a(
            mask_src.resolve(), Path(scene).resolve(), engine_dir(out_dir))
        if crop_note:
            stage.warnings.append(crop_note)
        res = engines.characterise(
            mask=Path(mask_in).resolve(),
            scene_meta=scene_meta_path.resolve(),
            out=native.resolve(),
            scene_db=Path(scene_in).resolve() if scene_in else None,
            confidence=detect_result.get("confidence"))
        stage.seconds = res.seconds
        if res.ok:
            shutil.copy(native, out_dir / stage.output)
            normalise.normalise_file("slick", out_dir / stage.output,
                                     scene_meta=meta or {}, detect=detect_result)
            stage.status, stage.source = "ok", "real"
            stage.engine_used = res.engine_used
            stage.warnings = [*stage.warnings, *res.warnings]
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

    engine_a = REPO_ROOT / "analysis_engines" / "engines" / "characterise" / "spill_features.py"
    if engine_a.exists() and engine_a.stat().st_size > 2000:
        stage.detail = "Nandha's Engine A detected but not yet wired; using stand-in"

    import rasterio
    db, profile, valid = read_scene(scene)
    with rasterio.open(characterise_mask_for(out_dir, detect_result)) as src:
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
    """Kept under its old name for its call sites; it no longer serves a mock."""
    return stage_unavailable(stage, out_dir, reason)


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


_GRID_FACTS: Dict[tuple, Optional[dict]] = {}


def grid_facts(path: Path) -> Optional[dict]:
    """The footprint and time span of a forcing grid, read once per file.

    Resolving forcing opens every grid in the cache to ask where and when it
    is -- 51 NetCDF opens at ~0.4 s each, on every call, which made the
    workspace's forcing_field endpoint a 25 s request. A grid's extent cannot
    change without the file changing, so the answer is keyed on the file's
    path, size and mtime and read once. None means the file could not be
    read; a missing time axis is ``times: None``.
    """
    try:
        st = Path(path).stat()
        key = (str(Path(path).resolve()), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key in _GRID_FACTS:
        return _GRID_FACTS[key]
    facts = None
    try:
        import numpy as _np
        import xarray as xr

        with xr.open_dataset(path) as ds:
            lon_name = next((n for n in ("lon", "longitude", "x") if n in ds.coords), None)
            lat_name = next((n for n in ("lat", "latitude", "y") if n in ds.coords), None)
            facts = {"lon": None, "lat": None, "times": None}
            if lon_name and lat_name:
                lon = ds[lon_name].values
                lat = ds[lat_name].values
                facts["lon"] = (float(_np.nanmin(lon)), float(_np.nanmax(lon)))
                facts["lat"] = (float(_np.nanmin(lat)), float(_np.nanmax(lat)))
            if "time" in ds.coords or "time" in ds.dims:
                times = ds["time"].values
                facts["times"] = (str(times[0])[:19], str(times[-1])[:19])
    except Exception:
        facts = None
    _GRID_FACTS[key] = facts
    return facts


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
    facts = grid_facts(path)
    if not facts or not facts["lon"] or not facts["lat"]:
        return None

    lo0, la0, lo1, la1 = [float(v) for v in bbox]
    lon_min, lon_max = facts["lon"]
    lat_min, lat_max = facts["lat"]
    # Grids are commonly stored on 0..360; compare in the scene's convention.
    if lon_min >= 0.0 and lon_max > 180.0 and lo0 < 0.0:
        lon_min, lon_max = lon_min - 360.0, lon_max - 360.0
    return (lon_min <= lo0 and lon_max >= lo1
            and lat_min <= la0 and lat_max >= la1)


def data_source_for_scene(scene: Path, meta: Optional[dict]) -> str:
    """sensor | synthetic | cached for the SAR raster itself.

    `source: "real"` on a stage means real code ran; it says nothing about the
    pixels. A run on contracts/mocks/scene_sigma0_db.tif ran real code on a
    fabricated scene and must never badge as sensor data.
    """
    parts = {p.lower() for p in Path(scene).resolve().parts}
    if "mocks" in parts or "synthetic" in parts:
        return "synthetic"
    flag = str((meta or {}).get("source", "")).lower()
    if flag in ("synthetic", "mock"):
        return "synthetic"
    # `cached` on the scene meta describes the PROVIDER path (served from the
    # local cache instead of CDSE/ASF); the pixels are still sensor data, and
    # that path is already visible as the stage's execution `source`.
    # Trujillo training chips are genuine Sentinel-1 backscatter (real sensor
    # data), though not a full scene; the scene_id tells the two apart.
    return "sensor"


def data_source_for_forcing(paths) -> str:
    """sensor (reanalysis/model products from a real provider) | synthetic | cached."""
    verdicts = []
    for path in paths:
        if not path:
            continue
        path = Path(path)
        try:
            import xarray as xr
            with xr.open_dataset(path) as ds:
                blob = " ".join(str(v) for v in ds.attrs.values()).lower()
            # Only the dataset's own `source` attribute may declare it synthetic;
            # titles and notes are prose.
            src_attr = ""
            try:
                with xr.open_dataset(path) as ds2:
                    src_attr = str(ds2.attrs.get("source", "")).lower()
            except Exception:
                pass
            if src_attr in ("synthetic", "mock", "fabricated"):
                verdicts.append("synthetic")
                continue
        except Exception:
            pass
        status = path.parent / "provider_status.json"
        try:
            st = json.loads(status.read_text(encoding="utf-8"))
            # Structured fields first -- free text (notes, titles) must never
            # decide provenance: a note SAYING "replaces the synthetic field"
            # once flipped a real CMEMS pull to SYNTHETIC.
            kind = "currents" if "current" in path.name.lower() else "wind"
            explicit = str(st.get("data_source") or (st.get(kind) or {}).get("source") or "").lower()
            providers = st.get("providers_used") or {}
            prov = str(providers.get(kind) or (st.get(kind) or {}).get("provider") or "").lower()
            real_providers = ("cmems", "era5", "hycom", "openmeteo", "open-meteo", "copernicus", "ecmwf")
            if explicit in ("synthetic", "mock"):
                verdicts.append("synthetic")
            elif explicit == "sensor" or any(k in prov for k in real_providers):
                verdicts.append("sensor")
            elif explicit == "cached" or prov in ("cache", "localcache", "staticcache"):
                verdicts.append("cached")
            else:
                verdicts.append("cached")
        except Exception:
            verdicts.append("cached")
    if not verdicts:
        return "synthetic"
    if "synthetic" in verdicts:
        return "synthetic"
    return "sensor" if all(v == "sensor" for v in verdicts) else "cached"


def data_source_for_vessels(path) -> str:
    """The vessels file's own `source` column decides: any synthetic row makes
    the layer synthetic -- a planted culprit poisons the whole ranking."""
    if not path:
        return "synthetic"
    try:
        import pandas as pd
        df = pd.read_parquet(path, columns=["source"])
        vals = set(str(v).lower() for v in df["source"].dropna().unique())
        if not vals or "synthetic" in vals or "mock" in vals:
            return "synthetic"
        return "sensor" if vals <= {"real", "sensor"} else "cached"
    except Exception:
        return "synthetic"


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
        REPO_ROOT / "analysis_engines" / "samples" / "inputs",
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


@lru_cache(maxsize=32)
def _forcing_attrs(path_str: str) -> tuple:
    """Global attributes of a forcing grid, as a hashable pair list."""
    try:
        import xarray as xr

        with xr.open_dataset(path_str) as ds:
            return tuple((str(k), str(v)) for k, v in ds.attrs.items())
    except Exception:                              # noqa: BLE001 - provenance is best-effort
        return ()


def forcing_provenance(path) -> Optional[dict]:
    """Who actually served this grid, read from the file itself.

    The published forcing block used to be a bare filename, which said nothing
    about whether the physics came from CMEMS or a static fallback (audit
    H-11). The normalised grids carry a `provider` global attribute, so the
    real identity is available without guessing -- and when it is absent the
    field is simply omitted rather than filled with the filename dressed up as
    a provider.
    """
    if not path:
        return None
    attrs = dict(_forcing_attrs(str(path)))
    block = {"file": Path(path).name}
    for src, dst in (("provider", "provider"), ("title", "dataset"),
                     ("history", "normalised")):
        if attrs.get(src):
            block[dst] = attrs[src]
    return block


@lru_cache(maxsize=1)
def _git_sha() -> str:
    """Commit that produced this run, honestly flagged when the tree is dirty.

    "unknown" and "<sha>+dirty" are both more useful than a clean-looking sha
    that does not describe the code that actually ran.
    """
    import subprocess

    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return "unknown"
        head = sha.stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO_ROOT),
                               capture_output=True, text=True, timeout=20)
        return f"{head}+dirty" if dirty.stdout.strip() else head
    except Exception:                              # noqa: BLE001 - never fail a run for this
        return "unknown"


@lru_cache(maxsize=1)
def _model_records() -> tuple:
    """Identity and hash of every model file the pipeline can load.

    Read from the ONNX metadata the exporter embedded, so the manifest names
    the checkpoint that actually ran rather than whatever a side-file claims.
    """
    weights_dir = REPO_ROOT / "main_system" / "backend" / "services" / "detection" / "weights"
    records = []
    for kind, filename in (("screen", "screen.onnx"), ("segment", "segment.onnx")):
        path = weights_dir / filename
        if not path.exists():
            continue
        raw = path.read_bytes()
        entry = {"kind": kind, "file": filename,
                 "sha256": provenance.sha256_bytes(raw), "bytes": len(raw)}
        try:
            import onnx

            props = {kv.key: kv.value for kv in
                     onnx.load(str(path), load_external_data=False).metadata_props}
            entry["name"] = props.get("model_version")
            entry["config_fingerprint"] = props.get("config_fingerprint")
        except Exception:                          # noqa: BLE001 - hash alone still helps
            pass
        records.append(entry)
    return tuple(records)


@lru_cache(maxsize=1)
def _weights_profile_stamp() -> Optional[dict]:
    """Which attribution weight profile scored this run.

    A hash of the `weights:` block only, so the stamp is stable when an
    unrelated gate threshold in the same file is tuned.
    """
    import hashlib

    path = REPO_ROOT / "analysis_engines" / "config" / "attribution_weights.yaml"
    if not path.exists():
        return None
    try:
        import yaml

        block = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("weights") or {}
        canonical = json.dumps({k: block[k] for k in sorted(block)}, sort_keys=True)
        total = round(sum(float(v) for v in block.values()), 12)
        return {
            "profile": "default-v1",
            "profile_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "sum": total,
            "validated": abs(total - 1.0) <= 1e-6,
        }
    except Exception:                              # noqa: BLE001
        return None


def _run_provider_snapshot(meta, currents, wind, vessels_path, by_name) -> dict:
    """Per-layer record of what actually served this run.

    Deliberately NOT a provider probe. A probe answers "is CDSE reachable
    now?", which says nothing about the run it would be filed beside -- and
    the file it replaces was worse still, a static mock with no relationship
    to anything. Every entry here is derived from an input the run really
    consumed, and a layer with no source says so rather than guessing.
    """
    def stage_status(name: str) -> Optional[str]:
        stage = by_name.get(name)
        return stage.status if stage else None

    scene_provider = (meta or {}).get("provider_used")
    snapshot = {
        "owner": "measured",
        "note": "What this run consumed, recorded at seal time. Not a "
                "reachability probe, and not a statement about provider "
                "health now -- see /api/apis/status for that.",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scene": {
            "provider": scene_provider,
            "source": (meta or {}).get("source"),
            "scene_id": (meta or {}).get("scene_id"),
            "data_source": (by_name["detect"].data_source
                            if "detect" in by_name else None),
        },
        "currents": forcing_provenance(currents),
        "wind": forcing_provenance(wind),
        "ais": {
            "file": Path(vessels_path).name if vessels_path else None,
            "data_source": (by_name["attribution"].data_source
                            if "attribution" in by_name else None),
        },
        "stages": {name: stage_status(name) for name in
                   ("detect", "characterise", "drift_hindcast",
                    "drift_forecast", "attribution")},
    }
    return {k: v for k, v in snapshot.items() if v is not None}


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
        acquired = datetime.fromisoformat(acquired_utc.replace("Z", "+00:00"))
        first, last = None, None
        for p in [x for x in paths if x]:
            facts = grid_facts(Path(p))
            if facts is None:
                raise OSError(f"unreadable forcing grid: {p}")
            if not facts["times"]:
                continue
            t0 = datetime.fromisoformat(facts["times"][0]).replace(tzinfo=timezone.utc)
            t1 = datetime.fromisoformat(facts["times"][1]).replace(tzinfo=timezone.utc)
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


def file_data_source(vessels: Optional[Path]) -> str:
    """What a vessels.parquet says about itself: real, synthetic or unknown.

    Read from the file's own `source` column rather than from its path, because
    a filename is a claim and the column is the record. "unknown" is a distinct
    answer from "synthetic": a legacy file that predates the column has not
    told us it is fabricated, and reporting it as such would be its own
    invention.
    """
    if vessels is None:
        return "none"
    try:
        import pandas as pd

        col = pd.read_parquet(vessels, columns=["source"])["source"]
    except Exception:
        return "unknown"
    values = {str(v).strip().lower() for v in col.dropna().unique()}
    if not values:
        return "unknown"
    if values == {"real"}:
        return "real"
    if "real" in values:
        return "mixed"
    return "synthetic"


def real_ais_candidates(out_dir: Path) -> List[Path]:
    """Every vessels.parquet this run could legitimately use, best first.

    The run directory comes first because `backend.prepare_ais` puts a file
    there specifically for this scene. `data/ais/real/` is next: archives
    ingested from MarineCadastre for some other date, which may or may not
    cover this origin -- that is measured, not assumed.
    """
    real_dir = REPO_ROOT / "data" / "ais" / "real"
    ordered = [
        out_dir / "vessels.parquet",
        *(sorted(real_dir.glob("*.parquet")) if real_dir.is_dir() else []),
        REPO_ROOT / "data" / "ais" / "vessels.parquet",
        REPO_ROOT / "ais_service" / "test_output" / "vessels.parquet",
        MOCKS / "vessels.parquet",
    ]
    seen, out = set(), []
    for candidate in ordered:
        candidate = Path(candidate)
        if not candidate.exists():
            continue
        key = candidate.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


class AISChoice:
    """Which AIS this run used, and every file it looked at to decide.

    The decision itself was always made; what was missing was any record of it
    (audit A-05/06). A sealed run that says only "vessels.parquet" cannot
    distinguish real AIS that covered the origin from a synthetic fleet built
    around it, and those are very different claims about the same investigation.
    """

    def __init__(self, path=None, data_source="none", covered=False,
                 selection="none", detail="", considered=None):
        self.path = Path(path) if path else None
        self.data_source = data_source
        self.covered = covered
        self.selection = selection
        self.detail = detail
        self.considered = considered or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selection": self.selection,
            "data_source": self.data_source,
            "covers_origin": self.covered,
            "file": self.path.name if self.path else None,
            "detail": self.detail,
            # The ledger of rejections. Without it, "we synthesised" reads as a
            # preference rather than as the last option after real data was
            # checked and did not cover this origin.
            "considered": self.considered,
        }


def ensure_vessels(out_dir: Path, origin_native: Path, meta: Optional[dict],
                   stage: "Stage") -> AISChoice:
    """Real AIS if any covers this origin; otherwise synthesise it here.

    Real-first, and measured rather than assumed (audit A-05/06). Every
    candidate file is judged on the one question that matters -- does it hold a
    vessel inside the cloud this run computed, in the window this run computed
    -- and real data wins whenever the answer is yes. A real archive for a
    different date is NOT better than synthetic: its vessels never enter this
    cloud, attribution correctly returns NO_VESSELS_IN_WINDOW, and the run
    looks broken when it was merely given the wrong day.

    Real AIS does not exist for Indian waters, which is why the synthetic
    generator is mandatory infrastructure rather than a fallback. When it runs,
    the culprit is planted at the origin THIS run computed, and every row it
    produces stays flagged SYNTHETIC exactly as before.

    Returns the choice AND the reasoning, so the sealed manifest can say which
    path ran instead of leaving a reader to infer it from a filename.
    """
    summary = origin_summary(origin_native)
    candidates = real_ais_candidates(out_dir)

    if summary is None or not summary.get("window_start_utc"):
        existing = resolve_vessels(out_dir)
        return AISChoice(
            existing, file_data_source(existing), covered=False,
            selection="unjudged" if existing else "none",
            detail="no origin window was computed, so no AIS file could be "
                   "tested against it")

    considered: List[Dict[str, Any]] = []
    covering: List[tuple] = []
    for candidate in candidates:
        source = file_data_source(candidate)
        covers = vessels_cover_origin(candidate, summary)
        considered.append({"file": candidate.name,
                           "path": str(candidate).replace("\\", "/"),
                           "data_source": source,
                           "covers_origin": covers})
        if covers:
            covering.append((source, candidate))

    # Real first, then anything else that covers, then synthesis. "mixed"
    # counts as real here because it contains genuine reports; the per-vessel
    # `source` column still tells the UI which rows those are.
    for wanted in ("real", "mixed"):
        for source, candidate in covering:
            if source == wanted:
                stage.warnings.append(
                    f"real AIS: {candidate.name} covers the computed origin "
                    f"window -- no synthetic fleet was generated")
                return AISChoice(candidate, source, covered=True,
                                 selection="real",
                                 detail="a real AIS archive holds reports inside "
                                        "the computed origin window",
                                 considered=considered)
    if covering:
        source, candidate = covering[0]
        return AISChoice(candidate, source, covered=True,
                         selection="existing",
                         detail=f"{candidate.name} covers the origin window",
                         considered=considered)

    for entry in considered:
        stage.warnings.append(
            f"{entry['file']} ({entry['data_source']}) does not cover the "
            f"computed origin window")
    if considered:
        stage.warnings.append("generating AIS for this origin instead")
    existing = candidates[0] if candidates else None

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
        return AISChoice(out, "synthetic", covered=True, selection="synthetic",
                         detail="no real AIS covered the computed origin window; "
                                "a fleet was synthesised around it and every row "
                                "is flagged SYNTHETIC",
                         considered=considered)
    stage.warnings.append(f"AIS generation failed ({res.error_class}): {res.detail}")
    return AISChoice(existing, file_data_source(existing), covered=False,
                     selection="generation_failed",
                     detail=f"AIS generation failed ({res.error_class}): {res.detail}",
                     considered=considered)


def vessel_sources_of(vessels: Path) -> Dict[int, str]:
    """mmsi -> source ("real"/"synthetic") from the vessels file attribution ranked."""
    try:
        import pandas as pd
        df = pd.read_parquet(vessels, columns=["mmsi", "source"])
        return {int(m): str(v) for m, v in zip(df["mmsi"], df["source"])}
    except Exception:
        return {}


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
        # Tolerant of BOTH generations of vessels.parquet: contract-compliant
        # files (timestamp_utc / draught_m / interpolated — what ais_service
        # now emits) and old run artefacts still on disk (timestamp / draft_m
        # / gap_flag). Each name is mirrored to its twin when absent, so the
        # engine (old names) and the frontend (contract names) both read the
        # same file whichever generation produced it.
        renames = {"timestamp_utc": "timestamp", "draught_m": "draft_m",
                   "interpolated": "gap_flag",
                   "timestamp": "timestamp_utc", "draft_m": "draught_m",
                   "gap_flag": "interpolated"}
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
        REPO_ROOT / "ais_service" / "test_output" / "vessels.parquet",
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



ERROR_CLASS_RE = re.compile(r"\b([A-Z][A-Z_]{3,})\b")


class RunCancelled(Exception):
    """A cancel was requested and honoured between stages.

    Not an error: the run did what it was asked. It is a distinct exception so
    the caller can tell "the operator stopped this" from "the pipeline broke",
    which are different rows in the runs table and different things to show.
    """


# run_id -> a callable returning True when this run should stop. A registry
# rather than a parameter threaded through eight `flush_status` call sites:
# the check has to happen at every stage boundary, and an argument that must be
# passed in eight places is an argument that will eventually be forgotten in one.
_cancel_checks: Dict[str, Any] = {}
_cancel_lock = threading.Lock()


def set_cancel_check(run_id: str, fn) -> None:
    with _cancel_lock:
        _cancel_checks[run_id] = fn


def clear_cancel_check(run_id: str) -> None:
    with _cancel_lock:
        _cancel_checks.pop(run_id, None)


def _cancel_requested(run_id: str) -> bool:
    with _cancel_lock:
        fn = _cancel_checks.get(run_id)
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception:                                  # noqa: BLE001
        # A broken cancel check must not stop a healthy run. Failing open here
        # is the safe direction: the worst case is a run that finishes.
        return False


def flush_status(out_dir: Path, run_id: str, scene_id: str, stages,
                 running: str = None) -> None:
    """Write status.json after every stage so the UI can render each layer the
    moment its stage completes, instead of waiting for the manifest at the end
    of the run. One row per stage: {stage, status, ok, engine_used, source,
    warnings, error_class, output}."""
    rows = []
    for s in stages:
        status = s.status or ("running" if s.name == running else "pending")
        err = None
        if status in ("failed", "mock"):
            m = ERROR_CLASS_RE.search(s.detail or "")
            err = m.group(1) if m else None
        rows.append({
            "stage": s.name,
            "status": status,
            "ok": status in ("ok", "fallback"),
            "engine_used": "fallback" if status == "fallback" else
                           ("primary" if status == "ok" else None),
            "source": s.source,
            "data_source": getattr(s, "data_source", "unknown"),
            "detail": s.detail,
            "warnings": list(s.warnings or []),
            "error_class": err,
            "output": s.output,
            "seconds": round(s.seconds or 0.0, 2),
        })
    payload = {
        "run_id": run_id,
        "scene_id": scene_id,
        "state": "running" if any(r["status"] in ("pending", "running")
                                  for r in rows) else "complete",
        "stages": rows,
    }
    tmp = out_dir / "status.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(out_dir / "status.json")

    # Checked AFTER the write, so a cancelled run still publishes the stage it
    # completed. Between stages only: killing a worker mid-write would leave a
    # half-finished GeoTIFF that a later run could mistake for a real artefact.
    if _cancel_requested(run_id):
        raise RunCancelled(f"run {run_id} cancelled after "
                           f"{sum(1 for s in stages if s.status)} stage(s)")


def run_pipeline(scene: Path, scene_meta: Optional[Path], run_id: str,
                 weights: Optional[Path] = None,
                 force_engine: Optional[str] = None) -> dict:
    t_start = time.time()
    out_dir = RUNS / run_id
    # §12: a completed run's artefacts are never overwritten. Re-running an
    # existing run_id would silently replace the files an investigation was
    # concluded from AND rewrite the hashes to match, leaving no trace. A
    # re-run gets a new run_id against the same scene.
    provenance.assert_writable(out_dir)
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
    flush_status(out_dir, run_id, scene_id, stages, running="detect")

    # --- detect -----------------------------------------------------------
    s = by_name["detect"]
    s.key(inputs={"scene": scene, "scene_meta": scene_meta},
          params={"weights": Path(weights).name,
                  "engine": force_engine or "auto"})
    t0 = time.time()
    detect_result = None
    try:
        detect_result = stage_detect(s, Path(scene), scene_id, meta, out_dir,
                                     Path(weights), force_engine)
    except Exception as exc:
        s.status, s.detail = "failed", f"{type(exc).__name__}: {exc}"
    s.seconds = time.time() - t0
    flush_status(out_dir, run_id, scene_id, stages, running="characterise")

    # --- characterise -----------------------------------------------------
    s = by_name["characterise"]
    s.key(inputs={"mask": "raw_mask.tif", "scene": scene},
          params={"scene_id": scene_id,
                  # characterise measures the oil-only mask when the screen
                  # split the regions; older runs measured the full mask.
                  "lookalike_screening": "oil-only-when-split"})
    t0 = time.time()
    if detect_result is None:
        stage_mocked(s, out_dir, "detection failed upstream")
    else:
        try:
            if not stage_characterise(s, Path(scene), out_dir, detect_result, meta):
                stage_mocked(s, out_dir, s.detail or "no oil region to characterise in this scene")
        except Exception as exc:
            stage_mocked(s, out_dir, f"stand-in failed ({type(exc).__name__}: {exc})")
    s.seconds = time.time() - t0
    flush_status(out_dir, run_id, scene_id, stages, running="drift_hindcast")

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
            flush_status(out_dir, run_id, scene_id, stages)
            continue

        mode_currents, mode_wind, available, notes = forcing_for(mode)
        s.key(inputs={"slick": "slick.geojson",
                      "currents": mode_currents.name if mode_currents else None,
                      "wind": mode_wind.name if mode_wind else None},
              params={"mode": mode, "hours": hours})
        s.warnings.extend(notes)
        if mode_currents is None and mode_wind is None:
            stage_mocked(s, out_dir, f"no forcing grid covers the {mode} window")
            flush_status(out_dir, run_id, scene_id, stages)
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
            forcing = {"currents": forcing_provenance(mode_currents),
                       "wind": forcing_provenance(mode_wind),
                       "engine": res.engine_used, "hours": hours}
            shutil.copy(native, out_dir / s.output)
            normalise.normalise_file(
                "origin_cloud" if mode == "hindcast" else "forecast",
                out_dir / s.output, scene_meta=meta or {}, forcing=forcing)
            s.source = "real"
            s.engine_used = res.engine_used
            # The in-house Lagrangian (Euler) integrator IS the declared primary:
            # dependency-free by design, verified on timestep negation,
            # per-particle perturbation, 4-D trilinear interpolation and
            # covariance ellipses. OpenDrift is future work, not the baseline
            # this path is measured against. Anything the artefact does not
            # name as a real backend stays badged FALLBACK.
            s.status = ("ok" if res.engine_used in ("euler", "openoil", "oceandrift")
                        else "fallback")
            s.warnings = res.warnings
            kind = ("currents+wind" if mode_currents and mode_wind
                    else "wind-only" if mode_wind else "currents-only")
            s.detail = f"Engine B [{res.engine_used}] {mode} {hours}h, {kind}"
        else:
            s.warnings.append(f"Engine B failed ({res.error_class}): {res.detail}")
            stage_mocked(s, out_dir, f"Engine B failed: {res.error_class}")
        flush_status(out_dir, run_id, scene_id, stages,
                     running="drift_forecast" if mode == "hindcast" else "attribution")

    # --- attribution ------------------------------------------------------
    s = by_name["attribution"]
    origin_native = engine_dir(out_dir) / "origin_cloud.geojson"
    ais_choice = ensure_vessels(out_dir, origin_native, meta, s)
    vessels_path = ais_choice.path
    s.key(inputs={"origin_cloud": "origin_cloud.geojson",
                  "vessels": Path(vessels_path).name if vessels_path else None,
                  "slick": "slick.geojson" if slick_native.exists() else None},
          params={"investigation_id": run_id})
    if origin_native.exists() and vessels_path is not None:
        native = engine_dir(out_dir) / s.output
        vessels_native = engine_native_vessels(Path(vessels_path), out_dir)
        # Spatial index: prune the vessel set through the partitioned AIS
        # store instead of handing Engine C a flat file to full-scan.
        vessels_native, ais_notes = ais_index.prune_via_store(
            Path(vessels_native), origin_native, run_id,
            origin_summary(origin_native), out_dir)
        if ais_notes.get("indexed"):
            s.warnings.append(
                f"AIS index: {ais_notes['vessels_in']} vessels/{ais_notes['rows_in']} rows "
                f"-> {ais_notes['vessels_out']} vessels/{ais_notes['rows_out']} rows "
                f"(query {ais_notes['t_query_ms']} ms vs full scan "
                f"{ais_notes['t_fullscan_ms']} ms; ingest {ais_notes['t_ingest_ms']} ms)")
        else:
            s.warnings.append(f"AIS index not used: {ais_notes.get('reason')}")
        res = engines.attribution(origin=origin_native.resolve(),
                                  vessels=Path(vessels_native).resolve(),
                                  out=native.resolve(),
                                  slick=slick_native.resolve() if slick_native.exists() else None,
                                  investigation_id=run_id)
        s.seconds = res.seconds
        if res.ok:
            shutil.copy(native, out_dir / s.output)
            normalise.normalise_file("suspects", out_dir / s.output,
                                     scene_meta=meta or {}, run_id=run_id,
                                     vessel_sources=vessel_sources_of(vessels_native))
            s.source = "real"
            s.status = "ok"
            s.engine_used = res.engine_used
            s.warnings = [*s.warnings, *res.warnings]
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
    flush_status(out_dir, run_id, scene_id, stages)

    # --- data provenance (TRANSFORMATION.md 3.1) ---------------------------
    # Orthogonal to `source`/status: what the bytes were, per layer.
    scene_ds = data_source_for_scene(Path(scene), meta)
    for name in ("detect", "characterise"):
        by_name[name].data_source = scene_ds if by_name[name].status in ("ok", "fallback") else "none"
    forcing_ds = data_source_for_forcing([currents, wind])
    for name in ("drift_hindcast", "drift_forecast"):
        by_name[name].data_source = (forcing_ds if by_name[name].status in ("ok", "fallback")
                                     else "none")
    by_name["attribution"].data_source = (
        data_source_for_vessels(vessels_path)
        if by_name["attribution"].status in ("ok", "fallback") else "none")

    # --- validate everything ---------------------------------------------
    for s in stages:
        produced = out_dir / s.output
        if s.status == "failed" or not produced.exists():
            continue
        err = validate(s.contract, produced)
        if err:
            s.warnings.append(f"CONTRACT VIOLATION: {err}")
            s.status = "failed"
    flush_status(out_dir, run_id, scene_id, stages)

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

    # What actually served THIS run, written from the run's own inputs. A
    # static mock was previously copied in from contracts/mocks/, so 19 sealed
    # run directories carried an identical fictional provider board that had
    # nothing to do with the run it sat next to (audit PC-11). This is a
    # measurement, not a probe: it records what the bytes came from, not what
    # some provider answered at seal time.
    (out_dir / "provider_status.json").write_text(
        json.dumps(_run_provider_snapshot(meta, currents, wind, vessels_path, by_name),
                   indent=2),
        encoding="utf-8")

    manifest = {
        "run_id": run_id,
        "scene_id": scene_id,
        "scene_path": str(scene).replace("\\", "/"),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_seconds": round(time.time() - t_start, 2),
        # Which code and which weights produced this. Without them a sealed run
        # proves only that its artefacts are unmodified -- not what generated
        # them, so a bug fixed later cannot be traced to the runs it affected.
        "code_git_sha": _git_sha(),
        "models": _model_records(),
        # Which AIS path ran, and what was rejected on the way (audit A-05/06).
        "ais": ais_choice.to_dict(),
        "attribution_profile": _weights_profile_stamp(),
        "stages": [s.to_dict() for s in stages],
    }
    # §12/§14: hash every artefact into the run record. This is what lets a
    # two-year-old investigation prove the GeoJSON in the report is the one the
    # pipeline produced. Writing the manifest is also what SEALS the run --
    # `assert_writable` above keys off its existence -- so it goes last.
    provenance.seal(out_dir, manifest)
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
    print(f"  {len(manifest['artefacts'])} artefact(s) hashed, "
          f"digest {manifest['artefact_digest'][:12]}...")
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
    ap.add_argument("--verify", metavar="RUN_ID",
                    help="re-hash a completed run's artefacts against its "
                         "manifest and report any that changed (design doc section 12)")
    args = ap.parse_args(argv)

    if args.verify:
        report = provenance.verify_run(RUNS / args.verify)
        if report["ok"]:
            print(f"verify {report['run_id']}: OK -- {report['checked']} artefact(s) "
                  f"unchanged since {report.get('generated_utc')}")
            return 0
        print(f"verify {report['run_id']}: {len(report['problems'])} problem(s)")
        for problem in report["problems"]:
            print(f"  - {problem}")
        # An unverifiable run (produced before hashing existed) is not a
        # tampered run; say so with a different exit code so a script can tell.
        return 2 if report.get("unverifiable") else 1

    run_id = args.run_id or f"inv-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    try:
        run_pipeline(args.scene, args.scene_meta, run_id, args.weights,
                     None if args.engine == "auto" else args.engine)
    except provenance.RunImmutable as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
