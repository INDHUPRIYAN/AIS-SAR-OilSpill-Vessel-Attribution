"""Spatial + temporal index over the AIS archive (design doc v2 §8, §12, §27·4).

§8 states the requirement plainly:

    "The spatial index is mandatory for production. A full scan over 20 mock
    vessels is instant; over a real coastal month it is minutes per
    attribution. Partition by region + time, index on (geom, timestamp_utc),
    and query the buffered origin cloud directly."

**Backend choice.** §12 lists two options for AIS position reports --
"TimescaleDB / partitioned Parquet". This module implements the second, for the
same reason §28 rejects Kubernetes: the demo must boot with zero credentials
and no server process, and a half-wired PostGIS is worse than a clean local
store plus a credible migration path. The query surface below is deliberately
the one a PostGIS-backed store would expose (bbox+time, and buffered-polygon
containment), so swapping the backend is a new class, not a rewrite.

**Layout.** ``<root>/region=<region>/<bucket>/part.parquet`` where ``bucket`` is
``date=YYYY-MM-DD`` (default) or ``hour=YYYY-MM-DDTHH`` for dense regions. Every
partition file is contract-shaped (``contracts.schemas.tabular.VESSEL_COLUMNS``,
14 columns, sorted by ``(mmsi, timestamp_utc)``), so any partition can be handed
to attribution directly.

**The index itself** is ``<root>/_index.json``: one record per partition holding
its bbox, its true time span, row/MMSI counts and a content hash. That record is
what makes the store an index rather than a directory -- a query reads it, prunes
to the handful of partitions that can possibly match, and never opens the rest.

Three levels of pruning, cheapest first:

1. **Partition prune** -- region + bucket from the manifest bbox and time span.
   A month of Danish AIS is ~30 partitions; a 6-hour origin window touches one
   or two. This is the step that turns minutes into milliseconds.
2. **Column prune** -- only the requested columns come off disk.
3. **Fine pass** -- exact bbox/time test in memory, and for
   :meth:`AISStore.query_cloud` a shapely ``STRtree`` over the surviving points
   queried with the buffered origin polygons. Point-in-polygon over 10^5 points
   is milliseconds with the tree and tens of seconds without.

Nothing here mutates a written partition in place: ``ingest`` of an existing
bucket merges and rewrites, and the manifest records the new content hash. The
immutability rule in §12 applies to *run artefacts*, not to this rolling archive.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from .contract import to_contract

log = logging.getLogger(__name__)

INDEX_FILENAME = "_index.json"
INDEX_VERSION = 1
PART_FILENAME = "part.parquet"

#: Mean metres per degree of latitude on the WGS84 ellipsoid. Longitude scales
#: by cos(latitude); see :func:`_buffer_degrees`.
_M_PER_DEG_LAT = 111_320.0

Bbox = Tuple[float, float, float, float]        # lon_min, lat_min, lon_max, lat_max


class IndexError_(RuntimeError):
    """Raised when the store is structurally unusable (bad manifest, bad path)."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _utc(value) -> pd.Timestamp:
    """Parse anything datetime-ish to a tz-aware UTC pandas Timestamp.

    Naive input is *assumed* UTC rather than local -- Standing Rule 1: "IST
    leaking in shifts the origin window 5.5 h and blames the wrong ship."
    """
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _iso(ts: pd.Timestamp) -> str:
    return _utc(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _normalise_region(region: str) -> str:
    """Region names become path segments, so keep them boring and portable."""
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "-"
                      for c in str(region).strip().lower())
    cleaned = "-".join(p for p in cleaned.split("-") if p)
    if not cleaned:
        raise ValueError("region must contain at least one alphanumeric character")
    return cleaned


