"""Seeded attribution scenarios: 50 fleets, each with exactly one known culprit.

Handbook §6 Phase 7 calls for running Engine C over Krishnan's 50 seeded scenarios and
reporting top-1/top-3 hit rate. His set is not ready yet, so this module generates an
equivalent one from the same track machinery the unit-test fixture uses. When his
arrives it drops straight in - the harness only needs a parquet path and a culprit MMSI.

**The difficulty spread is the whole point.** A benchmark built only from the easy case
- lone tanker, obvious slowdown, obvious blackout, no comparable traffic - would report
100% top-1 and put a meaningless number on the metrics slide. Three tiers are generated
instead:

    easy    culprit slows AND goes dark; few decoys, mostly low-prior types
    medium  culprit does exactly one of the two; a tanker decoy shares the region
    hard    culprit does neither and is a plain cargo ship; several aligned tankers
            pass through the same region inside the same window

The hard tier is genuinely ambiguous - sometimes there is no evidence distinguishing
the culprit from an innocent vessel, and the engine is expected to miss some of them.
That is what makes the reported number worth quoting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engines.common.geo import m_per_deg_lat, m_per_deg_lon
from tests.fixtures.make_vessels import VESSEL_DIMENSIONS, VesselSpec, _track_frame

TIERS = ("easy", "medium", "hard")
TIER_MIX = ("easy",) * 20 + ("medium",) * 17 + ("hard",) * 13   # 50 scenarios

DECOY_TYPES_LOW = ("Fishing", "Passenger", "Tug" if "Tug" in VESSEL_DIMENSIONS else "Fishing")
DECOY_TYPES_HIGH = ("Tanker", "Bulk Carrier", "Cargo")


@dataclass
class Scenario:
    index: int
    seed: int
    tier: str
    culprit_mmsi: int
    vessels_path: str
    n_vessels: int
    culprit_slowdown: bool
    culprit_gap: bool
    culprit_type: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "seed": self.seed,
            "tier": self.tier,
            "culprit_mmsi": self.culprit_mmsi,
            "vessels_path": self.vessels_path,
            "n_vessels": self.n_vessels,
            "culprit_slowdown": self.culprit_slowdown,
            "culprit_gap": self.culprit_gap,
            "culprit_type": self.culprit_type,
        }


def _tier_settings(tier: str, rng: np.random.Generator) -> dict[str, Any]:
    if tier == "easy":
        return {
            "slowdown": True, "gap": True, "culprit_type": "Tanker",
            "n_decoys": int(rng.integers(2, 5)),
            "n_confusers": 0, "axis_jitter": 5.0,
        }
    if tier == "medium":
        slowdown = bool(rng.integers(0, 2))
        return {
            "slowdown": slowdown, "gap": not slowdown, "culprit_type": "Bulk Carrier",
            "n_decoys": int(rng.integers(3, 7)),
            "n_confusers": 1, "axis_jitter": 12.0,
        }
    return {
        "slowdown": False, "gap": False, "culprit_type": "Cargo",
        "n_decoys": int(rng.integers(4, 9)),
        "n_confusers": int(rng.integers(2, 4)), "axis_jitter": 20.0,
    }


def build_scenario(
    index: int,
    out_dir: Path,
    *,
    origin_lon: float,
    origin_lat: float,
    window_start: str,
    window_end: str,
    slick_axis_deg: float,
    base_seed: int = 26143,
) -> Scenario:
    """Generate one scenario's ``vessels.parquet`` and describe its ground truth."""
    tier = TIER_MIX[index % len(TIER_MIX)]
    seed = base_seed + index
    rng = np.random.default_rng(seed)
    settings = _tier_settings(tier, rng)

    start = pd.Timestamp(window_start)
    end = pd.Timestamp(window_end)
    middle = start + (end - start) / 2
    window_minutes = (end - start).total_seconds() / 60.0

    def offset(km_east: float, km_north: float) -> tuple[float, float]:
        return (
            origin_lon + km_east * 1000.0 / m_per_deg_lon(origin_lat),
            origin_lat + km_north * 1000.0 / m_per_deg_lat(origin_lat),
        )

    culprit_mmsi = 500_000_000 + index
    specs = [
        VesselSpec(
            mmsi=culprit_mmsi,
            name=f"CULPRIT {index:02d}",
            vessel_type=settings["culprit_type"],
            imo=f"IMO{9000000 + index}",
            bearing_deg=(slick_axis_deg + rng.normal(0.0, settings["axis_jitter"])) % 360.0,
            pass_lon=origin_lon,
            pass_lat=origin_lat,
            pass_time=middle + pd.Timedelta(minutes=float(rng.uniform(-0.2, 0.2) * window_minutes)),
            cruise_kn=float(rng.uniform(10.0, 15.0)),
            culprit=True,
            slowdown=settings["slowdown"],
            ais_gap=settings["gap"],
        )
    ]

    # Confusers: innocent vessels deliberately built to look like the culprit - same
    # region, same window, compatible course, high-prior type. These are what separate
    # a real hit rate from a flattering one.
    for n in range(settings["n_confusers"]):
        specs.append(
            VesselSpec(
                mmsi=culprit_mmsi + 1000 + n,
                name=f"CONFUSER {index:02d}-{n}",
                vessel_type=str(rng.choice(DECOY_TYPES_HIGH)),
                imo=f"IMO{9100000 + index * 10 + n}",
                bearing_deg=(slick_axis_deg + rng.normal(0.0, 15.0)) % 360.0,
                pass_lon=offset(float(rng.normal(0.0, 2.0)), float(rng.normal(0.0, 2.0)))[0],
                pass_lat=offset(float(rng.normal(0.0, 2.0)), float(rng.normal(0.0, 2.0)))[1],
                pass_time=middle + pd.Timedelta(
                    minutes=float(rng.uniform(-0.4, 0.4) * window_minutes)
                ),
                cruise_kn=float(rng.uniform(9.0, 16.0)),
            )
        )

    # Ordinary traffic: scattered in space and time, mostly filtered by the gates.
    for n in range(settings["n_decoys"]):
        km_east = float(rng.normal(0.0, 25.0))
        km_north = float(rng.normal(0.0, 25.0))
        lon, lat = offset(km_east, km_north)
        specs.append(
            VesselSpec(
                mmsi=culprit_mmsi + 2000 + n,
                name=f"TRAFFIC {index:02d}-{n}",
                vessel_type=str(rng.choice(DECOY_TYPES_LOW + DECOY_TYPES_HIGH)),
                imo=f"IMO{9200000 + index * 10 + n}",
                bearing_deg=float(rng.uniform(0.0, 360.0)),
                pass_lon=lon,
                pass_lat=lat,
                pass_time=middle + pd.Timedelta(hours=float(rng.uniform(-20.0, 20.0))),
                cruise_kn=float(rng.uniform(4.0, 18.0)),
            )
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.concat([_track_frame(spec, rng) for spec in specs], ignore_index=True)
    frame = frame.sort_values(["mmsi", "timestamp"]).reset_index(drop=True)
    path = out_dir / f"scenario_{index:03d}.parquet"
    frame.to_parquet(path, index=False)

    return Scenario(
        index=index,
        seed=seed,
        tier=tier,
        culprit_mmsi=culprit_mmsi,
        vessels_path=str(path),
        n_vessels=len(specs),
        culprit_slowdown=settings["slowdown"],
        culprit_gap=settings["gap"],
        culprit_type=settings["culprit_type"],
    )


def build_all(
    out_dir: Path,
    count: int = 50,
    **kwargs: Any,
) -> list[Scenario]:
    """Generate ``count`` scenarios reproducibly."""
    return [build_scenario(i, out_dir, **kwargs) for i in range(count)]
