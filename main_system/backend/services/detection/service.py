"""The `/detect` service -- contract-frozen entry point for oil-slick detection.

Takes a calibrated Sigma0-dB GeoTIFF, returns a georeferenced 0/1 mask plus a
`DetectResponse` validated against `contracts/`. Engine selection is automatic
and always succeeds:

    ONNX segmenter (engine="ml")  ->  threshold+morphology (engine="threshold_fallback")

Consumers read `mask_path`, `confidence`, `candidates` and `engine`. They never
need to know which path ran -- that is the whole point of the fallback design,
and it is why the demo survives a missing GPU, missing weights, or a broken
ONNX Runtime install.

Usage:
    python -m backend.services.detection.service \\
        --scene contracts/mocks/scene_sigma0_db.tif \\
        --scene-id S1A_DEMO --out data/runs/inv-001
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "main_system") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "main_system"))

from contracts.schemas import Candidate, DetectResponse  # noqa: E402
from ml.config import load_config  # noqa: E402

from .threshold import ThresholdParams, detect_threshold  # noqa: E402

WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
DEFAULT_WEIGHTS = WEIGHTS_DIR / "segment.onnx"
DEFAULT_SCREEN = WEIGHTS_DIR / "screen.onnx"


@dataclass
class DetectionOutcome:
    """Internal result before it is shaped into the contract response."""

    mask: np.ndarray
    confidence: float
    engine: str
    model_version: str
    regions: List[dict]
    warnings: List[str]
    # Stage-1 output, kept so each delineated region can be labelled oil or
    # look-alike. None means the screen did not run, which is NOT the same as
    # "the screen found nothing" and must not be reported as a look-alike.
    screen: Optional[dict] = None


# --------------------------------------------------------------------------
# scene IO
# --------------------------------------------------------------------------


def read_scene(path: Path, band: Optional[int] = None):
    """Return (db_array, profile, valid_mask). Land/nodata is False in valid.

    The band is NOT hardcoded. Training reads `sar.primary_band` from the
    shared config (band 2: measured damping 8.12 dB, versus band 1's 1.45 dB,
    which sits inside the speckle noise), and inference has to read the same
    band or the model sees data unlike anything it was trained on.

    This was silently wrong for a long time because the demo scene is
    single-band, so band 1 happened to be correct there. On a real two-band
    scene it put the whole image near the -35 dB floor and the segmenter
    labelled 99.4% of it oil.
    """
    import rasterio

    if band is None:
        band = int(load_config().sar.primary_band)

    with rasterio.open(path) as src:
        # Single-band scenes are legitimate (already-extracted Sigma0); asking
        # for band 2 there is a config/scene mismatch, not a reason to fail.
        use = band if band <= src.count else 1
        db = src.read(use).astype(np.float32)
        profile = src.profile.copy()
        profile["_band_used"] = use
        profile["_band_requested"] = band
        nodata = src.nodata
        valid = np.isfinite(db)
        if nodata is not None:
            valid &= db != nodata
        # A calibrated Sigma0 scene should never be exactly 0 across a region;
        # treat a 0-filled border as unimaged rather than as very bright sea.
        valid &= ~(db == 0)
    return db, profile, valid


def write_mask(mask: np.ndarray, profile: dict, out_path: Path) -> Path:
    """Write the 0/1 mask on the scene's own grid, georeferencing preserved."""
    import rasterio

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Drop our own annotations (_band_used etc): everything left in the profile
    # is forwarded to GDAL as a creation option.
    prof = {k: v for k, v in profile.items() if not k.startswith("_")}
    prof.update(dtype="uint8", count=1, nodata=None, compress="deflate")
    with rasterio.open(out_path, "w", **prof) as dst:
        dst.write(mask.astype(np.uint8), 1)
    return out_path


