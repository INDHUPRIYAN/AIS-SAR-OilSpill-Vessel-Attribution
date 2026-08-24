"""Single source of truth for normalisation + tiling constants.

Both training and the /detect inference service import from here. If a dB
range appears as a literal anywhere else in the codebase, that is a bug:
a train/inference mismatch destroys real-scene performance silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

# ml/ -> 1_indhu_main_system/ -> config/normalisation.yaml
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "normalisation.yaml"

# Repo root, so data/ resolves the same no matter where a script is invoked from.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"


@dataclass(frozen=True)
class SarConfig:
    db_min: float
    db_max: float
    nodata_db: float
    primary_band: int


@dataclass(frozen=True)
class TilingConfig:
    tile_size: int
    stride: int
    inference_overlap: int
    min_oil_fraction: float
    hard_negative_ratio: float


@dataclass(frozen=True)
class NormalisationConfig:
    version: int
    sar: SarConfig
    tiling: TilingConfig

    @property
    def fingerprint(self) -> str:
        """Short hash of the constants, stamped into prepared tiles, checkpoints
        and the exported model so a mismatch is detectable rather than silent."""
        import hashlib

        payload = (
            f"v{self.version}|{self.sar.db_min}|{self.sar.db_max}|"
            f"{self.sar.primary_band}|{self.tiling.tile_size}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


@lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> NormalisationConfig:
    cfg_path = Path(path) if path else CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"normalisation.yaml not found at {cfg_path}. This file is a frozen "
            "contract shared by training and inference; it must exist."
        )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    sar, tiling = raw["sar"], raw["tiling"]
    if sar["db_min"] >= sar["db_max"]:
        raise ValueError(f"db_min ({sar['db_min']}) must be < db_max ({sar['db_max']})")

    return NormalisationConfig(
        version=int(raw["version"]),
        sar=SarConfig(
            db_min=float(sar["db_min"]),
            db_max=float(sar["db_max"]),
            nodata_db=float(sar["nodata_db"]),
            primary_band=int(sar["primary_band"]),
        ),
        tiling=TilingConfig(
            tile_size=int(tiling["tile_size"]),
            stride=int(tiling["stride"]),
            inference_overlap=int(tiling["inference_overlap"]),
            min_oil_fraction=float(tiling["min_oil_fraction"]),
            hard_negative_ratio=float(tiling["hard_negative_ratio"]),
        ),
    )


def db_to_uint8(db: np.ndarray, cfg: NormalisationConfig | None = None) -> np.ndarray:
    """Sigma0 dB -> uint8 [0,255], the on-disk tile representation.

    nodata is pulled to db_min (0) rather than left as a wild value, so it
    reads as "very dark sea" instead of poisoning the input distribution.
    """
    cfg = cfg or load_config()
    s = cfg.sar
    arr = np.asarray(db, dtype=np.float32)
    arr = np.where(np.isfinite(arr), arr, s.db_min)
    arr = np.where(arr <= s.nodata_db + 1.0, s.db_min, arr)
    arr = np.clip(arr, s.db_min, s.db_max)
    scaled = (arr - s.db_min) / (s.db_max - s.db_min) * 255.0
    return np.rint(scaled).astype(np.uint8)


def uint8_to_model(tile: np.ndarray) -> np.ndarray:
    """uint8 tile -> float32 [0,1] model input. The inverse mapping of
    db_to_uint8's final step, kept here so both paths cannot drift apart."""
    return np.asarray(tile, dtype=np.float32) / 255.0


def db_to_model(db: np.ndarray, cfg: NormalisationConfig | None = None) -> np.ndarray:
    """Sigma0 dB -> float32 [0,1] directly, for inference on a live scene
    that was never written to a uint8 tile cache."""
    return uint8_to_model(db_to_uint8(db, cfg))


if __name__ == "__main__":
    c = load_config()
    print(f"config      : {CONFIG_PATH}")
    print(f"version     : {c.version}   fingerprint: {c.fingerprint}")
    print(f"dB range    : [{c.sar.db_min}, {c.sar.db_max}]  band {c.sar.primary_band}")
    print(f"tiling      : {c.tiling.tile_size}px stride {c.tiling.stride} "
          f"min_oil {c.tiling.min_oil_fraction} hard_neg {c.tiling.hard_negative_ratio}")
    print(f"data root   : {DATA_ROOT}")
