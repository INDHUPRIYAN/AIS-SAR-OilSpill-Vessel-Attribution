"""Tests for the AIS spatial+temporal index (design doc v2 sections 8 and 12).

The behaviours worth pinning down are the ones a wrong index gets *silently*
wrong: a prune that drops a partition it should have kept returns fewer
suspects and nobody notices, and a buffer applied in degrees rather than
kilometres narrows the suspect net east-west by cos(lat) without saying so.
"""

import json
import math

import numpy as np
import pandas as pd
import pytest

from ais.index import AISStore, IndexError_, _buffer_degrees, _normalise_region

BASE = pd.Timestamp("2026-02-01T00:00:00Z")


def _track(mmsi, lat0, lon0, n=12, minutes=10, start=BASE, dlat=0.0, dlon=0.01,
           source="real", vessel_type="tanker", culprit=False):
    """A straight-line track, contract-shaped."""
    return pd.DataFrame({
        "mmsi": [mmsi] * n,
        "timestamp_utc": [start + pd.Timedelta(minutes=minutes * i) for i in range(n)],
        "lat": [lat0 + dlat * i for i in range(n)],
        "lon": [lon0 + dlon * i for i in range(n)],
        "sog_kn": [10.0] * n,
        "cog_deg": [90.0] * n,
        "heading_deg": [90.0] * n,
        "vessel_type": [vessel_type] * n,
        "length_m": [200.0] * n,
        "width_m": [30.0] * n,
        "draught_m": [10.0] * n,
        "source": [source] * n,
        "interpolated": [False] * n,
        "culprit": [culprit] * n,
    })


@pytest.fixture()
def store(tmp_path):
    s = AISStore(tmp_path / "store")
    # Two vessels in Danish waters on day 1, one on day 2, one far away.
    s.ingest(_track(219000001, 55.5, 11.0), region="denmark")
    s.ingest(_track(219000002, 55.6, 11.2), region="denmark")
    s.ingest(_track(219000003, 55.5, 11.0, start=BASE + pd.Timedelta(days=1)),
             region="denmark")
    s.ingest(_track(367000001, 29.0, -90.0), region="us-gulf")
    return s


# --------------------------------------------------------------------------
# layout and manifest
# --------------------------------------------------------------------------


def test_partitions_by_region_and_day(store):
    parts = {(p.region, p.bucket) for p in store.partitions()}
    assert parts == {
        ("denmark", "date=2026-02-01"),
        ("denmark", "date=2026-02-02"),
        ("us-gulf", "date=2026-02-01"),
    }


def test_manifest_records_bbox_time_and_hash(store):
    p = next(p for p in store.partitions()
             if p.region == "denmark" and p.bucket == "date=2026-02-01")
    assert p.rows == 24 and p.mmsi_count == 2
    lon_min, lat_min, lon_max, lat_max = p.bbox
    assert lon_min == pytest.approx(11.0) and lat_min == pytest.approx(55.5)
    assert lon_max == pytest.approx(11.2 + 0.11) and lat_max == pytest.approx(55.6)
    assert p.t_start == "2026-02-01T00:00:00Z"
    assert len(p.sha256) == 64
    assert p.sources == ["real"]


def test_verify_is_clean_then_notices_tampering(store):
    assert store.verify() == []
    victim = store.root / store.partitions()[0].path
    victim.write_bytes(victim.read_bytes() + b"\x00")
    assert any("content hash changed" in msg for msg in store.verify())


def test_rebuild_manifest_reproduces_the_index(store):
    before = {p.path: (p.rows, p.bbox, p.t_start, p.t_end)
              for p in store.partitions()}
    store.index_path.unlink()
    rebuilt = AISStore(store.root)
    assert rebuilt.partitions() == []          # no manifest == empty store
    rebuilt.rebuild_manifest()
    after = {p.path: (p.rows, p.bbox, p.t_start, p.t_end)
             for p in rebuilt.partitions()}
    assert after == before


def test_corrupt_manifest_is_loud_not_silent(tmp_path):
    s = AISStore(tmp_path / "store")
    s.ingest(_track(219000001, 55.5, 11.0), region="denmark")
    s.index_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(IndexError_):
        AISStore(s.root).partitions()


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------


