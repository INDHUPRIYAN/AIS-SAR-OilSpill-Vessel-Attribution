import os
import json
import numpy as np
import datetime
from .generator import _is_ocean, generate_synthetic_ais

def build_benchmark(scenarios=50, master_seed=1337, out_dir="data/ais/benchmark/",
                    hard_negatives=3):
    """
    Builds the 50-scenario benchmark suite.

    Each scenario's vessels.parquet meets the frozen contract and, with
    `hard_negatives` > 0, contains that many innocent near-miss vessels
    (MMSI 990000000+) so ranking the planted culprit first is earned, not
    handed over. Fully deterministic for a given master_seed.
    """
    print(f"Running benchmark suite with seed={master_seed} on {scenarios} scenarios...")
    
    np.random.seed(master_seed)
    sub_seeds = np.random.randint(0, 1000000, size=scenarios)
    
    os.makedirs(out_dir, exist_ok=True)
    
    for i in range(scenarios):
        scenario_idx = i + 1
        scenario_dir = os.path.join(out_dir, f"scenario_{scenario_idx:03d}")
        os.makedirs(scenario_dir, exist_ok=True)
        
        seed = int(sub_seeds[i])
        np.random.seed(seed)
        
        # Scene centres and culprit origins must be AT SEA. The land mask is
        # part of the generator: an origin drawn on land loses every culprit
        # fix and the scenario has no ground truth left to find. Rejection
        # sampling on the same seeded stream keeps this deterministic.
        lat_base = np.random.uniform(5.0, 20.0)
        lon_base = np.random.uniform(70.0, 90.0)
        while not bool(np.all(_is_ocean(lat_base, lon_base))):
            lat_base = np.random.uniform(5.0, 20.0)
            lon_base = np.random.uniform(70.0, 90.0)
        bbox = [lon_base - 0.5, lat_base - 0.5, lon_base + 0.5, lat_base + 0.5]

        start_dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=np.random.randint(0, 365))
        end_dt = start_dt + datetime.timedelta(hours=np.random.randint(6, 24))

        n_vessels = np.random.randint(20, 100)

        culprit_lat = np.random.uniform(lat_base - 0.2, lat_base + 0.2)
        culprit_lon = np.random.uniform(lon_base - 0.2, lon_base + 0.2)
        while not bool(np.all(_is_ocean(culprit_lat, culprit_lon))):
            culprit_lat = np.random.uniform(lat_base - 0.2, lat_base + 0.2)
            culprit_lon = np.random.uniform(lon_base - 0.2, lon_base + 0.2)

        w_start = start_dt + datetime.timedelta(hours=2)
        w_end = w_start + datetime.timedelta(hours=2)
        
        culprit_config = {
            "origin": {
                "lat": culprit_lat,
                "lon": culprit_lon,
                "window_start_utc": w_start.isoformat(),
                "window_end_utc": w_end.isoformat()
            },
            "behaviour": {
                "slowdown": bool(np.random.choice([True, False])),
                "ais_gap_minutes": int(np.random.choice([0, 30, 45, 60]))
            }
        }
        
        df = generate_synthetic_ais(
            bbox=bbox,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
            n_vessels=n_vessels,
            culprit_config=culprit_config,
            seed=seed,
            n_hard_negatives=hard_negatives,
        )

        if not df["culprit"].any():
            raise RuntimeError(
                f"scenario {scenario_idx}: culprit track has no fixes "
                f"(origin {culprit_lat:.3f},{culprit_lon:.3f}) -- a scenario "
                f"without ground truth must never be committed")

        parquet_path = os.path.join(scenario_dir, "vessels.parquet")
        df.to_parquet(parquet_path, index=False)

        hn_mmsis = sorted(int(m) for m in df["mmsi"].unique()
                          if m >= 990_000_000)
        truth_data = {
            "culprit_mmsi": 900000000,
            "origin": culprit_config["origin"],
            "seed": seed,
            "hard_negative_mmsis": hn_mmsis
        }
        
        truth_path = os.path.join(scenario_dir, "truth.json")
        with open(truth_path, 'w') as f:
            json.dump(truth_data, f, indent=2)
            
    # This function only BUILDS scenarios; it never runs attribution, so it has
    # no pass/fail or score to report. The measured result (top-1 / top-3 over
    # these scenarios) comes from `analysis_engines/benchmark/run.py`.
    return {"scenarios_built": scenarios, "out_dir": str(out_dir),
            "measured_by": "analysis_engines/benchmark/run.py"}
