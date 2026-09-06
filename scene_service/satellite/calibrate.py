"""Radiometric calibration: Sentinel-1 IW GRDH DN -> Sigma0 dB GeoTIFF.

Closes the single biggest production gap in the scene service: the download
chain delivered raw digital numbers (DN) while detection expects a calibrated
Sigma0 dB raster clipped to the shared ``db_range`` in
``main_system/config/normalisation.yaml`` (the frozen training/inference
contract — never hardcode it).

Pipeline (all raster I/O is windowed; scenes are ~25000 x 16000 and are never
read whole):

1. **Radiometric calibration**  Parse the ``sigmaNought`` LUT from
   ``calibration-*.xml`` (calibrationVectorList: sample points per line and
   pixel), bilinearly interpolate it to the full raster grid per row block,
   then sigma0 = DN^2 / A^2.
2. **Thermal noise removal**  Parse ``noise-*.xml`` (``noiseRangeVectorList``
   on current products, ``noiseVectorList`` on pre-March-2018 ones such as the
   Chennai 2017 scene), interpolate the same way, and subtract the noise power
   from DN^2 before dividing: sigma0 = (DN^2 - eta) / A^2, floored at a small
   positive value before the log. Slicks are dark — exactly where thermal
   noise matters most.
3. **dB + clip**  10*log10(sigma0), clipped to [db_min, db_max] from
   normalisation.yaml.
4. **Geolocation**  GRD products carry GCPs (in the measurement TIFF, else the
   annotation ``geolocationGrid``). The Sigma0 raster is warped from those
   GCPs to EPSG:4326. Full Range-Doppler terrain correction with an SRTM DEM
   is deliberately out of scope: over ocean (flat, height ~0) the GCP warp is
   equivalent to terrain correction to within the GCP accuracy, which is the
   honest production-lite choice for a marine service. Do NOT reuse this
   module for land applications without adding a DEM.
5. **Land mask**  Land pixels -> NaN via the ``global-land-mask`` package
   (~1 km coastline raster). Without this, Chennai city detects as a "slick".
6. **Border noise**  The dark swath-edge artefact is trimmed with a simple,
   documented heuristic: per row, the leading and trailing run of columns
   whose DN is below a small threshold (default 20 DN) is masked, plus every
   DN == 0 pixel anywhere. Contiguous-from-the-edge trimming cannot eat an
   interior slick.

Outputs: ``scene_sigma0_db.tif`` (float32, nodata=NaN, tiled, compressed) and
``scene_meta.json`` written through ``SceneMetadata.to_contract()`` so
db_range, CRS and bbox are truthful.

The §6a domain-gap gate: ``check_domain_gap()`` compares the sea-only p1-p99
of a calibrated scene against the normalisation clip range. Because the
delivered raster is already clipped, "p1 == db_min" (>=1% of sea pixels pinned
at the clip floor) is exactly the condition "unclipped p1 below the range";
the gate therefore requires p1 and p99 to sit STRICTLY inside the range.
"""

import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Linear sigma0 floor applied after noise subtraction, before 10*log10.
# 1e-9 == -90 dB: far below any db_min in use, so it only guards the log.
POWER_FLOOR = 1e-9

# DN below this at the swath edges is treated as border noise (uint16 DN
# amplitude; open-ocean VV DN is typically 40-300, border artefact < ~25).
BORDER_DN_THRESHOLD = 20

# Rows per processing block. 1024 x 25303 float32 ~ 100 MB peak per array.
BLOCK_ROWS = 1024

DEFAULT_NORMALISATION_RELPATH = Path("main_system") / "config" / "normalisation.yaml"


# ---------------------------------------------------------------------------
# SAFE product discovery
# ---------------------------------------------------------------------------


@dataclass
class SafeFileSet:
    """Resolved file locations for one polarisation of an unpacked product."""

    safe_dir: Path
    scene_id: str
    polarisation: str
    measurement_tif: Path
    calibration_xml: Path
    noise_xml: Path
    annotation_xml: Optional[Path] = None


