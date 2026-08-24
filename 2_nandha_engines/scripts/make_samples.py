"""Build the committed demo inputs under ``samples/inputs/``.

Handbook Part G requires example *inputs* as well as example outputs to be committed, so
that another developer can run the engines straight from a clone. The test fixtures
cannot serve that purpose: they live under ``tests/fixtures/data/``, which the
repository-root ``.gitignore`` swallows with its bare ``data/`` rule, and at the test
resolution the dB scene alone is 10 MB.

This script therefore writes a deliberately small demo set - ~55 m pixels instead of
~11 m - using the same generators, so there is no second code path to drift out of sync.
Files are given their contract names (``currents.nc``, ``wind.nc``) rather than the
fixture variant names.

    python scripts/make_samples.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.attribution.gates import slick_axis_from_cloud     # noqa: E402
from engines.characterise import characterise                   # noqa: E402
from engines.drift import hindcast                              # noqa: E402
from tests.fixtures.make_mask import build_scene                 # noqa: E402
from tests.fixtures.make_metocean import build_metocean          # noqa: E402
from tests.fixtures.make_vessels import build_vessels            # noqa: E402

DEMO_PIXEL_DEG = 0.0005          # ~55 m; keeps the committed dB scene small


def build(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Scene: mask + dB band + metadata, at demo resolution.
    scene = build_scene(out_dir, pixel_deg=DEMO_PIXEL_DEG)

    # 2. Met-ocean: generate the full fixture set in a scratch dir, then keep the two
    #    files the contract names, under those names.
    with tempfile.TemporaryDirectory() as scratch:
        met = build_metocean(Path(scratch))
        shutil.copy(met["currents_strain"], out_dir / "currents.nc")
        shutil.copy(met["wind_uniform"], out_dir / "wind.nc")

    # 3. Vessels have to be planted on a real origin window, so run A and B first.
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        slick = work / "slick.geojson"
        status = characterise(
            scene["mask_path"], scene["scene_meta_path"], slick,
            config_path="config/characterise.yaml",
        )
        if not status["ok"]:
            raise SystemExit(f"Engine A failed while building samples: {status}")

        cloud = work / "origin_cloud.geojson"
        status = hindcast(
            slick, cloud,
            currents_path=out_dir / "currents.nc", wind_path=out_dir / "wind.nc",
            config_path="config/drift.yaml",
        )
        if not status["ok"]:
            raise SystemExit(f"Engine B failed while building samples: {status}")

        document = json.loads(cloud.read_text(encoding="utf-8"))
        window = next(
            f for f in document["features"]
            if f["properties"].get("kind") == "origin_window"
        )
        lon, lat = window["geometry"]["coordinates"]
        vessels = build_vessels(
            out_dir, origin_lon=lon, origin_lat=lat,
            window_start=window["properties"]["start_utc"],
            window_end=window["properties"]["end_utc"],
            slick_axis_deg=slick_axis_from_cloud(document),
        )

    # The fixture ground-truth files are test scaffolding, not contract inputs.
    for noise in ("ground_truth.json", "vessels_truth.json", "metocean_truth.json"):
        (out_dir / noise).unlink(missing_ok=True)

    manifest = {
        "mask": "mask.tif",
        "scene_db": "scene_db.tif",
        "scene_meta": "scene_meta.json",
        "currents": "currents.nc",
        "wind": "wind.nc",
        "vessels": "vessels.parquet",
        "pixel_deg": DEMO_PIXEL_DEG,
        "culprit_mmsi": vessels["culprit_mmsi"],
        "note": (
            "Synthetic demo inputs, regenerate with `python scripts/make_samples.py`. "
            "Tests use a higher-resolution set under tests/fixtures/data/."
        ),
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build committed demo inputs")
    parser.add_argument("--out-dir", type=Path, default=Path("samples/inputs"))
    args = parser.parse_args(argv)

    manifest = build(args.out_dir)
    total = sum(f.stat().st_size for f in args.out_dir.iterdir() if f.is_file())
    print(json.dumps(manifest, indent=2))
    print(f"\n{args.out_dir}: {total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