def _bucket_key(ts: pd.Timestamp, granularity: str) -> str:
    ts = _utc(ts)
    if granularity == "day":
        return f"date={ts.strftime('%Y-%m-%d')}"
    if granularity == "hour":
        return f"hour={ts.strftime('%Y-%m-%dT%H')}"
    raise ValueError(f"granularity must be 'day' or 'hour', got {granularity!r}")


def _bbox_overlaps(a: Bbox, b: Bbox) -> bool:
    """Axis-aligned overlap test. Both boxes are (lon_min, lat_min, lon_max, lat_max).

    Antimeridian-crossing boxes are not supported: a query spanning +/-180 must
    be issued as two queries. Raising there would be worse than saying so --
    every AOI in ``config/aois.yaml`` is well inside one hemisphere.
    """
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _buffer_degrees(buffer_km: float, lat0: float) -> Tuple[float, float]:
    """(d_lon, d_lat) degrees equivalent to ``buffer_km`` at latitude ``lat0``.

    Buffering a lon/lat geometry by one degree value is wrong away from the
    equator, and wrong in the dangerous direction. One degree of longitude is
    111 km at the equator but only 63 km at 55.5 deg N, so a buffer of
    ``buffer_km/111.32`` degrees applied to both axes reaches the stated
    distance north-south and only ``cos(lat)`` of it east-west -- 57 % in Danish
    waters. The suspect net is quietly narrower than the number in the UI says,
    and the vessels it misses are never reported as missed.

    So longitude gets its own, larger degree buffer. Accurate to well under a
    percent over the few-hundred-km spans a drift cloud covers.
    """
    d_lat = buffer_km * 1000.0 / _M_PER_DEG_LAT
    cos_lat = max(math.cos(math.radians(float(lat0))), 1e-6)
    return d_lat / cos_lat, d_lat


def _read_partition(path: Path, columns: Optional[Sequence[str]]) -> pd.DataFrame:
    try:
        return pd.read_parquet(path, columns=list(columns) if columns else None)
    except Exception as exc:                                    # pragma: no cover
        raise IndexError_(f"unreadable partition {path}: {type(exc).__name__}: {exc}")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON via a temp file + replace, so a crash never truncates the index."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)
    except Exception:                                           # pragma: no cover
        Path(tmp).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------
# manifest records
# --------------------------------------------------------------------------