def test_ingest_splits_a_frame_across_midnight(tmp_path):
    s = AISStore(tmp_path / "store")
    df = _track(219000009, 55.5, 11.0, n=8, minutes=30,
                start=pd.Timestamp("2026-02-01T22:00:00Z"))
    report = s.ingest(df, region="denmark")
    assert report.partitions_written == 2
    assert {p.bucket for p in s.partitions()} == {"date=2026-02-01", "date=2026-02-02"}
    assert sum(p.rows for p in s.partitions()) == 8


def test_reingest_merges_and_dedupes_rather_than_clobbering(tmp_path):
    s = AISStore(tmp_path / "store")
    s.ingest(_track(219000001, 55.5, 11.0, n=6), region="denmark")
    report = s.ingest(_track(219000001, 55.5, 11.0, n=6), region="denmark")
    assert report.partitions_merged == 1
    assert report.duplicates_dropped == 6
    assert sum(p.rows for p in s.partitions()) == 6


def test_reingest_corrects_rather_than_duplicates(tmp_path):
    """Last write wins on (mmsi, timestamp_utc) -- a corrected re-ingest supersedes."""
    s = AISStore(tmp_path / "store")
    s.ingest(_track(219000001, 55.5, 11.0, n=3), region="denmark")
    fixed = _track(219000001, 55.5, 11.0, n=3)
    fixed["sog_kn"] = 3.5
    s.ingest(fixed, region="denmark")
    got = s.query(None, BASE, BASE + pd.Timedelta(hours=1), region="denmark")
    assert set(got["sog_kn"]) == {3.5}


def test_ingest_drops_rows_that_fail_the_contract_and_counts_them(tmp_path):
    s = AISStore(tmp_path / "store")
    df = _track(219000001, 55.5, 11.0, n=4)
    df.loc[0, "mmsi"] = 42                    # not a 9-digit MMSI
    df.loc[1, "lat"] = 999.0                  # off the planet
    report = s.ingest(df, region="denmark")
    assert report.rows_in == 4
    assert report.rows_written == 2
    assert report.rows_dropped_contract == 2


def test_ingest_normalises_the_region_into_a_path_segment(tmp_path):
    s = AISStore(tmp_path / "store")
    report = s.ingest(_track(219000001, 55.5, 11.0), region="  Bay of Bengal ")
    assert report.region == "bay-of-bengal"
    assert (s.root / "region=bay-of-bengal").is_dir()


def test_empty_ingest_is_a_no_op_not_a_crash(tmp_path):
    s = AISStore(tmp_path / "store")
    report = s.ingest(pd.DataFrame(), region="denmark")
    assert report.rows_written == 0 and s.partitions() == []


# --------------------------------------------------------------------------
# query: pruning must never lose a row it should have returned
# --------------------------------------------------------------------------


def test_query_bbox_and_time(store):
    df = store.query(bbox=(10.9, 55.4, 11.15, 55.55),
                     start=BASE, end=BASE + pd.Timedelta(hours=2))
    assert set(df["mmsi"]) == {219000001}
    assert df["lon"].between(10.9, 11.15).all()


def test_query_bounds_are_inclusive_at_both_ends(store):
    """An origin window is a closed interval; a fix exactly at `start` counts."""
    df = store.query(bbox=None, start=BASE, end=BASE, region="denmark")
    assert len(df) == 2                       # both day-1 vessels' first fix


def test_query_respects_the_region_partition(store):
    everywhere = store.query(None, BASE, BASE + pd.Timedelta(days=2))
    assert set(everywhere["mmsi"]) == {219000001, 219000002, 219000003, 367000001}
    just_dk = store.query(None, BASE, BASE + pd.Timedelta(days=2), region="denmark")
    assert 367000001 not in set(just_dk["mmsi"])


