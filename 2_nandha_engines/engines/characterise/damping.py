"""Backscatter damping ratio: how much darker the slick is than the sea around it.

Oil films flatten the short capillary waves that generate radar backscatter, so a slick
returns less energy than the sea beside it. The gap, in dB, is the damping ratio -
handbook §4.2's ``damping_ratio_db`` and one of the two inputs to the age proxy.

Reference sea level is sampled from a ring buffer around each slick rather than from
the whole scene, because backscatter varies with local wind. The ring is built from a
Euclidean distance transform (one pass) instead of a morphological dilation with a
large disk, which would be far slower over a full scene.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_edt

# Below this many usable ring pixels the sea reference is too thin to trust.
MIN_RING_PIXELS = 100


@dataclass(frozen=True)
class DampingResult:
    damping_db: float
    slick_db: float
    sea_db: float
    ring_pixels: int


def compute_damping(
    db: np.ndarray,
    component: np.ndarray,
    all_oil: np.ndarray,
    *,
    ring_inner_px: float = 10.0,
    ring_width_px: float = 30.0,
    sea_percentile: float = 50.0,
    nodata: float | None = None,
) -> tuple[DampingResult | None, list[str]]:
    """Damping ratio for one slick, in dB (positive = slick darker than the sea).

    ``component`` is this slick's pixels; ``all_oil`` is every oil pixel in the scene,
    used to keep *other* slicks out of the sea reference - a neighbouring slick in the
    ring would drag the sea level down and understate the damping.

    Returns ``(result, warnings)``; ``result`` is None when no usable ring exists (the
    slick runs off the edge of the scene, or the dB band is all nodata there).
    """
    warnings: list[str] = []

    valid = np.isfinite(db)
    if nodata is not None:
        valid &= db != nodata
    if not valid.any():
        return None, ["dB band contains no valid pixels; damping ratio omitted"]

    # Work in a crop around the slick: the ring can never extend past this margin.
    pad = int(math.ceil(ring_inner_px + ring_width_px)) + 2
    rows, cols = np.nonzero(component)
    r0, r1 = max(0, rows.min() - pad), min(component.shape[0], rows.max() + pad + 1)
    c0, c1 = max(0, cols.min() - pad), min(component.shape[1], cols.max() + pad + 1)

    sub_comp = component[r0:r1, c0:c1]
    sub_all = all_oil[r0:r1, c0:c1]
    sub_db = db[r0:r1, c0:c1]
    sub_valid = valid[r0:r1, c0:c1]

    # Distances in pixels: to this slick, and to any oil at all.
    dist_comp = distance_transform_edt(~sub_comp)
    dist_any = distance_transform_edt(~sub_all)

    ring = (
        (dist_comp > ring_inner_px)
        & (dist_comp <= ring_inner_px + ring_width_px)
        & (dist_any > ring_inner_px)      # keep other slicks out of the reference
        & sub_valid
    )

    ring_px = int(ring.sum())
    if ring_px == 0:
        return None, ["no clear sea ring around the slick; damping ratio omitted"]
    if ring_px < MIN_RING_PIXELS:
        warnings.append(
            f"sea reference ring has only {ring_px} usable pixels; "
            "damping ratio is weakly constrained"
        )

    inside = sub_comp & sub_valid
    if not inside.any():
        return None, ["slick has no valid dB pixels; damping ratio omitted"]

    # Medians, not means: a ship or a bright wave patch in the ring must not move the
    # sea reference, and speckle inside the slick must not move the slick level.
    slick_db = float(np.median(sub_db[inside]))
    sea_db = float(np.percentile(sub_db[ring], sea_percentile))

    return (
        DampingResult(
            damping_db=sea_db - slick_db,
            slick_db=slick_db,
            sea_db=sea_db,
            ring_pixels=ring_px,
        ),
        warnings,
    )