@dataclass
class Partition:
    """One indexed partition: where it is, what it covers, and what it holds."""

    region: str
    bucket: str                 # "date=2026-08-30" | "hour=2026-08-30T14"
    path: str                   # relative to the store root, forward slashes
    rows: int
    mmsi_count: int
    bbox: Bbox
    t_start: str
    t_end: str
    sha256: str
    bytes: int
    sources: List[str] = field(default_factory=list)   # 'real' / 'synthetic'

    def to_dict(self) -> dict:
        return {
            "region": self.region, "bucket": self.bucket, "path": self.path,
            "rows": self.rows, "mmsi_count": self.mmsi_count,
            "bbox": [float(v) for v in self.bbox],
            "t_start": self.t_start, "t_end": self.t_end,
            "sha256": self.sha256, "bytes": self.bytes,
            "sources": sorted(self.sources),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Partition":
        return cls(
            region=d["region"], bucket=d["bucket"], path=d["path"],
            rows=int(d["rows"]), mmsi_count=int(d.get("mmsi_count", 0)),
            bbox=tuple(float(v) for v in d["bbox"]),          # type: ignore[arg-type]
            t_start=d["t_start"], t_end=d["t_end"],
            sha256=d.get("sha256", ""), bytes=int(d.get("bytes", 0)),
            sources=list(d.get("sources", [])),
        )

    def matches(self, bbox: Optional[Bbox], start: pd.Timestamp,
                end: pd.Timestamp) -> bool:
        """Could this partition contain a row satisfying the query? (never a false negative)"""
        if _utc(self.t_end) < start or _utc(self.t_start) > end:
            return False
        if bbox is not None and not _bbox_overlaps(self.bbox, bbox):
            return False
        return True


@dataclass
class IngestReport:
    """What one :meth:`AISStore.ingest` call actually did. Printed by the CLI."""

    region: str
    rows_in: int
    rows_written: int
    rows_dropped_contract: int
    partitions_written: int
    partitions_merged: int
    duplicates_dropped: int
    t_start: Optional[str]
    t_end: Optional[str]
    bbox: Optional[Bbox]

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        if self.bbox is not None:
            d["bbox"] = [float(v) for v in self.bbox]
        return d


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------


class AISStore:
    """A partitioned, indexed AIS archive on the local filesystem.

    >>> store = AISStore("data/ais/store")
    >>> store.ingest(df, region="denmark")                    # doctest: +SKIP
    >>> store.query(bbox=(11.0, 55.0, 12.0, 56.0),
    ...             start="2026-02-01T00:00:00Z",
    ...             end="2026-02-01T06:00:00Z")               # doctest: +SKIP
    """

    def __init__(self, root: Union[str, Path], granularity: str = "day"):
        if granularity not in ("day", "hour"):
            raise ValueError(f"granularity must be 'day' or 'hour', got {granularity!r}")
        self.root = Path(root)
        self.granularity = granularity
        self._partitions: Optional[List[Partition]] = None

    # -- manifest ---------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_FILENAME

    def partitions(self, refresh: bool = False) -> List[Partition]:
        """The manifest, loaded once and cached. Missing manifest == empty store."""
        if self._partitions is None or refresh:
            self._partitions = self._load_manifest()
        return self._partitions

    def _load_manifest(self) -> List[Partition]:
        if not self.index_path.exists():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise IndexError_(
                f"{self.index_path} is not readable JSON ({type(exc).__name__}); "
                f"run `rebuild_manifest()` to regenerate it from the partitions")
        if int(payload.get("version", 0)) != INDEX_VERSION:
            raise IndexError_(
                f"index version {payload.get('version')} != {INDEX_VERSION}; "
                f"run `rebuild_manifest()`")
        return [Partition.from_dict(p) for p in payload.get("partitions", [])]

    def _save_manifest(self, partitions: List[Partition]) -> None:
        partitions = sorted(partitions, key=lambda p: (p.region, p.bucket))
        _atomic_write_json(self.index_path, {
            "version": INDEX_VERSION,
            "generated_utc": _iso(pd.Timestamp.now(tz="UTC")),
            "granularity": self.granularity,
            "partitions": [p.to_dict() for p in partitions],
        })
        self._partitions = partitions

    def rebuild_manifest(self) -> List[Partition]:
        """Re-derive the index by opening every partition. The repair path.

        Costs one full read of the archive, so it is not something a query does;
        it exists because an index that cannot be regenerated from the data is a
        single point of failure.
        """
        found: List[Partition] = []
        for path in sorted(self.root.glob(f"region=*/*/{PART_FILENAME}")):
            rel = path.relative_to(self.root)
            region = rel.parts[0].split("=", 1)[1]
            bucket = rel.parts[1]
            df = _read_partition(path, None)
            if df.empty:
                log.warning("dropping empty partition %s", rel)
                continue
            found.append(self._describe(df, region, bucket, path))
        self._save_manifest(found)
        return found

    def _describe(self, df: pd.DataFrame, region: str, bucket: str,
                  path: Path) -> Partition:
        ts = pd.to_datetime(df["timestamp_utc"], utc=True)
        return Partition(
            region=region, bucket=bucket,
            path=str(path.relative_to(self.root)).replace("\\", "/"),
            rows=int(len(df)), mmsi_count=int(df["mmsi"].nunique()),
            bbox=(float(df["lon"].min()), float(df["lat"].min()),
                  float(df["lon"].max()), float(df["lat"].max())),
            t_start=_iso(ts.min()), t_end=_iso(ts.max()),
            sha256=_sha256(path), bytes=path.stat().st_size,
            sources=sorted(set(df["source"].astype(str).unique())),
        )

    # -- write ------------------------------------------------------------

    def ingest(self, df: pd.DataFrame, region: str) -> IngestReport:
        """Add a contract-shaped (or coercible) frame to the store.

        The frame is projected onto the frozen contract first, so a caller
        cannot smuggle raw archive columns into the store. Rows land in the
        partition their own timestamp selects, which means one CSV spanning
        midnight correctly produces two partitions.

        Re-ingesting a bucket merges rather than clobbers: existing rows are
        read back, concatenated, de-duplicated on ``(mmsi, timestamp_utc)`` --
        last write wins, so a corrected re-ingest supersedes an earlier one --
        and the file is rewritten with a fresh content hash.
        """
        region = _normalise_region(region)
        rows_in = int(len(df)) if df is not None else 0
        shaped = to_contract(df)
        dropped = rows_in - int(len(shaped))
        if dropped:
            log.warning("ingest(%s): %d/%d rows failed the contract and were dropped",
                        region, dropped, rows_in)

        if shaped.empty:
            return IngestReport(region=region, rows_in=rows_in, rows_written=0,
                                rows_dropped_contract=dropped, partitions_written=0,
                                partitions_merged=0, duplicates_dropped=0,
                                t_start=None, t_end=None, bbox=None)

        ts = pd.to_datetime(shaped["timestamp_utc"], utc=True)
        buckets = ts.map(lambda t: _bucket_key(t, self.granularity))

        by_key = {p.path: p for p in self.partitions()}
        written = merged = dupes = 0
        rows_written = 0

        for bucket, part in shaped.groupby(buckets, sort=True):
            path = self.root / f"region={region}" / str(bucket) / PART_FILENAME
            rel = str(path.relative_to(self.root)).replace("\\", "/")

            if path.exists():
                before = len(part)
                existing = _read_partition(path, None)
                # `part` last so a corrected re-ingest wins the de-dup.
                part = pd.concat([existing, part], ignore_index=True)
                part = part.drop_duplicates(subset=["mmsi", "timestamp_utc"],
                                            keep="last")
                dupes += len(existing) + before - len(part)
                merged += 1
            else:
                written += 1

            part = to_contract(part)          # re-sort and re-assert dtypes
            path.parent.mkdir(parents=True, exist_ok=True)
            part.to_parquet(path, index=False)
            rows_written += int(len(part))
            by_key[rel] = self._describe(part, region, str(bucket), path)

        self._save_manifest(list(by_key.values()))
        return IngestReport(
            region=region, rows_in=rows_in, rows_written=rows_written,
            rows_dropped_contract=dropped, partitions_written=written,
            partitions_merged=merged, duplicates_dropped=int(dupes),
            t_start=_iso(ts.min()), t_end=_iso(ts.max()),
            bbox=(float(shaped["lon"].min()), float(shaped["lat"].min()),
                  float(shaped["lon"].max()), float(shaped["lat"].max())),
        )

    # -- read -------------------------------------------------------------

    def query(self, bbox: Optional[Bbox], start, end,
              region: Optional[str] = None,
              columns: Optional[Sequence[str]] = None,
              mmsi: Optional[Iterable[int]] = None) -> pd.DataFrame:
        """Rows inside ``bbox`` during ``[start, end]``, contract-shaped.

        ``bbox`` is ``(lon_min, lat_min, lon_max, lat_max)`` in WGS84 -- lon
        first, per Standing Rule 1 -- or ``None`` for "anywhere in this region".
        Bounds are inclusive at both ends: an origin window is a closed interval
        and a vessel transmitting exactly at ``start`` is squarely a candidate.

        Returns an empty contract-shaped frame (right columns, right dtypes)
        when nothing matches, so callers never branch on ``None``.
        """
        start_ts, end_ts = _utc(start), _utc(end)
        if end_ts < start_ts:
            raise ValueError(f"end {_iso(end_ts)} is before start {_iso(start_ts)}")
        want_region = _normalise_region(region) if region else None

        hits = [p for p in self.partitions()
                if (want_region is None or p.region == want_region)
                and p.matches(bbox, start_ts, end_ts)]
        log.debug("query pruned %d partitions to %d",
                  len(self.partitions()), len(hits))
        if not hits:
            return to_contract(None)

        cols = list(columns) if columns else None
        if cols:      # the predicate columns must be read even if not requested
            cols = list(dict.fromkeys(cols + ["timestamp_utc", "lat", "lon", "mmsi"]))

        frames = []
        for p in hits:
            path = self.root / p.path
            if not path.exists():
                log.warning("index references missing partition %s "
                            "(run rebuild_manifest)", p.path)
                continue
            frames.append(_read_partition(path, cols))
        if not frames:
            return to_contract(None)

        df = pd.concat(frames, ignore_index=True)
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        keep = df["timestamp_utc"].between(start_ts, end_ts)
        if bbox is not None:
            lon_min, lat_min, lon_max, lat_max = bbox
            keep &= (df["lon"].between(lon_min, lon_max)
                     & df["lat"].between(lat_min, lat_max))
        if mmsi is not None:
            keep &= df["mmsi"].isin(list(mmsi))
        df = df[keep]
        if columns:
            return df[[c for c in columns if c in df.columns]].reset_index(drop=True)
        return to_contract(df)

    def query_cloud(self, cloud, start, end, buffer_km: float = 5.0,
                    region: Optional[str] = None,
                    whole_track: bool = True) -> pd.DataFrame:
        """Vessels that entered a buffered drift origin cloud during a window.

        This is the query §8 asks for by name -- "query the buffered origin
        cloud directly" -- and the one Stage 6 actually issues.

        ``cloud`` is an ``origin_cloud.geojson`` path, a parsed GeoJSON dict, or
        a shapely geometry. Every geometry in it is buffered by ``buffer_km``
        (longitude scaled by cos(lat), see :func:`_buffer_degrees`) and unioned.
        The partition prune runs off the buffered bbox; the surviving points go
        into a shapely ``STRtree`` and the union is queried against it.

        With ``whole_track`` (the default) every fix a matching MMSI transmitted
        during the window is returned, including the ones far outside the cloud.
        Attribution scores trajectory and course change, which need the approach
        and the departure legs; handing it only the in-cloud fixes would silently
        flatten the trajectory factor. That costs a second, bbox-free query over
        the same time-pruned partitions -- cheap, because the MMSI set is small
        and the partitions are already known.
        """
        from shapely import STRtree, points as shapely_points
        from shapely.affinity import scale as shapely_scale
        from shapely.ops import unary_union

        geoms = _cloud_geometries(cloud)
        if not geoms:
            log.warning("query_cloud: cloud contains no geometries")
            return to_contract(None)

        union = unary_union(geoms)
        lon_min, lat_min, lon_max, lat_max = union.bounds
        d_lon, d_lat = _buffer_degrees(buffer_km, (lat_min + lat_max) / 2.0)

        # Buffer in a locally-scaled planar frame: squash longitude by cos(lat),
        # buffer by the isotropic latitude-degree radius, unsquash. The result is
        # a true circle in kilometres. Buffering the raw lon/lat geometry instead
        # would reach only cos(lat) of the stated distance east-west -- 57 % of it
        # in Danish waters -- and silently drop the vessels in between.
        scale = d_lat / d_lon if d_lon else 1.0
        squashed = shapely_scale(union, xfact=scale, yfact=1.0, origin=(0, 0))
        buffered = shapely_scale(squashed.buffer(d_lat), xfact=1.0 / scale,
                                 yfact=1.0, origin=(0, 0))

        search_bbox: Bbox = (lon_min - d_lon, lat_min - d_lat,
                             lon_max + d_lon, lat_max + d_lat)
        candidates = self.query(search_bbox, start, end, region=region)
        if candidates.empty:
            return candidates

        tree = STRtree(shapely_points(candidates["lon"].to_numpy(),
                                      candidates["lat"].to_numpy()))
        inside = tree.query(buffered, predicate="intersects")
        if len(inside) == 0:
            return to_contract(None)

        if not whole_track:
            return to_contract(candidates.iloc[np.asarray(inside)])

        # Re-query without the bbox: `candidates` only covers the buffered cloud,
        # so filtering it by MMSI would return a track truncated at the buffer
        # edge -- which is exactly the approach leg attribution needs.
        hit_mmsi = sorted(set(candidates.iloc[np.asarray(inside)]["mmsi"].tolist()))
        return self.query(None, start, end, region=region, mmsi=hit_mmsi)

    # -- introspection ----------------------------------------------------

    def stats(self) -> dict:
        """Summary of the archive, for the CLI and the monitoring page."""
        parts = self.partitions()
        if not parts:
            return {"root": str(self.root), "partitions": 0, "rows": 0,
                    "regions": {}, "bytes": 0}
        regions: Dict[str, dict] = {}
        for p in parts:
            r = regions.setdefault(p.region, {
                "partitions": 0, "rows": 0, "bytes": 0,
                "t_start": p.t_start, "t_end": p.t_end,
                "bbox": list(p.bbox), "sources": set(),
            })
            r["partitions"] += 1
            r["rows"] += p.rows
            r["bytes"] += p.bytes
            r["t_start"] = min(r["t_start"], p.t_start)
            r["t_end"] = max(r["t_end"], p.t_end)
            r["bbox"] = [min(r["bbox"][0], p.bbox[0]), min(r["bbox"][1], p.bbox[1]),
                         max(r["bbox"][2], p.bbox[2]), max(r["bbox"][3], p.bbox[3])]
            r["sources"].update(p.sources)
        for r in regions.values():
            r["sources"] = sorted(r["sources"])
        return {
            "root": str(self.root),
            "granularity": self.granularity,
            "partitions": len(parts),
            "rows": sum(p.rows for p in parts),
            "bytes": sum(p.bytes for p in parts),
            "regions": regions,
        }

    def verify(self) -> List[str]:
        """Check every indexed partition still exists and still hashes the same.

        Returns a list of human-readable problems; empty means the archive is
        exactly what the index says it is.
        """
        problems: List[str] = []
        for p in self.partitions():
            path = self.root / p.path
            if not path.exists():
                problems.append(f"missing partition: {p.path}")
                continue
            if p.sha256 and _sha256(path) != p.sha256:
                problems.append(f"content hash changed since indexing: {p.path}")
        return problems


# --------------------------------------------------------------------------
# GeoJSON -> shapely
# --------------------------------------------------------------------------


def _cloud_geometries(cloud) -> list:
    """Extract shapely geometries from a path / GeoJSON dict / shapely geometry."""
    from shapely.geometry import shape as shapely_shape
    from shapely.geometry.base import BaseGeometry

    if isinstance(cloud, BaseGeometry):
        return [cloud]
    if isinstance(cloud, (str, Path)):
        cloud = json.loads(Path(cloud).read_text(encoding="utf-8"))
    if not isinstance(cloud, dict):
        raise TypeError(f"cloud must be a path, GeoJSON dict or geometry, "
                        f"got {type(cloud).__name__}")

    kind = cloud.get("type")
    if kind == "FeatureCollection":
        return [shapely_shape(f["geometry"]) for f in cloud.get("features", [])
                if f.get("geometry")]
    if kind == "Feature":
        return [shapely_shape(cloud["geometry"])] if cloud.get("geometry") else []
    if kind:
        return [shapely_shape(cloud)]
    raise ValueError("cloud GeoJSON has no 'type'")