def test_query_prunes_but_agrees_with_a_full_scan(store):
    """The whole point of the index: same answer as scanning everything."""
    bbox = (10.5, 55.0, 11.5, 56.0)
    start, end = BASE, BASE + pd.Timedelta(days=2)
    pruned = store.query(bbox, start, end)

    scanned = pd.concat(
        [pd.read_parquet(store.root / p.path) for p in store.partitions()],
        ignore_index=True)
    scanned["timestamp_utc"] = pd.to_datetime(scanned["timestamp_utc"], utc=True)
    scanned = scanned[
        scanned["timestamp_utc"].between(start, end)
        & scanned["lon"].between(bbox[0], bbox[2])
        & scanned["lat"].between(bbox[1], bbox[3])]

    assert len(pruned) == len(scanned)
    assert set(pruned["mmsi"]) == set(scanned["mmsi"])


def test_query_returns_a_contract_shaped_frame_when_empty(store):
    from contracts.schemas.tabular import REQUIRED_VESSEL_COLUMNS

    df = store.query(bbox=(0.0, 0.0, 1.0, 1.0), start=BASE,
                     end=BASE + pd.Timedelta(hours=1))
    assert df.empty
    assert list(df.columns) == REQUIRED_VESSEL_COLUMNS


def test_query_output_passes_the_frozen_contract(store):
    from contracts.schemas.tabular import validate_vessels_df

    df = store.query(None, BASE, BASE + pd.Timedelta(days=2), region="denmark")
    validate_vessels_df(df)                   # raises on any violation


def test_query_rejects_a_backwards_window(store):
    with pytest.raises(ValueError, match="before start"):
        store.query(None, BASE + pd.Timedelta(hours=1), BASE)


def test_query_column_pruning(store):
    df = store.query(None, BASE, BASE + pd.Timedelta(days=2), region="denmark",
                     columns=["mmsi", "lat", "lon"])
    assert list(df.columns) == ["mmsi", "lat", "lon"]


def test_query_naive_timestamps_are_read_as_utc_not_local(store):
    """Standing Rule 1. A naive string must not be shifted by the host's zone."""
    aware = store.query(None, "2026-02-01T00:00:00Z", "2026-02-01T02:00:00Z",
                        region="denmark")
    naive = store.query(None, "2026-02-01T00:00:00", "2026-02-01T02:00:00",
                        region="denmark")
    assert len(aware) == len(naive) and len(aware) > 0


def test_missing_partition_file_warns_and_degrades(store, caplog):
    """Design rule 6: the pipeline degrades, never halts."""
    victim = store.root / store.partitions()[0].path
    victim.unlink()
    with caplog.at_level("WARNING"):
        df = store.query(None, BASE, BASE + pd.Timedelta(days=2))
    assert "rebuild_manifest" in caplog.text
    assert len(df) > 0                        # the other partitions still answer


# --------------------------------------------------------------------------
# query_cloud
# --------------------------------------------------------------------------


def _cloud(lon, lat, half=0.02):
    """A tiny square origin-cloud polygon as a GeoJSON FeatureCollection."""
    ring = [[lon - half, lat - half], [lon + half, lat - half],
            [lon + half, lat + half], [lon - half, lat + half],
            [lon - half, lat - half]]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Polygon", "coordinates": [ring]}}]}


def test_query_cloud_finds_the_vessel_that_crossed_it(store):
    df = store.query_cloud(_cloud(11.05, 55.5), BASE,
                           BASE + pd.Timedelta(hours=3), buffer_km=1.0)
    assert set(df["mmsi"]) == {219000001}


def test_query_cloud_returns_the_whole_track_by_default(store):
    """Attribution scores trajectory, which needs the approach and departure legs."""
    whole = store.query_cloud(_cloud(11.05, 55.5), BASE,
                              BASE + pd.Timedelta(hours=3), buffer_km=1.0)
    clipped = store.query_cloud(_cloud(11.05, 55.5), BASE,
                                BASE + pd.Timedelta(hours=3), buffer_km=1.0,
                                whole_track=False)
    assert len(whole) == 12                   # every fix of that vessel...
    assert 0 < len(clipped) < len(whole)      # ...not just the in-cloud ones
    # The extra fixes are the approach and departure legs, outside the buffer.
    assert whole["lon"].min() < clipped["lon"].min()
    assert whole["lon"].max() > clipped["lon"].max()


