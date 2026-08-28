# Engine C — Attribution

`origin_cloud.geojson` + `vessels.parquet` → ranked, explainable `suspects.json`.

```bash
python -m engines.attribution --origin origin_cloud.geojson --vessels vessels.parquet \
    --weights config/attribution_weights.yaml --out suspects.json
```

| | |
|---|---|
| **In** | `origin_cloud.geojson`, `vessels.parquet`, optional `slick.geojson` |
| **Out** | `suspects.json` (handbook §4.4) + status object |
| **Errors** | `MISSING_INPUT`, `NO_VESSELS_IN_WINDOW` |
| **Config** | `config/attribution_weights.yaml` (weights, gates, priors, thresholds) |

## Deliberately not a classifier

Handbook pitfall #8: no ground truth for attribution exists, and the use case requires
explainability. Every factor is a transparent, hand-checkable quantity, and every score
records the evidence behind it so the generated sentence can quote real numbers.

## Gates, then scoring

Three gates run first; a vessel failing any is excluded **with the reason recorded**, and
still appears in the output with `filtered: true`.

| Gate | Test | `filter_reason` |
|---|---|---|
| Spatial | track intersects the buffered origin region | `outside origin region` |
| Temporal | in that region **during** the window | `outside time window` |
| Trajectory | course within 45° of the slick axis (undirected) | `course incompatible with slick axis` |

Survivors are scored on six factors, each normalised to [0, 1], combined as a weighted
sum:

| Factor | Weight | Signal |
|---|---|---|
| `proximity` | 0.30 | depth of the path inside the cloud, weighted by density |
| `temporal` | 0.20 | alignment with the estimated discharge time |
| `trajectory` | 0.20 | angle vs the slick axis + path-overlap length |
| `anomaly` | 0.10 | slowdown, course change, loitering |
| `ais_gap` | 0.15 | blackout overlapping the origin window |
| `prior` | 0.05 | vessel type and draft |

Weights are read from config, restricted to the six contract factors, and renormalised if
they do not sum to 1 — with a warning either way.

`proximity` is measured along the vessel's **interpolated path**, not only at transmitted
fixes. A vessel that goes dark while crossing the origin has no fixes there, and scoring
only transmitted positions would let the blackout *lower* its score — rewarding the exact
evasion the engine exists to catch.

## Where the slick axis comes from

Engine C's contract inputs do **not** include `slick.geojson`, so the trajectory factor
derives the slick's major axis from the particles at `timestep_h: 0` — those *are* the
seeded slick. Pass `--slick` to use Engine A's measured value instead; the derived value
agreed to within 1.5° on the demo scene.

## Measured performance

50 seeded scenarios, one known culprit each — see [`benchmark/`](../../benchmark/):

| | |
|---|---|
| Top-1 | **86%** |
| Top-3 | **100%** |
| Culprit lost to the gates | 0 / 50 |
| Top-1 across six different weightings | 80–90% |

## Known issues

Hard-tier top-1 is 62% by design; the contract weights are not the best-performing ones;
gate thresholds are reasoned rather than fitted. See
[KNOWN_ISSUES.md](../../KNOWN_ISSUES.md) §5–7.