def _candidate_dirs(safe_dir: Path) -> List[Path]:
    """Directories that may hold measurement/annotation files.

    Handles both the standard SAFE layout (measurement/, annotation/,
    annotation/calibration/) and the flat COG-converted layout where every
    file sits beside an (empty) ``*.SAFE`` directory.
    """
    dirs = [safe_dir]
    for sub in ("measurement", "annotation", os.path.join("annotation", "calibration")):
        d = safe_dir / sub
        if d.is_dir():
            dirs.append(d)
    for child in sorted(safe_dir.iterdir()):
        if child.is_dir() and child.name.upper().endswith(".SAFE"):
            dirs.append(child)
            for sub in ("measurement", "annotation", os.path.join("annotation", "calibration")):
                d = child / sub
                if d.is_dir():
                    dirs.append(d)
    return dirs


def find_safe_files(safe_dir: Any, polarisation: str = "vv") -> SafeFileSet:
    """Locate the measurement GeoTIFF and its calibration/noise/annotation XMLs."""
    safe_dir = Path(safe_dir)
    if not safe_dir.is_dir():
        raise FileNotFoundError(f"SAFE directory not found: {safe_dir}")
    pol = polarisation.lower()
    tag = f"-{pol}-"

    measurement = calibration = noise = annotation = None
    for d in _candidate_dirs(safe_dir):
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            name = f.name.lower()
            if tag not in name:
                continue
            if name.endswith((".tif", ".tiff")) and not name.startswith(("calibration-", "noise-")):
                measurement = measurement or f
            elif name.endswith(".xml"):
                if name.startswith("calibration-"):
                    calibration = calibration or f
                elif name.startswith("noise-"):
                    noise = noise or f
                else:
                    annotation = annotation or f

    missing = [k for k, v in
               [("measurement .tiff", measurement), ("calibration-*.xml", calibration),
                ("noise-*.xml", noise)] if v is None]
    if missing:
        raise FileNotFoundError(
            f"Unpacked SAFE product at {safe_dir} is missing {missing} "
            f"for polarisation '{pol.upper()}'"
        )

    # Scene id: prefer the *.SAFE directory name (standard or flat layout).
    scene_id = safe_dir.name
    if safe_dir.name.upper().endswith(".SAFE"):
        scene_id = safe_dir.stem
    else:
        for child in sorted(safe_dir.iterdir()):
            if child.is_dir() and child.name.upper().endswith(".SAFE"):
                scene_id = child.stem
                break

    return SafeFileSet(
        safe_dir=safe_dir,
        scene_id=scene_id,
        polarisation=pol.upper(),
        measurement_tif=measurement,
        calibration_xml=calibration,
        noise_xml=noise,
        annotation_xml=annotation,
    )


# ---------------------------------------------------------------------------
# LUT parsing and bilinear interpolation
# ---------------------------------------------------------------------------


@dataclass
class Lut:
    """A per-line, per-pixel annotation LUT densified along the pixel axis.

    ``lines`` is the azimuth line of each calibration/noise vector (sorted,
    ascending); ``dense`` is shape (n_vectors, width): each vector's sparse
    pixel samples linearly interpolated onto the full pixel axis (edge values
    extended beyond the first/last sample, matching ESA's own convention).
    """

    lines: np.ndarray
    dense: np.ndarray

    def rows_for(self, row_indices: np.ndarray) -> np.ndarray:
        """Bilinear step 2: interpolate along the line axis for these rows.

        Combined with the per-vector pixel-axis interpolation done at parse
        time this is full bilinear interpolation of the sparse LUT grid.
        Returns shape (len(row_indices), width), float32.
        """
        rows = np.asarray(row_indices, dtype=np.float64)
        if len(self.lines) == 1:
            return np.repeat(self.dense, len(rows), axis=0).astype(np.float32)
        i1 = np.searchsorted(self.lines, rows, side="right")
        i1 = np.clip(i1, 1, len(self.lines) - 1)
        i0 = i1 - 1
        span = self.lines[i1] - self.lines[i0]
        w = np.clip((rows - self.lines[i0]) / np.where(span == 0, 1, span), 0.0, 1.0)
        out = (self.dense[i0] * (1.0 - w[:, None]) + self.dense[i1] * w[:, None])
        return out.astype(np.float32)


