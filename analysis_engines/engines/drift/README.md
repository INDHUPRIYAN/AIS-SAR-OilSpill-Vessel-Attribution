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

Handbook order is OpenOil → OceanDrift → Euler, and all three are implemented behind one
signature (`backends.py`). Selection is by availability:

```bash
--engine auto        # default: first installed engine in the order above
--engine openoil     # pin one; fails loudly if it is not installed
--engine euler       # force the fallback
```

Pinning an engine that is missing is an error, never a silent downgrade — a run that
quietly used different physics than the one you asked for would mean something different.

`engine_used` in the status object is `"primary"` for either OpenDrift model and
`"fallback"` for Euler; the output's `origin_window.engine_used` names the specific model.

Euler physics: `v = current(x,t) + 0.03 · wind10(x,t)`, forward Euler with per-step
Gaussian diffusion, backward runs by negating the timestep. Metre↔degree conversion uses
each particle's own latitude, re-evaluated every step.

---

## ⚠️ TO COMPLETE LATER — Phase 3 is written but unverified

**OpenDrift is not installed on this machine, so `opendrift_adapter.py` has never
executed.** Everything below it in the stack is done and tested; the adapter itself is a
reviewed draft against the OpenDrift API. Until the environment exists, every run takes
the Euler path and says so in its warnings.

### What is already done

- `backends.py` — the three backends, the selection order, and the pin-vs-downgrade rule.
  **Fully tested** (15 tests) without OpenDrift present.
- `opendrift_adapter.py` — seeding at our exact particle positions, negative timestep for
  backward runs, matched diffusivity and wind-drift factor, and unpacking both the modern
  `.result` xarray layout and the older `.history` masked arrays. **Untested.**
- The mock NetCDFs now carry proper CF `standard_name` attributes
  (`x_sea_water_velocity`, `y_sea_water_velocity`, `x_wind`, `y_wind`), which is what
  OpenDrift's generic reader maps by. Without this the OpenDrift path would have failed on
  our own fixtures.
- Three tests sit skipped behind `requires_opendrift`, including the handbook §8
  fallback-agreement test. They start running the moment the environment exists.

### What remains

1. **Install Miniconda** (~400 MB). Not done here because installing system-wide software
   is the machine owner's call.
2. Build the environment — and note it needs *this module's* dependencies too, or pytest
   cannot run the suite that exercises OpenDrift:

   ```bash
   conda create -n drift python=3.11
   conda activate drift
   conda install -c conda-forge opendrift
   python -c "import opendrift; print(opendrift.__version__)"   # smoke test
   pip install -r requirements.txt
   conda env export > environment.yml                            # replaces the skeleton
   ```

3. Run `python -m pytest tests/test_drift_backends.py` **from that environment**. The
   three skipped tests will execute. Expect the adapter to need adjustment on first
   contact — the likely friction points are the `.result` vs `.history` API split, the
   exact config key names (`processes:evaporation` and friends get renamed between
   releases), and whether `seed_elements` accepts `wind_drift_factor` for `OpenOil` in
   the installed version.
4. Compare a hindcast under `--engine oceandrift` against `--engine euler` on the same
   field and record the numbers in this README.
5. Commit the real `environment.yml` export, and add the Dockerfile (P2).

### If the install fights back

Handbook §5.2 sets the rule: **if the conda/GDAL chain resists for more than half a day,
tell the team and proceed on the Euler fallback.** That is not a defeat — Euler is
complete, analytically validated against a hand-computed backtrack, and is the guaranteed
demo path by design. The only cost is that "OpenDrift OpenOil" leaves the architecture
slide.

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