def pixel_bbox_to_wgs84(bbox_rc, profile) -> Optional[List[float]]:
    """(row_min, col_min, row_max, col_max) -> [W, S, E, N] in EPSG:4326.

    Contract bboxes are always WGS84, so a scene in any other CRS is reprojected
    here rather than leaking projected metres into a contract file.
    """
    import rasterio
    from rasterio.warp import transform_bounds

    minr, minc, maxr, maxc = bbox_rc
    transform = profile["transform"]
    x0, y0 = transform * (minc, minr)
    x1, y1 = transform * (maxc, maxr)
    west, east = min(x0, x1), max(x0, x1)
    south, north = min(y0, y1), max(y0, y1)

    crs = profile.get("crs")
    if crs is not None and rasterio.crs.CRS.from_user_input(crs).to_epsg() != 4326:
        west, south, east, north = transform_bounds(crs, "EPSG:4326",
                                                    west, south, east, north)
    # A single-pixel-wide region would produce a degenerate bbox, which the
    # contract validator rejects. Nudge it to a hair's width instead of failing.
    eps = 1e-9
    if east <= west:
        east = west + eps
    if north <= south:
        north = south + eps
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    return [round(west, 6), round(south, 6), round(east, 6), round(north, 6)]


# --------------------------------------------------------------------------
# ML path
# --------------------------------------------------------------------------


def load_onnx(weights: Path):
    """Return (session, metadata) or None if the ML path is unavailable.

    Every failure here is non-fatal by design: no weights, no onnxruntime, a
    corrupt graph -- all of them fall through to the threshold engine.
    """
    if not weights.exists():
        return None
    try:
        import onnxruntime as ort
    except Exception:
        return None
    try:
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                     if p in ort.get_available_providers()]
        sess = ort.InferenceSession(str(weights), providers=providers)
        return sess, dict(sess.get_modelmeta().custom_metadata_map)
    except Exception as exc:
        # Deliberately non-fatal -- but say why. This silently returned None for
        # a perfectly good model because of a typo here, and the pipeline dutifully
        # ran the threshold fallback while reporting "no usable ONNX model".
        print(f"[detect] ONNX load failed for {weights.name}: "
              f"{type(exc).__name__}: {exc}")
        return None


