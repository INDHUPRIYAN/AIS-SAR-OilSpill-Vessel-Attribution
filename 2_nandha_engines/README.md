# 2_nandha_engines — OceanTrace core engines

Owner: **Nandha Kumar**. The science between Indhu's detection mask and the UI — three
pure file-in/file-out engines. No network, no GPU, no teammate required to build, run or
demonstrate any of them.

| Engine | Status | In → Out |
|---|---|---|
| **A — Characterisation** | ✅ complete | mask GeoTIFF + `scene_meta.json` → `slick.geojson` |
| **B — Drift** (hindcast + forecast) | ✅ complete on the Euler fallback; ⬜ OpenDrift (Phase 3) | `slick.geojson` + `currents.nc` + `wind.nc` → `origin_cloud.geojson`, `forecast.geojson` |
| **C — Attribution** | ✅ complete | `origin_cloud.geojson` + `vessels.parquet` → `suspects.json` |

**177 tests passing.** Benchmark: **86% top-1**, **100% top-3** over 50 seeded scenarios.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# .venv/bin/pip install -r requirements.txt       # Linux/macOS
```

Engine B's *primary* path (OpenDrift) needs the separate conda environment described in
`environment.yml`; Engines A, C and the Euler drift fallback need only the venv above.
See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) §1 — OpenDrift is not installed yet, and that
`environment.yml` is untested.

## Run

```bash
python scripts/run_all.py     # all three engines against samples/inputs, ~3.5 s
python -m pytest              # 177 tests
```

Per engine:

```bash
python -m engines.characterise --mask <tif> --scene-meta <json> --out slick.geojson
python -m engines.drift --slick slick.geojson --currents currents.nc --wind wind.nc \
    --mode hindcast|forecast --hours 24 --out <geojson>
python -m engines.attribution --origin origin_cloud.geojson --vessels vessels.parquet \
    --weights config/attribution_weights.yaml --out suspects.json
```

Every engine returns the status object of handbook §4.5 —
`{ok, engine_used, warnings[], outputs{}}` — and exits `0` on success, `2` on a declared
error, never with a traceback.

## What it produces, end to end

```
Slick   DEMO-A_slick_01: 14.882 km2, 7.899 x 2.399 km at 62.0 deg, age ~29.7 h (low confidence)
Origin  2017-02-01T12:39:42Z -> 2017-02-02T00:39:42Z (peak 18:39:42, cloud_convergence, euler)
Spread  +6h 16.5 km2, +12h 21.7 km2, +24h 35.3 km2

Suspects (2 ranked, 4 filtered out):
  #1  0.945  MV DEMO TRADER (Tanker)
      Passed through the 90% origin region at 2017-02-01 18:14 UTC, slowed from 13.8 to
      5.9 kn, had a 55-minute AIS gap overlapping the estimated discharge window, and ran
      within 2 deg of the slick's axis.
  #2  0.702  COASTAL FERRY 7 (Passenger)
  --         MV EARLY BIRD: outside time window
  --         MV FAR AWAY: outside origin region
  --         MV CROSSCUT: course incompatible with slick axis
  --         FV NIGHT HAUL: outside origin region
```

## Documentation

| Document | For |
|---|---|
| [INTEGRATION.md](INTEGRATION.md) | Indhu — how to call the engines, every error class, what the UI should do |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | anyone integrating — the honest list |
| [HANDOVER.md](HANDOVER.md) | the Part G checklist, ticked with evidence |
| [COORDINATION.md](COORDINATION.md) | messages to send to Keerthana, Krishnan and Indhu |
| [TASKS.md](TASKS.md) | the task list and progress |
| `engines/*/README.md` | one per engine: purpose, I/O, options, accuracy |
| `benchmark/RESULTS.md` · `benchmark/SENSITIVITY.md` | the attribution numbers |

## Measured accuracy

**Engine A** against a drawn ellipse of 7.9 × 2.4 km at 62° (area 14.891 km²): area within
0.007%, perimeter within 0.12%, axes within 0.01%, orientation within 0.003°, damping
ratio within 0.11%.

**Engine B** — a 24 h backtrack in a constant current lands on the hand-computed point to
within 15 m; a forward-then-backward round trip returns within 50 m. No accuracy claim is
made for drift itself; the output always carries its uncertainty.

**Engine C** — 50 seeded scenarios with a known culprit each:

| | |
|---|---|
| Top-1 | **86%** (43/50) |
| Top-3 | **100%** (50/50) |
| Culprit lost to the gates | **0** / 50 |
| Top-1 across six different weightings | 80–90% |

The benchmark is built to be able to fail: its hard tier gives the culprit no slowdown, no
AIS blackout, a plain cargo hull, and innocent tankers running the same course through the
same region in the same window. Top-1 there is 62%, and several of those scenarios contain
genuinely no distinguishing evidence.

## Layout

```
engines/common/        errors, status object, geodesy, UTC, IO       (shared by A/B/C)
engines/schemas/       Pydantic contracts; every writer validates
engines/characterise/  features · damping · age · runner · CLI       (Engine A)
engines/drift/         grids · euler_fallback · cloud · forecast     (Engine B)
engines/attribution/   tracks · gates · scoring · explain · runner   (Engine C)
config/                characterise.yaml · drift.yaml · attribution_weights.yaml
scripts/               run_all.py (one-command demo) · make_samples.py
samples/inputs/        committed demo inputs (1.1 MB)
samples/               committed example outputs, one pipeline run
benchmark/             50-scenario harness, results and weight sensitivity
tests/fixtures/        seeded mock generators (their output is gitignored)
```

## Conventions enforced throughout

- **WGS84 (EPSG:4326)** everywhere; converted only at ingest boundaries, with an assert
  or a warning at each.
- **UTC everywhere** with a `Z` suffix. Engine A rejects a naive timestamp outright — the
  handbook's named IST trap. Engine C assumes UTC for a tz-naive parquet column, with a
  warning, because the column is UTC by contract and parquet routinely drops the tz.
- Degrees are never treated as metres: every metric quantity is computed in a local
  east/north frame at the feature's own latitude.
- Structured errors, never crashes. Drift output always shows uncertainty. Synthetic data
  is labelled.
