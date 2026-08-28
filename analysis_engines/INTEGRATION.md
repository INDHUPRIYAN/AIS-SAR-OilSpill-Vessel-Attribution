# Integration guide — Nandha's core engines

For Indhu. Everything the main system needs to call these three engines and handle what
comes back. Handbook Part G items 9 and 10.

## The shape of every call

Each engine is a Python function **and** a CLI. Both take contract file paths and return
the same status object (handbook §4.5):

```json
{ "ok": true,
  "engine_used": "primary" | "fallback",
  "warnings": ["..."],
  "outputs": { "slick": "out/slick.geojson" } }
```

On failure, `ok` is `false` and the object carries `error`:

```json
{ "ok": false, "engine_used": "primary", "warnings": [],
  "error": { "error_class": "EMPTY_MASK",
             "message": "mask contains no oil pixels",
             "detail": { "path": "mask.tif", "scene_id": "..." } } }
```

**The engines never raise past their own boundary and never print a traceback.** A
declared failure is data, not an exception. The CLI prints the same JSON to stdout and
exits `0` on success, `2` on a declared error.

## Calling from Python

```python
from engines.characterise import characterise
from engines.drift import hindcast, forecast
from engines.attribution import attribute

status = characterise("mask.tif", "scene_meta.json", "out/slick.geojson")
status = hindcast("out/slick.geojson", "out/origin_cloud.geojson",
                  currents_path="currents.nc", wind_path="wind.nc")
status = forecast("out/slick.geojson", "out/forecast.geojson",
                  currents_path="currents.nc", wind_path="wind.nc")
status = attribute("out/origin_cloud.geojson", "vessels.parquet",
                   "out/suspects.json", slick_path="out/slick.geojson")
```

## Calling from the shell

```bash
python -m engines.characterise --mask <tif> --scene-meta <json> --out slick.geojson
python -m engines.drift --slick slick.geojson --currents currents.nc --wind wind.nc \
    --mode hindcast|forecast --hours 24 --out <geojson>
python -m engines.attribution --origin origin_cloud.geojson --vessels vessels.parquet \
    --weights config/attribution_weights.yaml --out suspects.json
```

Run all four in sequence against the committed demo inputs:

```bash
python scripts/run_all.py            # ~3.5 s, no network, no GPU
```

## Error classes and what to do about each

| Class | Raised by | Means | Suggested main-system response |
|---|---|---|---|
| `MISSING_INPUT` | all | a file is absent/unreadable, or required metadata (`scene_id`, acquisition time, a parquet column) is missing; also a naive non-UTC timestamp | badge the stage as failed; this is a wiring problem, not a data one — retrying will not help |
| `EMPTY_MASK` | A | no oil pixels, or nothing survives the min-area threshold | badge "no slick detected"; this is a legitimate outcome for a clean scene, stop the pipeline for that scene |
| `BAD_GRID` | B | the NetCDF lacks `u`/`v`/`u10`/`v10`, is curvilinear, or does not cover the slick at all | badge "met-ocean unusable"; fall back to Keerthana's cache, or call `hindcast` with only `wind_path` for wind-only drift |
| `NO_VESSELS_IN_WINDOW` | C | no vessels in the file, or none transmitting anywhere near the window | badge "no AIS coverage"; **expected outcome**, not a bug — the UI should say so plainly |

**Important distinction in Engine C:** vessels that are present but all excluded by the
gates is **not** an error. You get `ok: true`, a written `suspects.json` with every
vessel marked `filtered: true`, an empty ranked list, and a warning. The UI should render
"0 suspects, N vessels filtered out" with each vessel's `filter_reason` — that is far
more useful to an investigator than an error badge.

## Warnings are meant to be surfaced

`warnings` is never decorative. Cases you will actually see:

- `"N component(s) below the ... minimum area were dropped as speckle"`
- `"no dB backscatter band available; damping ratio omitted and the age estimate falls back to area-only Fay"` — `damping_ratio_db` will be `null` and `age_method` will read `"fay"`
- `"the currents grid covers the slick but not the full distance particles could drift"` — the cloud is less reliable the further it goes
- `"the current field does not deform the cloud ... the whole run is reported as the origin window"` — **the origin window is the entire run**; show it as a range, never a point
- `"the slick axis was derived from the origin cloud's seeded particles"` — pass `slick_path` to Engine C to use Engine A's measured value instead

## Fallback behaviour

Engine B reports `engine_used: "fallback"` on every run today, because only the in-house
Euler integrator exists — OpenDrift (`OpenOil` → `OceanDrift`) is Phase 3 and is blocked
on a conda install. The output's `origin_window.engine_used` names the specific engine
(`"euler"`). When OpenDrift lands, the selection order becomes OpenOil → OceanDrift →
Euler and only that field changes; the file format does not.

Engines A and C have no fallback path and always report `"primary"`.

## Two deviations from the handbook you should know about

1. **`suspects.json` carries an extra `origin_window` block** (`start_utc`, `end_utc`,
   `peak_utc`, `engine_used`) so the UI can caption the suspect list without re-opening
   `origin_cloud.geojson`. Purely additive — every §4.4 field is present and unchanged.

2. **The temporal gate means "in the origin region *during* the window"**, not "has any
   fix during the window". Read literally, the handbook's wording filters almost nothing,
   because AIS tracks are continuous and nearly every vessel in a regional extract is
   transmitting during the window — it is just somewhere else. This changes what the UI's
   "filtered out: outside time window" badge implies: the vessel *was* in the region, but
   at the wrong time. Both counts (`fixes_in_window`, `fixes_in_region_and_window`) are
   available in the gate metrics if you want to show them.

## Performance

Against the committed demo inputs, on a laptop, single-threaded:

| Stage | Time |
|---|---|
| A characterise | 0.1 s |
| B hindcast (300 particles × 24 h) | 2.4 s |
| B forecast | 0.6 s |
| C attribution (6 vessels) | 0.3 s |

`origin_cloud.geojson` is the one large artifact — 2.4 MB at 300 particles × 25 hourly
timesteps. Turn down `output_every_h` or `particles` in `config/drift.yaml` if the UI
struggles when several investigations are open at once.
