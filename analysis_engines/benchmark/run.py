"""Run Engine C over the seeded scenarios and report the attribution hit rate.

Handbook §6 Phase 7: "Run Engine C over Krishnan's 50 seeded scenarios (each has a known
culprit) -> report top-1/top-3 hit rate. This number goes on the metrics slide."

    python -m benchmark.run                    # 50 scenarios, writes benchmark/results.*
    python -m benchmark.run --count 10         # quick pass

The origin cloud is produced once by actually running Engines A and B, then every
scenario is attributed against it. That matches what the benchmark measures: attribution
quality *given* an origin cloud. Detection and drift accuracy are measured separately
(IoU for detection; drift reports uncertainty rather than an accuracy claim).

Alongside the headline numbers the report breaks results down by difficulty tier and by
culprit behaviour, and separately counts how often the *gates* discarded the culprit.
A gate false-negative is unrecoverable - no amount of scoring can rank a vessel that
never reached the scorer - so it is worth seeing on its own.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from engines.attribution import attribute
from engines.characterise import characterise
from engines.drift import hindcast
from engines.attribution.gates import slick_axis_from_cloud
from tests.fixtures.make_mask import build_scene
from tests.fixtures.make_metocean import build_metocean

from .scenarios import TIERS, build_scenario
from engines.attribution.runner import DEFAULT_WEIGHTS_PATH

DEFAULT_COUNT = 50
OUT_DIR = Path("benchmark")


def prepare_origin(work: Path) -> dict[str, Any]:
    """Run Engines A and B once to obtain a real origin cloud."""
    scene = build_scene(work / "scene")
    met = build_metocean(work / "met")

    slick = work / "slick.geojson"
    status = characterise(
        scene["mask_path"], scene["scene_meta_path"], slick,
        config_path="config/characterise.yaml",
    )
    if not status["ok"]:
        raise RuntimeError(f"Engine A failed: {status}")

    cloud = work / "origin_cloud.geojson"
    status = hindcast(
        slick, cloud,
        currents_path=met["currents_strain"], wind_path=met["wind_uniform"],
        config_path="config/drift.yaml",
    )
    if not status["ok"]:
        raise RuntimeError(f"Engine B failed: {status}")

    document = json.loads(cloud.read_text(encoding="utf-8"))
    window = next(
        f for f in document["features"]
        if f["properties"].get("kind") == "origin_window"
    )
    lon, lat = window["geometry"]["coordinates"]
    return {
        "slick": slick,
        "cloud": cloud,
        "origin_lon": lon,
        "origin_lat": lat,
        "window_start": window["properties"]["start_utc"],
        "window_end": window["properties"]["end_utc"],
        "slick_axis_deg": slick_axis_from_cloud(document),
    }


def evaluate(scenario, origin: dict[str, Any], work: Path) -> dict[str, Any]:
    """Attribute one scenario and locate the culprit in the ranking."""
    out = work / f"suspects_{scenario.index:03d}.json"
    status = attribute(
        origin["cloud"], scenario.vessels_path, out,
        weights_path=str(DEFAULT_WEIGHTS_PATH),
        slick_path=origin["slick"],
        investigation_id=f"bench-{scenario.index:03d}",
    )

    record = scenario.as_dict()
    record["ok"] = status["ok"]

    if not status["ok"]:
        record.update(rank=None, outcome="engine_error",
                      error=status["error"]["error_class"])
        return record

    document = json.loads(out.read_text(encoding="utf-8"))
    ranked = [v for v in document["vessels"] if not v.get("filtered")]
    filtered = {v["mmsi"] for v in document["vessels"] if v.get("filtered")}

    record["candidates"] = len(ranked)
    if scenario.culprit_mmsi in filtered:
        record.update(rank=None, outcome="culprit_filtered")
        return record

    match = next(
        (v for v in ranked if v["mmsi"] == scenario.culprit_mmsi), None
    )
    if match is None:
        record.update(rank=None, outcome="culprit_absent")
        return record

    record.update(
        rank=match["rank"],
        score_total=match["score_total"],
        outcome="ranked",
        top_score=ranked[0]["score_total"],
        margin=round(match["score_total"] - ranked[0]["score_total"], 4),
    )
    return record


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    ranks = [r["rank"] for r in records]
    top1 = sum(1 for r in ranks if r == 1)
    top3 = sum(1 for r in ranks if r is not None and r <= 3)
    placed = [r for r in ranks if r is not None]

    by_tier = {}
    for tier in TIERS:
        subset = [r for r in records if r["tier"] == tier]
        if not subset:
            continue
        by_tier[tier] = {
            "scenarios": len(subset),
            "top1": sum(1 for r in subset if r["rank"] == 1),
            "top3": sum(1 for r in subset if r["rank"] is not None and r["rank"] <= 3),
            "top1_rate": round(sum(1 for r in subset if r["rank"] == 1) / len(subset), 3),
        }

    def rate_where(predicate) -> dict[str, Any] | None:
        subset = [r for r in records if predicate(r)]
        if not subset:
            return None
        return {
            "scenarios": len(subset),
            "top1_rate": round(sum(1 for r in subset if r["rank"] == 1) / len(subset), 3),
        }

    return {
        "scenarios": total,
        "top1": top1,
        "top3": top3,
        "top1_rate": round(top1 / total, 4) if total else 0.0,
        "top3_rate": round(top3 / total, 4) if total else 0.0,
        "mean_rank_when_placed": (
            round(sum(placed) / len(placed), 2) if placed else None
        ),
        "culprit_filtered_by_gates": sum(
            1 for r in records if r["outcome"] == "culprit_filtered"
        ),
        "engine_errors": sum(1 for r in records if r["outcome"] == "engine_error"),
        "by_tier": by_tier,
        "by_behaviour": {
            "with_ais_gap": rate_where(lambda r: r["culprit_gap"]),
            "without_ais_gap": rate_where(lambda r: not r["culprit_gap"]),
            "with_slowdown": rate_where(lambda r: r["culprit_slowdown"]),
            "without_slowdown": rate_where(lambda r: not r["culprit_slowdown"]),
            "no_behavioural_evidence": rate_where(
                lambda r: not r["culprit_gap"] and not r["culprit_slowdown"]
            ),
        },
        "outcomes": dict(Counter(r["outcome"] for r in records)),
    }


def write_report(summary: dict[str, Any], records: list[dict[str, Any]], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"summary": summary, "scenarios": records}, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Engine C attribution benchmark",
        "",
        f"{summary['scenarios']} seeded scenarios, one known culprit each. Generated by",
        "`benchmark/scenarios.py`; run with `python -m benchmark.run`.",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Top-1 hit rate | **{summary['top1_rate']:.0%}** ({summary['top1']}/{summary['scenarios']}) |",
        f"| Top-3 hit rate | **{summary['top3_rate']:.0%}** ({summary['top3']}/{summary['scenarios']}) |",
        f"| Mean rank when ranked | {summary['mean_rank_when_placed']} |",
        f"| Culprit discarded by the gates | {summary['culprit_filtered_by_gates']} |",
        "",
        "## By difficulty tier",
        "",
        "| Tier | Scenarios | Top-1 | Top-3 | Top-1 rate |",
        "|---|---|---|---|---|",
    ]
    for tier, stats in summary["by_tier"].items():
        lines.append(
            f"| {tier} | {stats['scenarios']} | {stats['top1']} | {stats['top3']} | "
            f"{stats['top1_rate']:.0%} |"
        )

    lines += ["", "## By culprit behaviour", "",
              "| Culprit evidence | Scenarios | Top-1 rate |", "|---|---|---|"]
    for label, stats in summary["by_behaviour"].items():
        if stats:
            lines.append(
                f"| {label.replace('_', ' ')} | {stats['scenarios']} | "
                f"{stats['top1_rate']:.0%} |"
            )

    lines += [
        "",
        "## How to read this",
        "",
        "The tiers are deliberately uneven. `easy` culprits both slow down and go dark;",
        "`medium` do one of the two; `hard` do neither and share the origin region with",
        "several innocent tankers running the same course. Some hard scenarios carry no",
        "evidence that distinguishes the culprit at all, so a perfect score there would",
        "mean the benchmark is rigged, not that the engine is good.",
        "",
        "Attribution has no real-world ground truth (handbook §10, blocker 10), so this",
        "synthetic benchmark is the only honest number available. It measures ranking",
        "given a correct origin cloud; detection quality (IoU) and drift uncertainty are",
        "reported separately.",
        "",
    ]
    (out_dir / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Engine C attribution benchmark")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    import tempfile

    work = args.work_dir or Path(tempfile.mkdtemp(prefix="oceantrace-bench-"))
    work.mkdir(parents=True, exist_ok=True)

    origin = prepare_origin(work)
    records = []
    for index in range(args.count):
        scenario = build_scenario(
            index, work / "scenarios",
            origin_lon=origin["origin_lon"], origin_lat=origin["origin_lat"],
            window_start=origin["window_start"], window_end=origin["window_end"],
            slick_axis_deg=origin["slick_axis_deg"],
        )
        records.append(evaluate(scenario, origin, work))
        print(
            f"  [{index + 1:>3}/{args.count}] {scenario.tier:<6} "
            f"rank={records[-1]['rank']} ({records[-1]['outcome']})"
        )

    summary = summarise(records)
    write_report(summary, records, args.out_dir)

    print()
    print(f"top-1: {summary['top1_rate']:.0%}   top-3: {summary['top3_rate']:.0%}")
    print(f"report written to {args.out_dir / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
