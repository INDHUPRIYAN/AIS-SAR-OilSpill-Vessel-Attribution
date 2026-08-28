"""Plan C: dependency-free oil-slick detection by adaptive thresholding.

No model, no GPU, no dataset, no network. This is the guaranteed detection path
from the fallback register -- if the ML weights are missing, the GPU is absent,
or ONNX Runtime will not load, `/detect` serves this instead and reports
`engine: "threshold_fallback"`. The pipeline never halts.

The physics it leans on: an oil film damps capillary/short-gravity waves, so a
slick returns markedly less radar energy than the surrounding sea. In Sigma0 dB
a slick is a dark patch, typically 5-10 dB below local background.

Doing that honestly needs a *local* threshold, not a global one. Sigma0 varies
across a scene with wind and incidence angle, so a single global cut either
misses slicks in calm regions or floods bright ones. Here the background is
estimated with a large median filter and each pixel is compared against its own
neighbourhood.

Speckle is the other hazard: SAR noise is multiplicative, so isolated dark
pixels are everywhere. Median pre-filtering plus morphological opening removes
them, and a minimum-area rule discards what survives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class ThresholdParams:
    """Tunable knobs. Defaults chosen to favour precision over recall: a
    spurious slick sends an investigator after an innocent ship, which is worse
    than missing a faint one that the ML path would catch anyway."""

    background_window: int = 51      # px; local background estimate, >> slick width
    speckle_window: int = 5          # px; median pre-filter
    min_area_px: int = 400           # discard blobs smaller than this
    max_area_fraction: float = 0.35  # a "slick" covering most of the scene is wind, not oil
    open_radius: int = 2             # morphological opening, kills speckle survivors
    close_radius: int = 3            # closing, fills interior gaps
    nodata_db: float = -999.0

    # --- look-alike rejection, without a model -----------------------------
    # Two cheap physical gates. A fresh discharge trails behind a moving vessel,
    # so it is dark AND elongated. The commonest look-alike -- a low-wind calm
    # patch -- is only weakly darker and roughly round.
    #
    # Calibrated on the synthetic mock scene in contracts/mocks/, where the
    # planted slick drops 11 dB with elongation 8.3 and the planted low-wind
    # look-alike drops 3.8 dB with elongation 1.0. Either gate alone separates
    # them (IoU 0.985, precision 1.000); both are on for redundancy.
    #
    # That is ONE synthetic scene. Real internal waves, biogenic films and rain
    # cells are far harder, which is exactly why the DARTIS screening model
    # exists. Treat these as sane defaults, not as validated thresholds.
    drop_db: float = 4.0             # how far below local background counts as slick
    min_elongation: float = 1.5      # major/minor axis ratio; 0 disables the gate


@dataclass
class ThresholdResult:
    mask: np.ndarray                       # uint8 {0,1}, same grid as input
    confidence: float                      # 0-1, heuristic (see _confidence)
    regions: List[dict] = field(default_factory=list)
    background_db: Optional[np.ndarray] = None
    notes: List[str] = field(default_factory=list)


def _median_filter(arr: np.ndarray, size: int) -> np.ndarray:
    from scipy.ndimage import median_filter as _mf
    return _mf(arr, size=size, mode="nearest")


def _uniform_filter(arr: np.ndarray, size: int) -> np.ndarray:
    from scipy.ndimage import uniform_filter
    return uniform_filter(arr, size=size, mode="nearest")


def _local_background(db: np.ndarray, valid: np.ndarray, window: int) -> np.ndarray:
    """Local background level in dB, ignoring invalid pixels.

    A plain box filter would drag the background down wherever land or nodata
    sits, so the mean is computed over valid pixels only (sum of values divided
    by count of contributors).
    """
    filled = np.where(valid, db, 0.0).astype(np.float32)
    weights = valid.astype(np.float32)
    num = _uniform_filter(filled, window)
    den = _uniform_filter(weights, window)
    with np.errstate(invalid="ignore", divide="ignore"):
        bg = np.where(den > 1e-6, num / den, np.nan)
    # Where no valid neighbours existed, fall back to the scene median.
    if np.isnan(bg).any():
        med = float(np.nanmedian(db[valid])) if valid.any() else 0.0
        bg = np.where(np.isnan(bg), med, bg)
    return bg


def _confidence(db: np.ndarray, mask: np.ndarray, background: np.ndarray) -> float:
    """Heuristic confidence: how deep and how clean the darkening is.

    Deliberately conservative and clearly not a calibrated probability -- the
    DetectResponse contract only promises 0-1, and the UI badges this path as a
    fallback. Reporting a confident number from a threshold rule would be a lie.
    """
    if not mask.any():
        return 0.0
    contrast = float(np.mean(background[mask.astype(bool)] - db[mask.astype(bool)]))
    # 3 dB -> ~0.35, 6 dB -> ~0.6, 10 dB -> ~0.8. Capped well below 1.0.
    return float(np.clip(contrast / 12.0, 0.0, 0.85))


def detect_threshold(
    db: np.ndarray,
    params: Optional[ThresholdParams] = None,
    valid_mask: Optional[np.ndarray] = None,
) -> ThresholdResult:
    """Detect dark slicks in a Sigma0 dB array.

    Args:
        db:         2-D float array of Sigma0 in dB.
        params:     tuning knobs.
        valid_mask: True where the pixel is usable sea. Land/nodata should be
                    False; those pixels can never be reported as slick.
    """
    from skimage import morphology
    from skimage.measure import label, regionprops

    p = params or ThresholdParams()
    notes: List[str] = []
    db = np.asarray(db, dtype=np.float32)

    if valid_mask is None:
        valid = np.isfinite(db) & (db > p.nodata_db + 1.0)
    else:
        valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(db)

    if not valid.any():
        return ThresholdResult(np.zeros(db.shape, np.uint8), 0.0,
                               notes=["no valid pixels in scene"])

    # Work on a speckle-suppressed copy; the median filter preserves slick edges
    # far better than a Gaussian would.
    work = np.where(valid, db, np.nan).astype(np.float32)
    filled = np.where(valid, db, float(np.nanmedian(work))).astype(np.float32)
    smooth = _median_filter(filled, p.speckle_window)

    background = _local_background(smooth, valid, p.background_window)
    darker_by = background - smooth
    candidate = (darker_by >= p.drop_db) & valid

    if not candidate.any():
        return ThresholdResult(np.zeros(db.shape, np.uint8), 0.0, [], background,
                               notes=[f"nothing {p.drop_db} dB below local background"])

    # Opening removes speckle survivors; closing then fills interior pinholes so
    # one slick is one region rather than a shower of fragments.
    cleaned = candidate
    if p.open_radius > 0:
        cleaned = morphology.binary_opening(cleaned, morphology.disk(p.open_radius))
    if p.close_radius > 0:
        cleaned = morphology.binary_closing(cleaned, morphology.disk(p.close_radius))

    labels = label(cleaned)
    total_valid = int(valid.sum())
    mask = np.zeros(db.shape, np.uint8)
    regions: List[dict] = []

    for r in regionprops(labels):
        if r.area < p.min_area_px:
            continue
        if r.area > p.max_area_fraction * total_valid:
            notes.append(
                f"discarded a {r.area}px region covering "
                f"{r.area / total_valid:.0%} of the scene -- that is a wind/calm "
                f"feature, not a slick")
            continue
        if p.min_elongation > 0:
            major = max(r.axis_major_length, 1e-6)
            minor = max(r.axis_minor_length, 1e-6)
            if major / minor < p.min_elongation:
                continue

        blob = labels == r.label
        mask[blob] = 1
        minr, minc, maxr, maxc = r.bbox
        regions.append({
            "area_px": int(r.area),
            "bbox_rc": [int(minr), int(minc), int(maxr), int(maxc)],
            "centroid_rc": [float(r.centroid[0]), float(r.centroid[1])],
            "mean_drop_db": float(np.mean(darker_by[blob])),
            "elongation": float(r.axis_major_length / max(r.axis_minor_length, 1e-6)),
            "orientation_rad": float(r.orientation),
        })

    regions.sort(key=lambda d: d["area_px"], reverse=True)
    if not regions:
        notes.append(f"all candidate regions were below {p.min_area_px}px")

    return ThresholdResult(
        mask=mask,
        confidence=_confidence(smooth, mask, background),
        regions=regions,
        background_db=background,
        notes=notes,
    )