def _parse_vector_list(root: ET.Element, list_tags: List[str],
                       value_tags: List[str]) -> List[Tuple[int, np.ndarray, np.ndarray]]:
    """Extract (line, pixels, values) triples from an annotation vector list."""
    vec_list = None
    for tag in list_tags:
        vec_list = root.find(f".//{tag}")
        if vec_list is not None:
            break
    if vec_list is None:
        raise ValueError(f"None of {list_tags} found in annotation XML")

    vectors = []
    for vec in vec_list:
        line_el = vec.find("line")
        pixel_el = vec.find("pixel")
        value_el = None
        for vt in value_tags:
            value_el = vec.find(vt)
            if value_el is not None:
                break
        if line_el is None or pixel_el is None or value_el is None:
            continue
        line = int(float(line_el.text))
        pixels = np.array([float(x) for x in pixel_el.text.split()], dtype=np.float64)
        values = np.array([float(x) for x in value_el.text.split()], dtype=np.float64)
        if len(pixels) != len(values) or len(pixels) == 0:
            raise ValueError(
                f"LUT vector at line {line}: {len(pixels)} pixels vs {len(values)} values"
            )
        vectors.append((line, pixels, values))
    if not vectors:
        raise ValueError(f"Empty vector list {list_tags} in annotation XML")
    vectors.sort(key=lambda t: t[0])
    return vectors


def _densify(vectors: List[Tuple[int, np.ndarray, np.ndarray]], width: int) -> Lut:
    """Bilinear step 1: interpolate each vector's sparse samples onto 0..width-1."""
    xs = np.arange(width, dtype=np.float64)
    lines = np.array([v[0] for v in vectors], dtype=np.float64)
    dense = np.empty((len(vectors), width), dtype=np.float64)
    for i, (_line, pixels, values) in enumerate(vectors):
        dense[i] = np.interp(xs, pixels, values)  # edge-extended beyond samples
    return Lut(lines=lines, dense=dense)


def parse_calibration_lut(xml_path: Any, width: int) -> Lut:
    """Parse the sigmaNought calibration LUT (A in sigma0 = DN^2 / A^2)."""
    root = ET.parse(str(xml_path)).getroot()
    vectors = _parse_vector_list(
        root, ["calibrationVectorList"], ["sigmaNought"]
    )
    return _densify(vectors, width)


def parse_noise_lut(xml_path: Any, width: int) -> Lut:
    """Parse the thermal-noise LUT (eta, in DN-power units).

    Current products store range noise under ``noiseRangeVectorList`` /
    ``noiseRangeLut``; products generated before IPF 2.9 (March 2018) —
    including the Chennai 2017 scene — use ``noiseVectorList`` / ``noiseLut``.
    """
    root = ET.parse(str(xml_path)).getroot()
    vectors = _parse_vector_list(
        root,
        ["noiseRangeVectorList", "noiseVectorList"],
        ["noiseRangeLut", "noiseLut"],
    )
    return _densify(vectors, width)


def parse_ads_header(xml_path: Any) -> Dict[str, Any]:
    """Pull acquisition metadata out of an annotation adsHeader."""
    root = ET.parse(str(xml_path)).getroot()
    out: Dict[str, Any] = {}
    hdr = root.find(".//adsHeader")
    if hdr is not None:
        for k in ("missionId", "productType", "polarisation", "mode", "startTime", "stopTime"):
            el = hdr.find(k)
            if el is not None and el.text:
                out[k] = el.text.strip()
    return out


