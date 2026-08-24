# Engine B — Drift (hindcast + forecast)

`slick.geojson` + met-ocean → `origin_cloud.geojson` (backward) and `forecast.geojson`
(forward).

```bash
python -m engines.drift --slick slick.geojson --currents currents.nc --wind wind.nc \
    --mode hindcast --hours 24 --out origin_cloud.geojson
python -m engines.drift --slick slick.geojson --currents currents.nc --wind wind.nc \
    --mode forecast --hours 24 --out forecast.geojson
```

| | |
|---|---|
| **In** | `slick.geojson`, `currents.nc` (u/v), `wind.nc` (u10/v10) |
| **Out** | `origin_cloud.geojson` (§4.3) or `forecast.geojson` (§4.3) + status object |
| **Errors** | `MISSING_INPUT`, `BAD_GRID` |
| **Config** | `config/drift.yaml` |

## Engine selection

Handbook order is OpenOil → OceanDrift → Euler. **Only the Euler fallback exists today**
(Phase 3 is blocked on a conda install), so every run reports `engine_used: "fallback"`
and the output names `"euler"`. The file format will not change when OpenDrift lands.

Physics: `v = current(x,t) + 0.03 · wind10(x,t)`, forward Euler with per-step Gaussian
diffusion, backward runs by negating the timestep. Metre↔degree conversion uses each
particle's own latitude, re-evaluated every step.

## Both files are optional, separately

`--currents` alone runs wind-free; `--wind` alone runs zero-current mode. Omitting both
is `MISSING_INPUT`. Each degradation is warned about, matching the fallback register.

## Variable names

The contract says `u`/`v` and `u10`/`v10`. The reader also accepts the usual CF and
provider aliases (`uo`, `water_u`, `eastward_sea_water_velocity`, `10u`, …) and warns
which it matched, so a naming mismatch with Keerthana degrades to a warning rather than a
failed run.

## What the output guarantees

Never a single origin point (handbook pitfall #5): always a weighted particle cloud, a
confidence ellipse per timestep, and a time window.

**When the current field does not deform the cloud** — uniform translation or rigid
rotation — the backtracked particles never converge, so no release time can be recovered
from drift alone. The engine widens the window to the whole run and says so. See
[KNOWN_ISSUES.md](../../KNOWN_ISSUES.md) §4.

Forecast mode emits one predicted-extent polygon per horizon (+6/+12/+24 h), hulled with
`shapely.concave_hull` over the particles inside the 90% confidence region, plus an
`uncertainty_growth` ratio against the spread at seeding.

## Verified against

- **Analytic:** a 24 h backtrack in a constant current lands on the hand-computed point
  (`seed − v·t`) to within 15 m.
- **Round trip:** forward 12 h then backward 12 h returns within 50 m.
- **Irreversibility:** with diffusion on, the round trip must *not* close — a random walk
  is not reversible, and there is a test asserting it.
