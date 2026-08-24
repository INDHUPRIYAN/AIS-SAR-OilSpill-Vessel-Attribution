"""
Generate contracts/mocks/* — the fake-but-valid files every developer builds against.

Run:  python contracts/make_mocks.py
Then: pytest contracts/tests -q      (proves every mock satisfies its schema)

Demo Scene A: Chennai / Ennore coast, 2017-02-02T00:39Z (synthetic stand-in geometry).
Nothing here is real data. Everything is flagged source="synthetic" where a source flag
exists, so the UI badge tells the truth even in mock mode.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schemas.tabular import VESSEL_COLUMNS  # noqa: E402

MOCKS = Path(__file__).parent / "mocks"
MOCKS.mkdir(parents=True, exist_ok=True)

SCENE_ID = "S1A_IW_GRDH_20170202T0039_DEMO-A"
ACQUIRED = datetime(2017, 2, 2, 0, 39, 42, tzinfo=timezone.utc)
BBOX = [80.10, 12.90, 80.55, 13.35]          # min_lon, min_lat, max_lon, max_lat
DB_RANGE = [-35.0, 0.0]
SIZE = 512
RNG = np.random.default_rng(26143)

# Slick geometry (a plausible ship-track spill: long, thin, NE-SW oriented)
SLICK_CENTER = (80.315, 13.052)               # lon, lat
SLICK_MAJOR_M = 9200.0
SLICK_MINOR_M = 1100.0
SLICK_ORIENT_DEG = 62.0                       # bearing of major axis, 0 = North, clockwise

BACKTRACK_H = 12.0
STEP_MIN = 90.0                               # 8 steps over 12 h
DRIFT_BEARING_DEG = 205.0                     # slick drifted towards SSW -> origin lies NNE
DRIFT_SPEED_MS = 0.22


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def offset(lon: float, lat: float, bearing_deg: float, dist_m: float):
    """Move a lon/lat point by dist_m along a compass bearing (flat-earth, fine at this scale)."""
    br = math.radians(bearing_deg)
    dlat = (dist_m * math.cos(br)) / 111_320.0
    dlon = (dist_m * math.sin(br)) / (111_320.0 * math.cos(math.radians(lat)))
    return lon + dlon, lat + dlat


def ellipse_polygon(lon, lat, semi_major_m, semi_minor_m, orient_deg, n=64):
    """Closed GeoJSON ring approximating an ellipse, [lon, lat] order."""
    ring = []
    o = math.radians(orient_deg)
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        # local frame: x along major axis, y along minor
        x = semi_major_m * math.cos(t)
        y = semi_minor_m * math.sin(t)
        # rotate into compass frame (bearing measured from North, clockwise)
        east = x * math.sin(o) + y * math.cos(o)
        north = x * math.cos(o) - y * math.sin(o)
        ring.append([
            round(lon + east / (111_320.0 * math.cos(math.radians(lat))), 6),
            round(lat + north / 111_320.0, 6),
        ])
    return [ring]


# ---------------------------------------------------------------------------
# 1. scene_sigma0_db.tif  +  2. raw_mask.tif
# ---------------------------------------------------------------------------

def make_rasters():
    lons = np.linspace(BBOX[0], BBOX[2], SIZE)
    lats = np.linspace(BBOX[3], BBOX[1], SIZE)            # north-up
    LON, LAT = np.meshgrid(lons, lats)

    # Sea clutter: ~ -12 dB with speckle
    scene = -12.0 + RNG.normal(0, 1.4, (SIZE, SIZE)).astype("float32")

    # Slick: dark ellipse (~ -26 dB), i.e. damped backscatter
    o = math.radians(SLICK_ORIENT_DEG)
    east = (LON - SLICK_CENTER[0]) * 111_320.0 * math.cos(math.radians(SLICK_CENTER[1]))
    north = (LAT - SLICK_CENTER[1]) * 111_320.0
    x = east * math.sin(o) + north * math.cos(o)          # along major axis
    y = east * math.cos(o) - north * math.sin(o)          # along minor axis
    ell = (x / (SLICK_MAJOR_M / 2)) ** 2 + (y / (SLICK_MINOR_M / 2)) ** 2
    mask = (ell <= 1.0)
    scene[mask] -= 14.0

    # A look-alike (low-wind patch) so the screening stage has something to reject
    lx, ly = offset(BBOX[0] + 0.33, BBOX[1] + 0.34, 0, 0)
    dl = np.hypot((LON - 80.46) * 108_000, (LAT - 13.24) * 111_320)
    scene[dl < 3000] -= 8.0

    scene = np.clip(scene, DB_RANGE[0], DB_RANGE[1])
    transform = from_bounds(BBOX[0], BBOX[1], BBOX[2], BBOX[3], SIZE, SIZE)
    prof = dict(driver="GTiff", height=SIZE, width=SIZE, count=1,
                crs="EPSG:4326", transform=transform, compress="deflate")

    with rasterio.open(MOCKS / "scene_sigma0_db.tif", "w", dtype="float32", **prof) as dst:
        dst.write(scene, 1)
        dst.update_tags(scene_id=SCENE_ID, acquired_utc=z(ACQUIRED), source="synthetic")

    with rasterio.open(MOCKS / "raw_mask.tif", "w", dtype="uint8", nodata=0, **prof) as dst:
        dst.write(mask.astype("uint8"), 1)
        dst.update_tags(scene_id=SCENE_ID, engine="mock", source="synthetic")

    return mask


# ---------------------------------------------------------------------------
# 3. scene_meta.json   4. detect_response.json
# ---------------------------------------------------------------------------

def make_scene_meta():
    return {
        "scene_id": SCENE_ID,
        "acquired_utc": z(ACQUIRED),
        "bbox": BBOX,
        "crs": "EPSG:4326",
        "db_range": DB_RANGE,
        "file_path": "contracts/mocks/scene_sigma0_db.tif",
        "provider_used": "MOCK",
        "source": "synthetic",
        "pixel_spacing_m": 96.0,
        "polarisation": "VV",
        "incidence_angle_band": None,
    }


def make_detect_response():
    half_maj_deg = (SLICK_MAJOR_M / 2) / 111_320.0
    return {
        "scene_id": SCENE_ID,
        "mask_path": "contracts/mocks/raw_mask.tif",
        "confidence": 0.91,
        "candidates": [
            {"bbox": [round(SLICK_CENTER[0] - half_maj_deg, 4), round(SLICK_CENTER[1] - half_maj_deg, 4),
                      round(SLICK_CENTER[0] + half_maj_deg, 4), round(SLICK_CENTER[1] + half_maj_deg, 4)],
             "class": "oil", "score": 0.93, "phenomenon": None},
            {"bbox": [80.435, 13.215, 80.485, 13.265],
             "class": "lookalike", "score": 0.78, "phenomenon": "low_wind"},
        ],
        "model_version": "mock-v0",
        "engine": "ml",
        "runtime_ms": 1840,
    }


# ---------------------------------------------------------------------------
# 5. slick.geojson
# ---------------------------------------------------------------------------

def make_slick(mask):
    px_area_km2 = ((BBOX[2] - BBOX[0]) * 108.0 / SIZE) * ((BBOX[3] - BBOX[1]) * 111.32 / SIZE)
    area_km2 = float(mask.sum()) * px_area_km2
    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": SCENE_ID,
            "detected_utc": z(ACQUIRED + timedelta(minutes=14)),
            "acquired_utc": z(ACQUIRED),
            "model_version": "mock-v0",
            "mask_path": "contracts/mocks/raw_mask.tif",
            "crs": "EPSG:4326",
        },
        "features": [{
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": ellipse_polygon(*SLICK_CENTER, SLICK_MAJOR_M / 2,
                                               SLICK_MINOR_M / 2, SLICK_ORIENT_DEG),
            },
            "properties": {
                "slick_id": "slick-001",
                "confidence": 0.91,
                "area_km2": round(area_km2, 3),
                "perimeter_km": round(2 * math.pi * math.sqrt(
                    ((SLICK_MAJOR_M / 2) ** 2 + (SLICK_MINOR_M / 2) ** 2) / 2) / 1000, 3),
                "centroid": [SLICK_CENTER[0], SLICK_CENTER[1]],
                "major_axis_m": SLICK_MAJOR_M,
                "minor_axis_m": SLICK_MINOR_M,
                "orientation_deg": SLICK_ORIENT_DEG,
                "damping_ratio": 13.8,
                "age_hours_estimate": 9.5,
                "age_confidence": 0.35,
                "engine": "ml",
                "source": "synthetic",
            },
        }],
    }


# ---------------------------------------------------------------------------
# 6. origin_cloud.geojson
# ---------------------------------------------------------------------------

def make_origin_cloud():
    n_particles = 300
    n_steps = int(BACKTRACK_H * 60 / STEP_MIN)            # 8
    features = []

    # start particles spread along the slick, then walk them backwards up-drift
    start_lon = np.array([SLICK_CENTER[0]] * n_particles)
    start_lat = np.array([SLICK_CENTER[1]] * n_particles)
    t_along = RNG.uniform(-0.5, 0.5, n_particles) * SLICK_MAJOR_M
    o = math.radians(SLICK_ORIENT_DEG)
    for i in range(n_particles):
        start_lon[i], start_lat[i] = offset(SLICK_CENTER[0], SLICK_CENTER[1],
                                            SLICK_ORIENT_DEG, t_along[i])

    lon, lat = start_lon.copy(), start_lat.copy()
    pid = 0
    for step in range(n_steps + 1):
        t = ACQUIRED - timedelta(minutes=STEP_MIN * step)
        if step > 0:
            dist = DRIFT_SPEED_MS * STEP_MIN * 60
            for i in range(n_particles):
                bearing = (DRIFT_BEARING_DEG + 180) + RNG.normal(0, 9)   # reverse drift + spread
                lon[i], lat[i] = offset(lon[i], lat[i], bearing, dist * RNG.normal(1.0, 0.18))
        weight = float(np.exp(-0.12 * step))
        for i in range(n_particles):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon[i], 6), round(lat[i], 6)]},
                "properties": {"feature_type": "particle", "particle_id": pid,
                               "t_utc": z(t), "step_index": step, "weight": round(weight, 4)},
            })
            pid += 1
        # 90% confidence ellipse for this timestep
        c_lon, c_lat = float(lon.mean()), float(lat.mean())
        spread_m = 900 + 620 * step
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": ellipse_polygon(c_lon, c_lat, SLICK_MAJOR_M / 2 + spread_m,
                                                        spread_m, SLICK_ORIENT_DEG)},
            "properties": {"feature_type": "ellipse", "t_utc": z(t), "step_index": step,
                           "center": [round(c_lon, 6), round(c_lat, 6)],
                           "semi_major_m": SLICK_MAJOR_M / 2 + spread_m,
                           "semi_minor_m": float(spread_m),
                           "orientation_deg": SLICK_ORIENT_DEG, "confidence_level": 0.9},
        })

    return {
        "type": "FeatureCollection",
        "metadata": {
            "scene_id": SCENE_ID,
            "origin_window_start_utc": z(ACQUIRED - timedelta(hours=BACKTRACK_H)),
            "origin_window_end_utc": z(ACQUIRED - timedelta(hours=6)),
            "backtrack_hours": BACKTRACK_H,
            "n_particles": n_particles,
            "timestep_minutes": STEP_MIN,
            "forcing": {"currents": "MOCK", "wind": "MOCK", "windage": 0.03,
                        "diffusion_m2_s": 4.0},
            "source": "synthetic",
            "crs": "EPSG:4326",
        },
        "features": features,
    }


# ---------------------------------------------------------------------------
# 7. forecast.geojson
# ---------------------------------------------------------------------------

def make_forecast():
    features = []
    for h in (6, 12, 24):
        c_lon, c_lat = offset(*SLICK_CENTER, DRIFT_BEARING_DEG, DRIFT_SPEED_MS * h * 3600)
        for conf, k in ((0.5, 1.0), (0.9, 1.7)):
            semi_maj = (SLICK_MAJOR_M / 2 + 340 * h) * k
            semi_min = (SLICK_MINOR_M / 2 + 240 * h) * k
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon",
                             "coordinates": ellipse_polygon(c_lon, c_lat, semi_maj, semi_min,
                                                            SLICK_ORIENT_DEG)},
                "properties": {"horizon_h": h, "valid_utc": z(ACQUIRED + timedelta(hours=h)),
                               "confidence_level": conf,
                               "area_km2": round(math.pi * semi_maj * semi_min / 1e6, 3),
                               "source": "synthetic"},
            })
    return {
        "type": "FeatureCollection",
        "metadata": {"scene_id": SCENE_ID, "issued_utc": z(ACQUIRED + timedelta(minutes=20)),
                     "horizons_h": [6, 12, 24],
                     "forcing": {"currents": "MOCK", "wind": "MOCK", "windage": 0.03},
                     "crs": "EPSG:4326"},
        "features": features,
    }


# ---------------------------------------------------------------------------
# 8. vessels.parquet
# ---------------------------------------------------------------------------

CULPRIT_MMSI = 419000631
VESSEL_TYPES = ["tanker", "cargo", "bulk", "fishing", "passenger", "tug", "other"]


def make_vessels():
    rows = []
    t0 = ACQUIRED - timedelta(hours=BACKTRACK_H + 2)
    n_steps = int((BACKTRACK_H + 4) * 60 / 5)             # 5-minute reports

    # --- the culprit: crosses the origin cloud, slows down, goes dark for 47 min ---
    origin_lon, origin_lat = offset(*SLICK_CENTER, DRIFT_BEARING_DEG + 180,
                                    DRIFT_SPEED_MS * BACKTRACK_H * 3600)
    entry_lon, entry_lat = offset(origin_lon, origin_lat, SLICK_ORIENT_DEG + 180, 26_000)
    gap_start = ACQUIRED - timedelta(hours=10, minutes=20)
    gap_end = gap_start + timedelta(minutes=47)
    lon, lat = entry_lon, entry_lat
    for s in range(n_steps):
        t = t0 + timedelta(minutes=5 * s)
        in_window = timedelta(hours=0) <= (ACQUIRED - t) <= timedelta(hours=BACKTRACK_H)
        sog = 13.6 if not in_window else 5.9                     # deliberate slowdown
        lon, lat = offset(lon, lat, SLICK_ORIENT_DEG, sog * 0.5144 * 300)
        if gap_start <= t <= gap_end:
            continue                                             # AIS blackout
        rows.append(dict(mmsi=CULPRIT_MMSI, timestamp_utc=t, lat=lat, lon=lon,
                         sog_kn=sog + RNG.normal(0, 0.15), cog_deg=SLICK_ORIENT_DEG,
                         heading_deg=SLICK_ORIENT_DEG, vessel_type="tanker",
                         length_m=183.0, width_m=32.0, draught_m=11.4,
                         source="synthetic", interpolated=False, culprit=True))

    # --- 19 innocents: random tracks across the scene, none loitering in the cloud ---
    for k in range(19):
        mmsi = 419000000 + 1000 + k * 37
        vtype = VESSEL_TYPES[k % len(VESSEL_TYPES)]
        vlon = RNG.uniform(BBOX[0] + 0.02, BBOX[2] - 0.02)
        vlat = RNG.uniform(BBOX[1] + 0.02, BBOX[3] - 0.02)
        bearing = RNG.uniform(0, 360)
        speed = RNG.uniform(6, 17)
        for s in range(0, n_steps, 2):                           # 10-minute reports
            t = t0 + timedelta(minutes=5 * s)
            vlon, vlat = offset(vlon, vlat, bearing, speed * 0.5144 * 600)
            rows.append(dict(mmsi=mmsi, timestamp_utc=t, lat=vlat, lon=vlon,
                             sog_kn=speed + RNG.normal(0, 0.3), cog_deg=bearing,
                             heading_deg=bearing, vessel_type=vtype,
                             length_m=float(round(RNG.uniform(40, 250))),
                             width_m=float(round(RNG.uniform(8, 40))),
                             draught_m=float(round(RNG.uniform(3, 14), 1)),
                             source="synthetic", interpolated=False, culprit=False))

    df = pd.DataFrame(rows)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["cog_deg"] = df["cog_deg"] % 360
    df["heading_deg"] = df["heading_deg"] % 360
    df["mmsi"] = df["mmsi"].astype("int64")
    df["sog_kn"] = df["sog_kn"].clip(0, 60).astype("float64")
    df = df.sort_values(["mmsi", "timestamp_utc"]).reset_index(drop=True)
    return df[list(VESSEL_COLUMNS)]


# ---------------------------------------------------------------------------
# 9. suspects.json
# ---------------------------------------------------------------------------

WEIGHTS = {"proximity": 0.25, "temporal": 0.20, "trajectory": 0.20,
           "behaviour": 0.15, "ais_gap": 0.15, "vessel_prior": 0.05}


def make_suspects(df):
    others = [m for m in df["mmsi"].unique() if m != CULPRIT_MMSI][:4]

    def total(sub):
        return round(sum(WEIGHTS[k] * v for k, v in sub.items()), 4)

    subs = [
        {"proximity": 0.94, "temporal": 0.91, "trajectory": 0.88,
         "behaviour": 0.79, "ais_gap": 0.85, "vessel_prior": 0.90},
        {"proximity": 0.52, "temporal": 0.61, "trajectory": 0.34,
         "behaviour": 0.21, "ais_gap": 0.00, "vessel_prior": 0.70},
        {"proximity": 0.41, "temporal": 0.38, "trajectory": 0.29,
         "behaviour": 0.18, "ais_gap": 0.12, "vessel_prior": 0.55},
        {"proximity": 0.22, "temporal": 0.30, "trajectory": 0.19,
         "behaviour": 0.09, "ais_gap": 0.00, "vessel_prior": 0.40},
        {"proximity": 0.15, "temporal": 0.12, "trajectory": 0.11,
         "behaviour": 0.05, "ais_gap": 0.00, "vessel_prior": 0.30},
    ]
    mmsis = [CULPRIT_MMSI] + list(others)
    names = ["MV DEMO ALPHA", "MV DEMO BRAVO", "MV DEMO CHARLIE", "MV DEMO DELTA", "MV DEMO ECHO"]
    types = ["tanker", "tanker", "cargo", "bulk", "fishing"]
    reasons = [
        "Passed through the 90% origin region at 14:19 UTC, slowed from 13.6 to 5.9 kn along the "
        "slick major axis, and transmitted nothing for 47 minutes inside the estimated discharge window.",
        "Crossed the edge of the origin region during the window but held course and speed throughout, "
        "with continuous AIS reporting.",
        "Present in the wider search area during the window; track never entered the high-probability "
        "origin region and course differs from the slick axis by 61 degrees.",
        "Brief presence near the search-area boundary, no behavioural anomalies, course unrelated to the slick.",
        "Low-speed fishing activity far from the origin region; retained only for completeness.",
    ]

    suspects = []
    for i, sub in enumerate(subs):
        suspects.append({
            "rank": i + 1, "mmsi": int(mmsis[i]), "vessel_name": names[i],
            "vessel_type": types[i], "total_score": total(sub), "sub_scores": sub,
            "reason": reasons[i],
            "evidence": {
                "closest_approach_km": [0.4, 6.2, 11.8, 19.5, 27.3][i],
                "time_in_origin_window_min": [92, 25, 0, 0, 0][i],
                "ais_gap_minutes": [47, 0, 4, 0, 0][i],
                "course_delta_deg": [3.0, 24.0, 61.0, 88.0, 132.0][i],
                "min_sog_kn": [5.9, 12.1, 9.8, 14.0, 3.2][i],
                "track_points_in_cloud": [19, 5, 0, 0, 0][i],
            },
            "source": "synthetic",
        })
    suspects.sort(key=lambda s: -s["total_score"])
    for i, s in enumerate(suspects):
        s["rank"] = i + 1

    filtered = [{"mmsi": int(m), "reason": "track never intersected the buffered origin cloud"}
                for m in df["mmsi"].unique() if m not in mmsis][:8]

    return {
        "scene_id": SCENE_ID, "run_id": "inv-001-run-001",
        "generated_utc": z(ACQUIRED + timedelta(minutes=25)),
        "weights": WEIGHTS, "suspects": suspects, "filtered_out": filtered,
        "total_vessels_considered": int(df["mmsi"].nunique()), "source": "synthetic",
    }


# ---------------------------------------------------------------------------
# 10. provider_status.json
# ---------------------------------------------------------------------------

def make_provider_status():
    now = datetime(2026, 8, 24, 13, 55, 41, tzinfo=timezone.utc)
    return {
        "generated_utc": z(now), "owner": "mock",
        "providers": [
            {"provider": "CDSE", "purpose": "Sentinel-1 SAR scene download", "status": "WORKING",
             "last_code": 200, "last_latency_ms": 1840, "last_success_utc": z(now),
             "last_failure_utc": None, "last_error_class": "NONE",
             "chain": ["CDSE", "ASF", "LocalCache"], "active_provider": "CDSE"},
            {"provider": "CMEMS", "purpose": "surface currents for drift", "status": "DEGRADED",
             "last_code": 200, "last_latency_ms": 9120,
             "last_success_utc": z(now - timedelta(minutes=2)),
             "last_failure_utc": z(now - timedelta(minutes=40)),
             "last_error_class": "TIMEOUT",
             "chain": ["CMEMS", "HYCOM", "StaticCache"], "active_provider": "CMEMS"},
            {"provider": "ERA5", "purpose": "historical wind for drift", "status": "FAILED",
             "last_code": 403, "last_latency_ms": 512,
             "last_success_utc": z(now - timedelta(minutes=45)),
             "last_failure_utc": z(now), "last_error_class": "AUTH_FAILED",
             "chain": ["ERA5", "OpenMeteo", "StaticCache"], "active_provider": "OpenMeteo"},
            {"provider": "AISStream", "purpose": "live/historic AIS vessel tracks", "status": "WORKING",
             "last_code": 200, "last_latency_ms": 340,
             "last_success_utc": z(now), "last_failure_utc": None, "last_error_class": "NONE",
             "chain": ["AISStream", "MarineCadastre", "SyntheticGenerator"],
             "active_provider": "AISStream"},
        ],
    }


def dump(name, obj):
    (MOCKS / name).write_text(json.dumps(obj, indent=2) + "\n")
    print(f"  wrote mocks/{name}")


def main():
    print("Generating OceanTrace mock contracts ->", MOCKS)
    mask = make_rasters()
    print("  wrote mocks/scene_sigma0_db.tif, mocks/raw_mask.tif")
    dump("scene_meta.json", make_scene_meta())
    dump("detect_response.json", make_detect_response())
    dump("slick.geojson", make_slick(mask))
    dump("origin_cloud.geojson", make_origin_cloud())
    dump("forecast.geojson", make_forecast())
    df = make_vessels()
    df.to_parquet(MOCKS / "vessels.parquet", index=False)
    print(f"  wrote mocks/vessels.parquet  ({len(df)} rows, {df['mmsi'].nunique()} vessels)")
    dump("suspects.json", make_suspects(df))
    dump("provider_status.json", make_provider_status())
    print("Done. Now run: pytest contracts/tests -q")


if __name__ == "__main__":
    main()