def parse_range_pixel_spacing(annotation_xml: Optional[Any]) -> Optional[float]:
    """Ground pixel size from the product annotation, if available."""
    if annotation_xml is None:
        return None
    try:
        root = ET.parse(str(annotation_xml)).getroot()
        el = root.find(".//imageAnnotation/imageInformation/rangePixelSpacing")
        if el is None:
            el = root.find(".//rangePixelSpacing")
        return float(el.text) if el is not None and el.text else None
    except Exception:  # metadata nicety only — never fail calibration over it
        return None


def parse_geolocation_gcps(annotation_xml: Any):
    """Fallback: build rasterio GCPs from the annotation geolocationGrid."""
    from rasterio.control import GroundControlPoint

    root = ET.parse(str(annotation_xml)).getroot()
    pts = root.findall(".//geolocationGridPointList/geolocationGridPoint")
    gcps = []
    for p in pts:
        try:
            gcps.append(
                GroundControlPoint(
                    row=float(p.find("line").text),
                    col=float(p.find("pixel").text),
                    x=float(p.find("longitude").text),
                    y=float(p.find("latitude").text),
                    z=float(p.find("height").text) if p.find("height") is not None else 0.0,
                )
            )
        except (AttributeError, TypeError, ValueError):
            continue
    return gcps


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def default_normalisation_path() -> Path:
    """Repo-root main_system/config/normalisation.yaml (frozen contract)."""
    return Path(__file__).resolve().parents[2] / DEFAULT_NORMALISATION_RELPATH


def load_db_range(normalisation_path: Optional[Any] = None) -> Tuple[float, float]:
    """Read [db_min, db_max] from the shared normalisation contract.

    Training and inference MUST share these numbers; this function is the only
    sanctioned way for the scene service to obtain them.
    """
    import yaml

    path = Path(normalisation_path) if normalisation_path else default_normalisation_path()
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sar = cfg["sar"]
    return float(sar["db_min"]), float(sar["db_max"])


# ---------------------------------------------------------------------------
# Per-block radiometry
# ---------------------------------------------------------------------------


def border_noise_mask(dn: np.ndarray, threshold: int = BORDER_DN_THRESHOLD) -> np.ndarray:
    """True where a pixel belongs to the leading/trailing swath-edge artefact.

    Per row: every column before the first DN > threshold and after the last
    DN > threshold is masked. Trimming only contiguous runs from the edges
    means an interior dark slick can never be eaten by this mask.
    """
    valid = dn > threshold
    any_valid = valid.any(axis=1)
    first = valid.argmax(axis=1)
    last = dn.shape[1] - 1 - valid[:, ::-1].argmax(axis=1)
    cols = np.arange(dn.shape[1])[None, :]
    mask = (cols < first[:, None]) | (cols > last[:, None])
    mask[~any_valid] = True
    return mask


def radiometric_block(
    dn: np.ndarray,
    cal_rows: np.ndarray,
    noise_rows: np.ndarray,
    db_min: float,
    db_max: float,
    power_floor: float = POWER_FLOOR,
    border_dn_threshold: int = BORDER_DN_THRESHOLD,
    clip: bool = True,
) -> np.ndarray:
    """DN block -> Sigma0 dB block (float32, NaN where invalid).

    sigma0_lin = max((DN^2 - eta) / A^2, power_floor);  dB = 10*log10(sigma0)

    ``clip=False`` yields the unclipped dB values — used only by the
    domain-gap audit, never for production rasters.
    """
    dn_f = dn.astype(np.float32)
    invalid = (dn <= 0) | border_noise_mask(dn, border_dn_threshold)
    power = dn_f * dn_f - noise_rows.astype(np.float32)
    sigma_lin = power / (cal_rows.astype(np.float32) ** 2)
    sigma_lin = np.maximum(sigma_lin, np.float32(power_floor))
    db = np.float32(10.0) * np.log10(sigma_lin, dtype=np.float32)
    if clip:
        np.clip(db, db_min, db_max, out=db)
    db[invalid] = np.nan
    return db


