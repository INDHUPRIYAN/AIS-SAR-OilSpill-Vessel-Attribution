"""Generate Engine C's mock input: ``vessels.parquet`` with one planted culprit.

Handbook §5.4 / §8: "synthetic ``vessels.parquet`` with one planted culprit" and "the
planted culprit must rank top-1". Columns are exactly the frozen contract set, so the
same code path serves Krishnan's real files with no changes.

The fleet is built so that every gate has something to catch. Each vessel exists to
produce one specific outcome:

    culprit      Tanker        passes all gates - through the region in-window,
                               slows 13.8 -> 5.9 kn, and goes dark for 47 minutes
    early        Bulk Carrier  fails TEMPORAL  - right place, 12 h too early
    distant      Cargo         fails SPATIAL   - right time, 30 km away
    crossing     Cargo         fails TRAJECTORY - right place and time, but running
                               across the slick axis rather than along it
    ferry        Passenger     passes the gates and should still rank below the
                               culprit once Phase 6 scores vessel priors
    fishing      Fishing       fails everything - background traffic

This module takes the origin region and window as plain arguments rather than reading
``origin_cloud.geojson``, so it stays independent of Engine B; the tests pass in values
derived from a real hindcast.

Run:  python -m tests.fixtures.make_vessels [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from engines.common.geo import m_per_deg_lat, m_per_deg_lon

KN_TO_MS = 0.514444

# Defaults match the demo scene; the tests override them from a real origin cloud.
ORIGIN_LON = 80.302
ORIGIN_LAT = 13.041
WINDOW_START = "2017-02-01T12:39:42Z"
WINDOW_END = "2017-02-02T00:39:42Z"
SLICK_AXIS_DEG = 62.0

STEP_MIN = 5                      # the contract's 5-minute interpolation
TRACK_SPAN_H = 18                 # how much track either side of the pass time

# Culprit behaviour, matching the handbook's worked example (§4.4).
CRUISE_KN = 13.8
SLOWDOWN_KN = 5.9
SLOWDOWN_HALF_WIDTH_MIN = 45
AIS_GAP_MIN = 47

VESSEL_DIMENSIONS = {
    "Tanker": (183.0, 32.0, 12.5),
    "Bulk Carrier": (199.0, 32.0, 11.8),
    "Cargo": (140.0, 22.0, 8.4),
    "Passenger": (96.0, 17.0, 4.2),
    "Fishing": (28.0, 7.0, 3.1),
}


@dataclass
class VesselSpec:
    mmsi: int
    name: str
    vessel_type: str
    imo: str
    bearing_deg: float
    pass_lon: float
    pass_lat: float
    pass_time: pd.Timestamp
    cruise_kn: float
    culprit: bool = False
    slowdown: bool = False
    ais_gap: bool = False
    expectation: str = ""


def _speed_profile(times: pd.DatetimeIndex, spec: VesselSpec) -> np.ndarray:
    """Speed in m/s at each timestamp; dips around the pass time for the culprit."""
    speeds = np.full(len(times), spec.cruise_kn, dtype=float)
    if spec.slowdown:
        minutes = np.abs((times - spec.pass_time).total_seconds().to_numpy() / 60.0)
        speeds = np.where(minutes <= SLOWDOWN_HALF_WIDTH_MIN, SLOWDOWN_KN, speeds)
    return speeds * KN_TO_MS


def _positions(times: pd.DatetimeIndex, spec: VesselSpec, speeds_ms: np.ndarray):
    """Integrate along a constant bearing so the vessel is at its pass point on time."""
    seconds = (times - times[0]).total_seconds().to_numpy()
    # Cumulative along-track distance, re-zeroed at the pass time.
    distance = np.concatenate([[0.0], np.cumsum(np.diff(seconds) * speeds_ms[:-1])])
    pass_index = int(np.argmin(np.abs((times - spec.pass_time).total_seconds())))
    distance = distance - distance[pass_index]

    bearing = math.radians(spec.bearing_deg)
    east = distance * math.sin(bearing)
    north = distance * math.cos(bearing)

    lons = spec.pass_lon + east / m_per_deg_lon(spec.pass_lat)
    lats = spec.pass_lat + north / m_per_deg_lat(spec.pass_lat)
    return lons, lats


def _track_frame(spec: VesselSpec, rng: np.random.Generator) -> pd.DataFrame:
    times = pd.date_range(
        spec.pass_time - pd.Timedelta(hours=TRACK_SPAN_H),
        spec.pass_time + pd.Timedelta(hours=TRACK_SPAN_H),
        freq=f"{STEP_MIN}min",
        tz="UTC",
    )
    speeds_ms = _speed_profile(times, spec)
    lons, lats = _positions(times, spec, speeds_ms)

    length, width, draft = VESSEL_DIMENSIONS[spec.vessel_type]
    frame = pd.DataFrame(
        {
            "mmsi": np.int64(spec.mmsi),
            "timestamp": times,
            "lat": lats,
            "lon": lons,
            "sog_kn": speeds_ms / KN_TO_MS,
            "cog_deg": spec.bearing_deg % 360.0,
            "heading_deg": (spec.bearing_deg + rng.normal(0.0, 1.5, len(times))) % 360.0,
            "vessel_name": spec.name,
            "imo": spec.imo,
            "vessel_type": spec.vessel_type,
            "length_m": length,
            "width_m": width,
            "draft_m": draft,
            "status": "under way using engine",
            "source": "synthetic",
            "culprit": spec.culprit,
        }
    )

    if spec.ais_gap:
        # Goes dark shortly before the pass time - the blackout the AIS-gap factor
        # is meant to catch. Fixes either side still bracket the origin.
        gap_start = spec.pass_time - pd.Timedelta(minutes=20)
        gap_end = gap_start + pd.Timedelta(minutes=AIS_GAP_MIN)
        frame = frame[(frame["timestamp"] < gap_start) | (frame["timestamp"] > gap_end)]

    return frame


def build_vessels(
    out_dir: Path,
    *,
    origin_lon: float = ORIGIN_LON,
    origin_lat: float = ORIGIN_LAT,
    window_start: str = WINDOW_START,
    window_end: str = WINDOW_END,
    slick_axis_deg: float = SLICK_AXIS_DEG,
    seed: int = 26143,
) -> dict:
    """Write vessels.parquet plus a ground-truth JSON naming the culprit."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    start = pd.Timestamp(window_start)
    end = pd.Timestamp(window_end)
    middle = start + (end - start) / 2

    def offset(km_east: float, km_north: float) -> tuple[float, float]:
        return (
            origin_lon + km_east * 1000.0 / m_per_deg_lon(origin_lat),
            origin_lat + km_north * 1000.0 / m_per_deg_lat(origin_lat),
        )

    distant_lon, distant_lat = offset(30.0, 0.0)
    fishing_lon, fishing_lat = offset(-45.0, 25.0)

    specs = [
        VesselSpec(
            mmsi=419001234, name="MV DEMO TRADER", vessel_type="Tanker", imo="IMO9111111",
            bearing_deg=slick_axis_deg, pass_lon=origin_lon, pass_lat=origin_lat,
            pass_time=middle, cruise_kn=CRUISE_KN,
            culprit=True, slowdown=True, ais_gap=True,
            expectation="passes all gates; must rank top-1",
        ),
        VesselSpec(
            mmsi=419002222, name="MV EARLY BIRD", vessel_type="Bulk Carrier",
            imo="IMO9222222", bearing_deg=slick_axis_deg,
            pass_lon=origin_lon, pass_lat=origin_lat,
            pass_time=start - pd.Timedelta(hours=12), cruise_kn=12.0,
            expectation="fails the temporal gate",
        ),
        VesselSpec(
            mmsi=419003333, name="MV FAR AWAY", vessel_type="Cargo", imo="IMO9333333",
            bearing_deg=slick_axis_deg, pass_lon=distant_lon, pass_lat=distant_lat,
            pass_time=middle, cruise_kn=11.0,
            expectation="fails the spatial gate",
        ),
        VesselSpec(
            mmsi=419004444, name="MV CROSSCUT", vessel_type="Cargo", imo="IMO9444444",
            bearing_deg=(slick_axis_deg + 90.0) % 360.0,
            pass_lon=origin_lon, pass_lat=origin_lat,
            pass_time=middle + pd.Timedelta(minutes=25), cruise_kn=10.5,
            expectation="fails the trajectory gate",
        ),
        VesselSpec(
            mmsi=419005555, name="COASTAL FERRY 7", vessel_type="Passenger",
            imo="IMO9555555", bearing_deg=slick_axis_deg,
            pass_lon=origin_lon, pass_lat=origin_lat,
            pass_time=middle - pd.Timedelta(minutes=40), cruise_kn=18.0,
            expectation="passes the gates; should rank below the culprit",
        ),
        VesselSpec(
            mmsi=419006666, name="FV NIGHT HAUL", vessel_type="Fishing", imo="IMO9666666",
            bearing_deg=200.0, pass_lon=fishing_lon, pass_lat=fishing_lat,
            pass_time=start - pd.Timedelta(hours=20), cruise_kn=4.5,
            expectation="fails every gate",
        ),
    ]

    frame = pd.concat([_track_frame(spec, rng) for spec in specs], ignore_index=True)
    frame = frame.sort_values(["mmsi", "timestamp"]).reset_index(drop=True)

    parquet_path = out_dir / "vessels.parquet"
    frame.to_parquet(parquet_path, index=False)

    truth = {
        "vessels_path": str(parquet_path),
        "culprit_mmsi": 419001234,
        "origin": [origin_lon, origin_lat],
        "window_start_utc": window_start,
        "window_end_utc": window_end,
        "slick_axis_deg": slick_axis_deg,
        "rows": int(len(frame)),
        "vessels": [
            {
                "mmsi": spec.mmsi,
                "name": spec.name,
                "vessel_type": spec.vessel_type,
                "culprit": spec.culprit,
                "expectation": spec.expectation,
            }
            for spec in specs
        ],
    }
    (out_dir / "vessels_truth.json").write_text(
        json.dumps(truth, indent=2) + "\n", encoding="utf-8"
    )
    return truth


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Engine C vessel mocks")
    parser.add_argument("--out-dir", default="tests/fixtures/data", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_vessels(args.out_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