def test_query_cloud_buffer_is_a_true_circle_in_km(store):
    """A 'km' buffer must reach the stated distance east as well as north.

    At 55.5 deg N one degree of longitude is only 0.567 of a degree of latitude.
    An implementation that buffers the raw lon/lat geometry by buffer_km/111.32
    degrees therefore reaches the full distance north but only 57 % of it east,
    and the vessels in between are dropped without a word. The two probes below
    straddle that gap: `inside_east` is inside the true circle but outside a
    naive degree buffer, `outside_east` is outside both.
    """
    buffer_km = 5.0
    d_lon, d_lat = _buffer_degrees(buffer_km, 55.5)
    assert d_lon / d_lat == pytest.approx(1.0 / math.cos(math.radians(55.5)), rel=1e-3)

    s = AISStore(store.root)
    cloud, window = _cloud(11.05, 55.5, half=0.0), (BASE, BASE + pd.Timedelta(hours=1))

    # 4.5 km east: inside the true circle, outside a naive degree buffer
    # (which only reaches d_lat = 2.8 km east).
    s.ingest(_track(219000778, 55.5, 11.05 + 0.9 * d_lon, n=2, dlon=0.0),
             region="denmark")
    # 6.0 km east: outside either.
    s.ingest(_track(219000777, 55.5, 11.05 + 1.2 * d_lon, n=2, dlon=0.0),
             region="denmark")

    hit = set(s.query_cloud(cloud, *window, buffer_km=buffer_km)["mmsi"])
    assert 219000778 in hit, "a vessel 4.5 km east of a 5 km buffer must be caught"
    assert 219000777 not in hit, "a vessel 6.0 km east of a 5 km buffer must not be"

    # ... and the north-south reach is the same distance, not a different one.
    s.ingest(_track(219000779, 55.5 + 0.9 * d_lat, 11.05, n=2, dlon=0.0),
             region="denmark")
    s.ingest(_track(219000780, 55.5 + 1.2 * d_lat, 11.05, n=2, dlon=0.0),
             region="denmark")
    hit = set(s.query_cloud(cloud, *window, buffer_km=buffer_km)["mmsi"])
    assert 219000779 in hit and 219000780 not in hit


def test_query_cloud_accepts_a_geojson_path(store, tmp_path):
    path = tmp_path / "origin_cloud.geojson"
    path.write_text(json.dumps(_cloud(11.05, 55.5)), encoding="utf-8")
    df = store.query_cloud(path, BASE, BASE + pd.Timedelta(hours=3), buffer_km=1.0)
    assert set(df["mmsi"]) == {219000001}


def test_query_cloud_outside_any_traffic_is_empty_not_an_error(store):
    df = store.query_cloud(_cloud(0.0, 0.0), BASE, BASE + pd.Timedelta(hours=3))
    assert df.empty


def test_query_cloud_respects_the_time_window(store):
    """Day-2 traffic must not be returned for a day-1 origin window."""
    df = store.query_cloud(_cloud(11.05, 55.5), BASE,
                           BASE + pd.Timedelta(hours=3), buffer_km=2.0)
    assert 219000003 not in set(df["mmsi"])


# --------------------------------------------------------------------------
# small units
# --------------------------------------------------------------------------


def test_normalise_region_rejects_a_nameless_region():
    with pytest.raises(ValueError):
        _normalise_region("///")


def test_hour_granularity(tmp_path):
    s = AISStore(tmp_path / "store", granularity="hour")
    s.ingest(_track(219000001, 55.5, 11.0, n=6, minutes=30), region="denmark")
    assert {p.bucket for p in s.partitions()} == {
        "hour=2026-02-01T00", "hour=2026-02-01T01", "hour=2026-02-01T02"}


def test_stats_summarises_the_archive(store):
    st = store.stats()
    assert st["partitions"] == 3
    assert st["rows"] == 48
    assert set(st["regions"]) == {"denmark", "us-gulf"}
    assert st["regions"]["denmark"]["rows"] == 36