# ---------------------------------------------------------------------------
# Land mask
# ---------------------------------------------------------------------------


def land_mask_for_block(transform, height: int, width: int, row_off: int = 0) -> np.ndarray:
    """Boolean land mask (True = land) for a row block of an EPSG:4326 raster."""
    from global_land_mask import globe

    cols = np.arange(width)
    rows = np.arange(row_off, row_off + height)
    # Pixel-centre coordinates.
    lons = transform.c + (cols + 0.5) * transform.a
    lats = transform.f + (rows + 0.5) * transform.e
    lats = np.clip(lats, -89.999, 89.999)
    lons_wrapped = ((lons + 180.0) % 360.0) - 180.0
    lat2d, lon2d = np.meshgrid(lats, lons_wrapped, indexing="ij")
    return globe.is_land(lat2d, lon2d)


def apply_land_mask(sigma0_path: Any, block_rows: int = 512) -> int:
    """Set land pixels to NaN in-place. Returns the number of pixels masked."""
    import rasterio
    from rasterio.windows import Window

    n_masked = 0
    with rasterio.open(str(sigma0_path), "r+") as ds:
        for row0 in range(0, ds.height, block_rows):
            nrows = min(block_rows, ds.height - row0)
            win = Window(0, row0, ds.width, nrows)
            data = ds.read(1, window=win)
            land = land_mask_for_block(ds.transform, nrows, ds.width, row_off=row0)
            hit = land & np.isfinite(data)
            n_masked += int(hit.sum())
            data[land] = np.nan
            ds.write(data, 1, window=win)
    return n_masked


# ---------------------------------------------------------------------------
# Full-scene calibration
# ---------------------------------------------------------------------------