def infer_tiled(sess, db: np.ndarray, valid: np.ndarray, cfg,
                threshold: Optional[float] = None, batch_size: int = 8) -> Tuple[np.ndarray, float]:
    """Tile the scene, run the segmenter, stitch the probabilities back.

    Tiles overlap and are averaged in the seams. Without overlap, every tile
    boundary shows as a straight edge in the mask -- the model has no context
    beyond the tile, so its prediction jumps discontinuously across the join.
    """
    from ml.config import db_to_model

    # Operating point comes from the config, which records the measured
    # sweep it was chosen from -- not a literal buried in the code.
    if threshold is None:
        threshold = cfg.tiling.detect_threshold

    tile = cfg.tiling.tile_size
    overlap = cfg.tiling.inference_overlap
    step = max(tile - overlap, 1)
    h, w = db.shape

    x = db_to_model(np.where(valid, db, cfg.sar.db_min), cfg).astype(np.float32)

    # Pad so the last tile in each direction is full-size.
    pad_h = max(0, -(-max(h - tile, 0) // step) * step + tile - h) if h > tile else tile - h
    pad_w = max(0, -(-max(w - tile, 0) // step) * step + tile - w) if w > tile else tile - w
    pad_h, pad_w = max(pad_h, 0), max(pad_w, 0)
    xp = np.pad(x, ((0, pad_h), (0, pad_w)), mode="reflect") if (pad_h or pad_w) else x
    H, W = xp.shape

    prob = np.zeros((H, W), np.float32)
    count = np.zeros((H, W), np.float32)

    coords, batch = [], []
    input_name = sess.get_inputs()[0].name

    def flush():
        if not batch:
            return
        arr = np.stack(batch)[:, None, :, :].astype(np.float32)
        logits = sess.run(None, {input_name: arr})[0]
        probs = 1.0 / (1.0 + np.exp(-logits))
        for (r, c), p in zip(coords, probs):
            prob[r:r + tile, c:c + tile] += p[0]
            count[r:r + tile, c:c + tile] += 1.0
        coords.clear()
        batch.clear()

    for r in range(0, max(H - tile, 0) + 1, step):
        for c in range(0, max(W - tile, 0) + 1, step):
            batch.append(xp[r:r + tile, c:c + tile])
            coords.append((r, c))
            if len(batch) >= batch_size:
                flush()
    flush()

    prob = np.divide(prob, np.maximum(count, 1e-6))[:h, :w]
    prob = np.where(valid, prob, 0.0)
    mask = (prob > threshold).astype(np.uint8)
    conf = float(prob[mask.astype(bool)].mean()) if mask.any() else 0.0
    return mask, conf


def screen_scene(db: np.ndarray, valid: np.ndarray, cfg, weights: Path,
                 conf: float = 0.25) -> Optional[dict]:
    """Stage 1: the DARTIS screening detector.

    Answers "is there oil here, or is this a look-alike?" before the segmenter
    is asked to delineate anything. Returns None when the model is unavailable,
    in which case detection proceeds unscreened -- a missing screen must not
    stop the pipeline, it just costs look-alike rejection.

    The detector was trained on 640x640 DARTIS patches rendered as 8-bit
    greyscale, so the scene is tiled the same way and each tile normalised with
    the shared dB constants. Feeding it a differently-scaled image would make
    its confidences meaningless.
    """
    if not Path(weights).exists():
        return None
    try:
        from ultralytics import YOLO as UltralyticsYOLO
    except Exception:
        return None

    from ml.config import db_to_uint8

    try:
        model = UltralyticsYOLO(str(weights))
    except Exception:
        return None

    tile = 640
    step = tile - 64
    h, w = db.shape
    grey = db_to_uint8(np.where(valid, db, cfg.sar.db_min), cfg)

    detections: List[dict] = []
    tiles_seen = 0
    for r in range(0, max(h - tile, 0) + 1, step) or [0]:
        for c in range(0, max(w - tile, 0) + 1, step) or [0]:
            patch = grey[r:r + tile, c:c + tile]
            if patch.shape != (tile, tile):
                # Pad with the scene's median backscatter, NOT zeros. In uint8
                # tile space 0 maps to db_min -- the darkest possible value --
                # so zero-padding paints a large black rectangle, which is
                # precisely what an oil detector is trained to fire on. Padding
                # with typical sea keeps the border uninteresting.
                fill = int(np.median(grey[valid])) if valid.any() else 128
                padded = np.full((tile, tile), fill, np.uint8)
                padded[:patch.shape[0], :patch.shape[1]] = patch
                patch = padded
            tiles_seen += 1
            rgb = np.stack([patch] * 3, axis=-1)
            try:
                res = model.predict(rgb, conf=conf, verbose=False)[0]
            except Exception:
                return None
            for b in res.boxes:
                x0, y0, x1, y1 = [float(v) for v in b.xyxy[0].tolist()]
                detections.append({
                    "bbox_rc": [int(r + y0), int(c + x0), int(r + y1), int(c + x1)],
                    "score": float(b.conf[0]),
                })

    return {"detections": detections, "tiles": tiles_seen,
            "n": len(detections),
            "max_score": max((d["score"] for d in detections), default=0.0)}


def regions_from_mask(mask: np.ndarray, db: np.ndarray) -> List[dict]:
    """Connected components of an ML mask, in the same shape the threshold
    engine reports, so both paths produce candidates identically."""
    from skimage.measure import label, regionprops

    out = []
    for r in regionprops(label(mask)):
        minr, minc, maxr, maxc = r.bbox
        out.append({
            "area_px": int(r.area),
            "bbox_rc": [int(minr), int(minc), int(maxr), int(maxc)],
            "elongation": float(r.axis_major_length / max(r.axis_minor_length, 1e-6)),
            "mean_drop_db": 0.0,
        })
    out.sort(key=lambda d: d["area_px"], reverse=True)
    return out


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def _boxes_overlap(a, b) -> float:
    """Intersection over the area of `a` (the segmented region).

    Asymmetric on purpose: the screen draws one loose box around a whole slick
    while the segmenter may split it into several components. Asking "how much
    of this region did the screen cover" answers the right question; plain IoU
    would reject every small piece of a correctly-screened slick.
    """
    ar0, ac0, ar1, ac1 = a
    br0, bc0, br1, bc1 = b
    ir = max(0, min(ar1, br1) - max(ar0, br0))
    ic = max(0, min(ac1, bc1) - max(ac0, bc0))
    area = max((ar1 - ar0) * (ac1 - ac0), 1)
    return (ir * ic) / area


def classify_regions(regions: List[dict], screen: Optional[dict],
                     min_overlap: float = 0.1) -> None:
    """Label each region oil or look-alike, in place, using the screening model.

    The screen is a single-class oil detector trained with 2,290 DARTIS
    look-alike patches as background, so its *silence* over a dark feature is a
    trained signal, not an absence of evidence: it saw the patch and declined to
    call it oil. That is what makes "lookalike" a measured label here rather
    than a guess.

    Two conditions must BOTH hold before silence counts as a rejection:

      1. the screen ran at all, and
      2. it fired somewhere in this scene.

    Condition 2 is not a formality. Measured on Trujillo Part III, the
    DARTIS-trained screen produces zero detections on scenes it has never seen
    the domain of -- oil, look-alike and clear water alike. Treating that
    blanket silence as a rejection labelled 100% of genuine oil regions as
    look-alikes. A detector with no recall on a scene has no opinion about it,
    and inventing one from its silence is worse than not classifying at all.

    Without a responsive screen every region stays "oil" -- the same
    conservative behaviour the threshold engine has always had.
    """
    responsive = bool(screen and screen.get("detections"))
    for r in regions:
        r["class"] = "oil"
        r["screen_score"] = None
        if not responsive:
            continue
        best = 0.0
        for det in screen["detections"]:
            ov = _boxes_overlap(r["bbox_rc"], det["bbox_rc"])
            if ov >= min_overlap and det["score"] > best:
                best = det["score"]
        if best > 0.0:
            r["screen_score"] = round(best, 4)
        else:
            # The oil detector looked here and stayed quiet.
            r["class"] = "lookalike"


def run_detection(db, valid, cfg, weights: Path, scene_db_range=None,
                  force_engine: Optional[str] = None,
                  screen_weights: Optional[Path] = None) -> DetectionOutcome:
    warnings: List[str] = []
    screen = None

    # The domain-gap guard. The model learnt one dB clip range; a scene
    # calibrated to another looks the same to the eye and silently wrecks
    # accuracy. Warn loudly rather than reporting a confident wrong mask.
    if scene_db_range is not None:
        want = [cfg.sar.db_min, cfg.sar.db_max]
        if [float(v) for v in scene_db_range] != want:
            warnings.append(
                f"scene db_range {list(scene_db_range)} != model range {want} "
                f"-- preprocessing mismatch, results are unreliable")

    # --- stage 1: screening ----------------------------------------------
    screen_path = Path(screen_weights or DEFAULT_SCREEN)
    if force_engine != "threshold_fallback":
        screen = screen_scene(db, valid, cfg, screen_path)
        if screen is None:
            warnings.append(f"screening model unavailable at {screen_path.name}; "
                            f"proceeding without look-alike rejection")
        else:
            warnings.append(f"screen: {screen['n']} oil candidate(s) over "
                            f"{screen['tiles']} tile(s), best {screen['max_score']:.2f}")

    # --- stage 2: delineation ---------------------------------------------
    if force_engine != "threshold_fallback":
        loaded = load_onnx(weights)
        if loaded is not None:
            sess, meta = loaded
            stamped = meta.get("config_fingerprint")
            if stamped and stamped != cfg.fingerprint:
                warnings.append(
                    f"model fingerprint {stamped} != config {cfg.fingerprint}; "
                    f"refusing the ML path")
            else:
                try:
                    t0 = time.time()
                    mask, conf = infer_tiled(sess, db, valid, cfg)
                    warnings.append(f"ml inference {time.time() - t0:.1f}s")
                    version = meta.get("model_version", "unet-r34-onnx")
                    if screen is not None:
                        version += "+yolo-screen"
                    regions = regions_from_mask(mask, db)
                    classify_regions(regions, screen)
                    n_look = sum(r["class"] == "lookalike" for r in regions)
                    if n_look:
                        warnings.append(
                            f"screen rejected {n_look}/{len(regions)} segmented "
                            f"region(s) as look-alike")
                    elif regions and screen is not None and not screen["detections"]:
                        warnings.append(
                            "screen returned no detections anywhere in this scene, "
                            "so look-alike rejection was NOT applied; candidates "
                            "are reported as oil without stage-1 confirmation")
                    return DetectionOutcome(mask, conf, "ml", version,
                                            regions, warnings, screen)
                except Exception as exc:
                    warnings.append(f"ML path failed ({type(exc).__name__}: {exc}); "
                                    f"falling back to threshold")
        elif force_engine == "ml":
            warnings.append("ML engine forced but unavailable; using threshold")
        else:
            warnings.append(f"no usable ONNX model at {weights}; using threshold")

    res = detect_threshold(db, ThresholdParams(), valid)
    warnings.extend(res.notes)
    # The screen still applies on the fallback path when it loaded: a threshold
    # mask is exactly the kind of output that needs look-alike rejection most.
    classify_regions(res.regions, screen)
    return DetectionOutcome(res.mask, res.confidence, "threshold_fallback",
                            "threshold-morphology-v1", res.regions, warnings,
                            screen)


def detect(scene_path: Path, scene_id: str, out_dir: Path,
           weights: Path = DEFAULT_WEIGHTS, scene_meta: Optional[dict] = None,
           force_engine: Optional[str] = None,
           screen_weights: Optional[Path] = None) -> DetectResponse:
    """Full `/detect` call: scene in, contract-valid DetectResponse out."""
    t0 = time.time()
    cfg = load_config()
    db, profile, valid = read_scene(Path(scene_path))

    outcome = run_detection(db, valid, cfg, Path(weights),
                            (scene_meta or {}).get("db_range"), force_engine,
                            screen_weights)

    mask_path = write_mask(outcome.mask, profile, Path(out_dir) / "raw_mask.tif")

    candidates: List[Candidate] = []
    for r in outcome.regions:
        bbox = pixel_bbox_to_wgs84(r["bbox_rc"], profile)
        if bbox is None:
            outcome.warnings.append(f"dropped a region with an out-of-range bbox: {r['bbox_rc']}")
            continue
        # Where the screen confirmed this region, its own confidence is the
        # honest per-candidate score; the scene-level segmentation confidence
        # says nothing about THIS region. phenomenon stays null because the
        # screen is single-class -- it can tell oil from not-oil, but naming
        # which look-alike this is would be invention.
        kind = r.get("class", "oil")
        score = r.get("screen_score")
        if score is None:
            score = min(0.99, outcome.confidence)
        candidates.append(Candidate(
            bbox=bbox,
            **{"class": kind},
            score=round(float(score), 4),
        ))

    response = DetectResponse(
        scene_id=scene_id,
        mask_path=str(mask_path).replace("\\", "/"),
        confidence=round(outcome.confidence, 4),
        candidates=candidates,
        model_version=outcome.model_version,
        engine=outcome.engine,
        runtime_ms=int((time.time() - t0) * 1000),
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "detect_response.json").write_text(
        response.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    if outcome.warnings:
        (out / "detect_warnings.json").write_text(
            json.dumps(outcome.warnings, indent=2), encoding="utf-8")
    return response


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--scene-id", default=None)
    ap.add_argument("--scene-meta", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "runs" / "detect")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--engine", choices=["auto", "ml", "threshold_fallback"], default="auto")
    args = ap.parse_args(argv)

    meta = json.loads(args.scene_meta.read_text()) if args.scene_meta else None
    scene_id = args.scene_id or (meta or {}).get("scene_id") or Path(args.scene).stem

    resp = detect(args.scene, scene_id, args.out, args.weights, meta,
                  None if args.engine == "auto" else args.engine)

    print(f"scene      : {args.scene}")
    print(f"engine     : {resp.engine.value}   model {resp.model_version}")
    print(f"confidence : {resp.confidence}")
    print(f"candidates : {len(resp.candidates)}")
    for c in resp.candidates[:5]:
        print(f"   {c.class_.value:<10s} score {c.score:.3f}  bbox {c.bbox}")
    print(f"mask       -> {resp.mask_path}")
    print(f"response   -> {args.out / 'detect_response.json'}   ({resp.runtime_ms} ms)")
    warn = args.out / "detect_warnings.json"
    if warn.exists():
        for w in json.loads(warn.read_text()):
            print(f"   note: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
