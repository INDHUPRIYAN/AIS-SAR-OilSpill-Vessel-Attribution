# 2_nandha_engines — OceanTrace Core Engines

Owner: **Nandha Kumar**. The science between Indhu's detection mask and the UI — three
pure file-in/file-out engines. No network, no GPU, no teammate required.

| Engine | Status | In → Out |
|---|---|---|
| **A — Characterisation** | ✅ implemented | mask GeoTIFF + `scene_meta.json` → `slick.geojson` |
| **B — Drift** (hindcast/forecast) | ⬜ Phase 2–4 | `slick.geojson` + `currents.nc` + `wind.nc` → `origin_cloud.geojson`, `forecast.geojson` |
| **C — Attribution** | ⬜ Phase 5–6 | `origin_cloud.geojson` + `vessels.parquet` → `suspects.json` |

Task list and progress: [TASKS.md](TASKS.md). Contracts: `docs/PS26143_Handbook_2_Nandha_Kumar.md` §4.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# .venv/bin/pip install -r requirements.txt       # Linux/macOS
```

Engine B's primary path (OpenDrift) needs the separate conda environment in
`environment.yml`; Engines A, C and the Euler drift fallback run in the venv above.

## Run

```bash
# regenerate the mock inputs (deterministic, seeded)
python -m tests.fixtures.make_mask --empty

# Engine A
python -m engines.characterise \
    --mask tests/fixtures/data/mask.tif \
    --scene-meta tests/fixtures/data/scene_meta.json \
    --out samples/slick.geojson
```

Tests: `python -m pytest` (51 passing).

## Engine A — Characterisation

Turns a 0/1 detection mask into `slick.geojson` per handbook §4.2.

```
--mask <tif>            0/1 mask GeoTIFF from Indhu's /detect
--scene-meta <json>     scene_id, acquisition time (UTC), file_path, confidence
--out <geojson>         output path
--scene-db <tif>        optional: Sigma0 dB scene (default: scene_meta["file_path"])
--config <yaml>         optional: config/characterise.yaml
--confidence <0-1>      optional: used only when the metadata omits it
--slick-id-prefix <s>   optional: default is the scene_id's trailing token
```

Prints the status object `{ok, engine_used, warnings[], outputs{}}` to stdout.
**Exit 0** on success, **exit 2** on a declared engine error — the status JSON is
printed either way, never a traceback.

### Accuracy against the known-shape fixture

A drawn ellipse of 7.9 × 2.4 km at 62°, area 14.891 km²:

| Quantity | Measured | True | Error |
|---|---|---|---|
| Area | 14.890 km² | 14.891 km² | −0.007% |
| Perimeter | 17.375 km | 17.354 km | +0.12% |
| Major axis | 7.900 km | 7.900 km | +0.005% |
| Minor axis | 2.400 km | 2.400 km | −0.01% |
| Orientation | 61.997° | 62.000° | −0.003° |
| Damping ratio | 7.008 dB | 7.000 dB | +0.11% |

### Conventions and decisions

- **`orientation_deg` is a bearing from true north**, clockwise, folded to [0, 180)
  because an ellipse axis is undirected. North–south = 0, north-east = 45, east–west =
  90. This matches AIS COG/heading, so Engine C can compare a vessel's course with the
  slick axis by direct subtraction. No handbook section fixes this convention — it is
  chosen here and enforced by test.
- **All metric quantities are computed in a local east/north frame** anchored at each
  slick's own centroid, never in raw lon/lat (a degree of longitude is ~10.84 m at the
  demo latitude versus ~11.06 m for latitude). Pixel ground area is accumulated per
  raster row, since it varies with latitude down the scene.
- **Perimeter comes from the simplified polygon**, not the raw raster boundary. A
  staircase boundary overstates perimeter by ~31%; simplifying at 1.5 px brings it to
  +0.12%, beating `skimage.perimeter_crofton` (+0.86%) while staying correct for
  anisotropic pixels.
- **`confidence` is not computed here.** It originates in Indhu's `/detect` response and
  is read from the scene metadata → `--confidence` → `null` with a warning.
- **Inputs are converted at the boundary only.** A non-WGS84 mask is reprojected on
  read with a warning; a dB band on a different grid is resampled onto the mask grid.

### The age estimate is deliberately weak

`age_hours_est` is an order-of-magnitude bracket, not a timestamp, and
`age_confidence` is hard-wired to `"low"`. Fay's law needs a volume; a mask only gives
area, so the two are closed with an assumed mean slick thickness `h`:

```
t = A^(2/3) / [ π² k₂⁴ (Δ g h² / √ν)^(2/3) ]
```

Because **t ∝ h^(−4/3)**, that one assumption dominates the answer, while **t ∝ A^(2/3)**
means the measured area barely moves it:

| Assumed thickness | Age of the 14.89 km² demo slick |
|---|---|
| 1 µm (sheen) | ~297,000 h — clamped, with a warning |
| 0.5 mm | ~75 h |
| **1 mm (default, fresh discharge)** | **~30 h** |
| 2.35 mm | ~9.5 h |

Tune it in `config/characterise.yaml`. Engine B's hindcast window is the defensible
answer for *when* a discharge happened; this is only a sanity check on it, and the two
are expected to differ.

## Error classes

Declared per handbook §4.5, returned as structured status objects — never crashes.

| Class | Raised when |
|---|---|
| `MISSING_INPUT` | file absent or unreadable; `scene_id`/acquisition time missing; naive (non-UTC) timestamp; confidence outside [0, 1] |
| `EMPTY_MASK` | mask has no oil pixels, or nothing survives the min-area threshold |
| `BAD_GRID` | *(Engine B)* NetCDF lacks a variable or does not cover the slick |
| `NO_VESSELS_IN_WINDOW` | *(Engine C)* no vessel passes the gates — a valid outcome |

## Layout

```
engines/common/        errors, status object, geodesy, UTC, IO      (shared by A/B/C)
engines/schemas/       Pydantic contracts; every writer validates
engines/characterise/  features · damping · age · runner · CLI      (Engine A)
engines/drift/         euler_fallback (to be rewritten in Phase 2)  (Engine B)
engines/attribution/   attribution_scorer (Phase 5-6)               (Engine C)
config/                characterise.yaml · attribution_weights.yaml
tests/fixtures/        seeded mock generators (outputs are gitignored)
samples/               committed example outputs
```

## Known issues

- `tests/fixtures/data/` is ignored by the repo-root `data/` rule; regenerate with
  `python -m tests.fixtures.make_mask --empty`.
- The repo-root `.gitignore` also has a bare `docs/` rule, so `2_nandha_engines/docs/`
  is untracked. Raise with Indhu — it needs to be `/docs/` to scope it to the root.
- `slick.geojson` carries a single Polygon per feature by contract; if simplification
  ever splits a slick into disjoint parts, only the largest is written and a warning
  is recorded.
