"""Run all three engines end to end against the committed demo inputs.

Handbook Part G item 1: "Working code, runnable from **one command**." This is that
command, and it is also the demo path - it needs no network, no GPU, and no teammate.

    python scripts/run_all.py                       # samples/inputs -> out/
    python scripts/run_all.py --out samples         # regenerate the committed outputs

Exits non-zero if any engine returns ``ok: false``, so it doubles as a smoke test.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.attribution import attribute            # noqa: E402
from engines.characterise import characterise        # noqa: E402
from engines.drift import forecast, hindcast         # noqa: E402

CHECK = "OK "
CROSS = "FAIL"


def _report(label: str, status: dict, elapsed: float) -> bool:
    ok = bool(status.get("ok"))
    print(f"  [{CHECK if ok else CROSS}] {label:<28} {elapsed:5.1f}s")
    for warning in status.get("warnings", []):
        print(f"         warning: {warning}")
    if not ok:
        error = status.get("error", {})
        print(f"         {error.get('error_class')}: {error.get('message')}")
    return ok


def _timed(function, *args, **kwargs) -> tuple[dict, float]:
    started = time.perf_counter()
    status = function(*args, **kwargs)
    return status, time.perf_counter() - started


def run(inputs: Path, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    mask = inputs / "mask.tif"
    scene_meta = inputs / "scene_meta.json"
    currents = inputs / "currents.nc"
    wind = inputs / "wind.nc"
    vessels = inputs / "vessels.parquet"

    missing = [p.name for p in (mask, scene_meta, currents, wind, vessels) if not p.is_file()]
    if missing:
        print(f"missing input(s) in {inputs}: {missing}")
        print("run `python scripts/make_samples.py` first")
        return 1

    slick = out / "slick.geojson"
    origin_cloud = out / "origin_cloud.geojson"
    forecast_path = out / "forecast.geojson"
    suspects = out / "suspects.json"

    print(f"OceanTrace core engines - inputs: {inputs}, outputs: {out}\n")
    ok = True

    status, elapsed = _timed(
        characterise, mask, scene_meta, slick, config_path="config/characterise.yaml"
    )
    ok &= _report("A  characterise", status, elapsed)
    if not ok:
        return 1

    status, elapsed = _timed(
        hindcast, slick, origin_cloud,
        currents_path=currents, wind_path=wind, config_path="config/drift.yaml",
    )
    ok &= _report("B  drift (hindcast)", status, elapsed)
    if not ok:
        return 1

    status, elapsed = _timed(
        forecast, slick, forecast_path,
        currents_path=currents, wind_path=wind, config_path="config/drift.yaml",
    )
    ok &= _report("B  drift (forecast)", status, elapsed)

    status, elapsed = _timed(
        attribute, origin_cloud, vessels, suspects,
        weights_path="config/attribution_weights.yaml", slick_path=slick,
    )
    ok &= _report("C  attribution", status, elapsed)
    if not ok:
        return 1

    _summarise(slick, origin_cloud, forecast_path, suspects)
    return 0 if ok else 1


def _summarise(slick: Path, cloud: Path, forecast_path: Path, suspects: Path) -> None:
    document = json.loads(slick.read_text(encoding="utf-8"))
    first = document["features"][0]["properties"]
    print(f"\nSlick   {first['slick_id']}: {first['area_km2']} km2, "
          f"{first['major_axis_km']} x {first['minor_axis_km']} km at "
          f"{first['orientation_deg']} deg, age ~{first['age_hours_est']} h "
          f"({first['age_confidence']} confidence)")

    window = next(
        f["properties"] for f in json.loads(cloud.read_text(encoding="utf-8"))["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    print(f"Origin  {window['start_utc']} -> {window['end_utc']} "
          f"(peak {window['peak_utc']}, {window['method']}, {window['engine_used']})")

    horizons = json.loads(forecast_path.read_text(encoding="utf-8"))["features"]
    spread = ", ".join(
        f"+{h['properties']['horizon_h']:g}h {h['properties']['area_km2']:.1f} km2"
        for h in horizons
    )
    print(f"Spread  {spread}")

    payload = json.loads(suspects.read_text(encoding="utf-8"))
    ranked = [v for v in payload["vessels"] if not v.get("filtered")]
    filtered = [v for v in payload["vessels"] if v.get("filtered")]
    print(f"\nSuspects ({len(ranked)} ranked, {len(filtered)} filtered out):")
    for vessel in ranked:
        print(f"  #{vessel['rank']}  {vessel['score_total']:.3f}  "
              f"{vessel['name']} ({vessel['vessel_type']})")
        print(f"      {vessel['reason']}")
    for vessel in filtered:
        print(f"  --         {vessel['name']}: {vessel['filter_reason']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Engines A, B and C end to end")
    parser.add_argument("--inputs", type=Path, default=Path("samples/inputs"))
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args(argv)
    return run(args.inputs, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