def calibrate_safe(
    safe_dir: Any,
    out_path: Any,
    polarisation: str = "vv",
    normalisation_path: Optional[Any] = None,
    land_mask: bool = True,
    clip: bool = True,
    block_rows: int = BLOCK_ROWS,
    border_dn_threshold: int = BORDER_DN_THRESHOLD,
    write_meta: bool = True,
    provider_used: str = "LocalCache",
    source: str = "real",
) -> Dict[str, Any]:
    """Calibrate one polarisation of an unpacked SAFE product end-to-end.

    Returns a stats dict: scene_id, out_path, bbox, shape, wall-time per
    stage, pixels land-masked, and the db_range applied.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import array_bounds
    from rasterio.warp import calculate_default_transform, reproject
    from rasterio.windows import Window

    t_start = time.perf_counter()
    files = find_safe_files(safe_dir, polarisation)
    db_min, db_max = load_db_range(normalisation_path)
    # Absolute path: file_path lands verbatim in the scene_meta.json contract,
    # whose consumers do not share this process's working directory.
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".radiometric.tmp.tif")

    stats: Dict[str, Any] = {
        "scene_id": files.scene_id,
        "polarisation": files.polarisation,
        "out_path": str(out_path),
        "db_range": [db_min, db_max],
        "clip": clip,
    }

    with rasterio.open(str(files.measurement_tif)) as src:
        width, height = src.width, src.height
        stats["shape"] = [height, width]
        gcps, gcp_crs = src.gcps
        if not gcps:
            if files.annotation_xml is None:
                raise ValueError(
                    f"{files.measurement_tif} has no GCPs and no annotation XML "
                    "was found to rebuild them from the geolocationGrid"
                )
            gcps = parse_geolocation_gcps(files.annotation_xml)
            gcp_crs = rasterio.crs.CRS.from_epsg(4326)
            if not gcps:
                raise ValueError(f"No GCPs recoverable for {files.measurement_tif}")

        cal_lut = parse_calibration_lut(files.calibration_xml, width)
        noise_lut = parse_noise_lut(files.noise_xml, width)

        profile = {
            "driver": "GTiff",
            "width": width,
            "height": height,
            "count": 1,
            "dtype": "float32",
            "nodata": float("nan"),
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
            "compress": "deflate",
            "predictor": 3,
            "BIGTIFF": "IF_SAFER",
        }
        t0 = time.perf_counter()
        with rasterio.open(str(tmp_path), "w", **profile) as dst:
            dst.gcps = (gcps, gcp_crs)
            for row0 in range(0, height, block_rows):
                nrows = min(block_rows, height - row0)
                win = Window(0, row0, width, nrows)
                dn = src.read(1, window=win)
                rows_idx = np.arange(row0, row0 + nrows)
                db = radiometric_block(
                    dn,
                    cal_lut.rows_for(rows_idx),
                    noise_lut.rows_for(rows_idx),
                    db_min,
                    db_max,
                    border_dn_threshold=border_dn_threshold,
                    clip=clip,
                )
                dst.write(db, 1, window=win)
        stats["t_radiometric_s"] = round(time.perf_counter() - t0, 1)

        # ------------------------------------------------------------------
        # GCP warp to EPSG:4326 (see module docstring for the terrain-
        # correction scope note).
        # ------------------------------------------------------------------
        t0 = time.perf_counter()
        dst_crs = "EPSG:4326"
        dst_transform, dst_w, dst_h = calculate_default_transform(
            gcp_crs, dst_crs, width, height, gcps=gcps
        )
        out_profile = dict(profile, width=dst_w, height=dst_h,
                           crs=dst_crs, transform=dst_transform)
        with rasterio.open(str(tmp_path)) as rsrc:
            with rasterio.open(str(out_path), "w", **out_profile) as dst:
                reproject(
                    source=rasterio.band(rsrc, 1),
                    destination=rasterio.band(dst, 1),
                    src_crs=gcp_crs,
                    gcps=gcps,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    src_nodata=float("nan"),
                    dst_nodata=float("nan"),
                    resampling=Resampling.bilinear,
                    warp_mem_limit=256,
                    num_threads=2,
                )
        stats["t_warp_s"] = round(time.perf_counter() - t0, 1)

    try:
        os.remove(tmp_path)
    except OSError:
        pass

    if land_mask:
        t0 = time.perf_counter()
        stats["land_pixels_masked"] = apply_land_mask(out_path)
        stats["t_land_mask_s"] = round(time.perf_counter() - t0, 1)

    left, bottom, right, top = array_bounds(dst_h, dst_w, dst_transform)
    bbox = [round(left, 6), round(bottom, 6), round(right, 6), round(top, 6)]
    stats["bbox"] = bbox

    hdr = parse_ads_header(files.calibration_xml)
    acquired = hdr.get("startTime")
    if acquired:
        acq_dt = datetime.fromisoformat(acquired).replace(tzinfo=timezone.utc)
    else:  # extremely defensive; adsHeader is mandatory in the spec
        acq_dt = datetime.now(timezone.utc)

    if write_meta:
        from .models import SceneMetadata

        meta = SceneMetadata(
            scene_id=files.scene_id,
            platform=f"Sentinel-1{hdr.get('missionId', 'S1?')[-1]}"
            if hdr.get("missionId") else "Sentinel-1",
            acquisition_time=acq_dt,
            bbox=bbox,
            product_type=hdr.get("productType", "GRD"),
            polarisation=files.polarisation,
            file_path=str(out_path),
            crs="EPSG:4326",
            db_range=[db_min, db_max],
            provider_used=provider_used,
            source=source,
            pixel_spacing_m=parse_range_pixel_spacing(files.annotation_xml),
        )
        meta_path = out_path.parent / "scene_meta.json"
        meta.write_contract(meta_path)
        stats["meta_path"] = str(meta_path)

    stats["t_total_s"] = round(time.perf_counter() - t_start, 1)
    logger.info("Calibrated %s -> %s in %.1fs", files.scene_id, out_path, stats["t_total_s"])
    return stats


# ---------------------------------------------------------------------------
# §6a domain-gap gate
# ---------------------------------------------------------------------------


def sea_histogram(sigma0_path: Any, bin_edges: Optional[np.ndarray] = None,
                  block_rows: int = 1024) -> Tuple[np.ndarray, np.ndarray, int]:
    """Histogram of finite (sea, since land is NaN) Sigma0 dB pixels.

    Streamed row-block accumulation — never loads the scene whole.
    Returns (counts, bin_edges, n_finite).
    """
    import rasterio
    from rasterio.windows import Window

    if bin_edges is None:
        bin_edges = np.linspace(-90.0, 30.0, 1201)  # 0.1 dB bins
    counts = np.zeros(len(bin_edges) - 1, dtype=np.int64)
    n_finite = 0
    with rasterio.open(str(sigma0_path)) as ds:
        for row0 in range(0, ds.height, block_rows):
            nrows = min(block_rows, ds.height - row0)
            data = ds.read(1, window=Window(0, row0, ds.width, nrows))
            finite = data[np.isfinite(data)]
            n_finite += finite.size
            if finite.size:
                counts += np.histogram(finite, bins=bin_edges)[0]
    return counts, bin_edges, n_finite


def _percentile_from_hist(counts: np.ndarray, edges: np.ndarray, q: float) -> float:
    """Percentile (0-100) from histogram counts via the bin-centre CDF."""
    total = counts.sum()
    if total == 0:
        return float("nan")
    cum = np.cumsum(counts)
    target = q / 100.0 * total
    idx = int(np.searchsorted(cum, target))
    idx = min(idx, len(counts) - 1)
    centres = (edges[:-1] + edges[1:]) / 2.0
    return float(centres[idx])


def check_domain_gap(
    sigma0_path: Any,
    normalisation_path: Optional[Any] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """§6a gate: does this scene's sea backscatter fit the training clip range?

    PASS requires p1 and p99 STRICTLY inside (db_min, db_max): on an
    already-clipped raster, p1 == db_min means >= 1% of sea pixels were pinned
    at the clip floor, i.e. the unclipped p1 lies outside the range — a
    domain gap that would force the team to re-tile the training data.
    """
    db_min, db_max = load_db_range(normalisation_path)
    counts, edges, n = sea_histogram(sigma0_path)
    p1 = _percentile_from_hist(counts, edges, 1.0)
    p99 = _percentile_from_hist(counts, edges, 99.0)
    p50 = _percentile_from_hist(counts, edges, 50.0)
    # 0.1 dB bin width: a value pinned exactly at db_min lands in the bin
    # containing db_min, so compare with half-bin tolerance.
    half_bin = (edges[1] - edges[0]) / 2.0
    inside = (p1 > db_min + half_bin) and (p99 < db_max - half_bin)
    frac_at_min = float(counts[edges[:-1] <= db_min].sum() / max(counts.sum(), 1))
    frac_at_max = float(counts[edges[1:] >= db_max].sum() / max(counts.sum(), 1))
    result = {
        "sigma0_path": str(sigma0_path),
        "n_sea_pixels": int(n),
        "p1_db": round(p1, 2),
        "p50_db": round(p50, 2),
        "p99_db": round(p99, 2),
        "db_min": db_min,
        "db_max": db_max,
        "frac_at_or_below_db_min": round(frac_at_min, 4),
        "frac_at_or_above_db_max": round(frac_at_max, 4),
        "verdict": "PASS" if inside else "DOMAIN GAP",
    }
    if verbose:
        print(
            f"[6a gate] {Path(str(sigma0_path)).name}: sea p1={result['p1_db']} dB, "
            f"p50={result['p50_db']} dB, p99={result['p99_db']} dB "
            f"vs normalisation clip [{db_min}, {db_max}] dB "
            f"({n:,} sea pixels; {frac_at_min:.1%} at/below floor, "
            f"{frac_at_max:.1%} at/above ceiling)"
        )
        print(f"[6a gate] verdict: {result['verdict']}")
    return result
