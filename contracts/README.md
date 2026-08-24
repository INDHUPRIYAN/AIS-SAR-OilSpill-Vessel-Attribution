# OceanTrace — `contracts/`

**Status: FROZEN.** Changing any field below needs team sign-off from Indhu.

This folder is the reason nobody in this team ever waits for anybody. It contains
(a) the schemas every component's output must satisfy and (b) a full set of valid mock
files so you can build, run and test your component alone, today, before a single real
API key exists.

**If this README and someone's memory disagree, this README wins.**

---

## 1. Two laws

| Law | Meaning |
|---|---|
| **WGS84, `[lon, lat]`** | Every coordinate is EPSG:4326 in GeoJSON order — longitude first. Reproject at your ingest boundary, never inside a contract file. |
| **UTC with `Z`** | Every timestamp is timezone-aware UTC, e.g. `2017-02-02T00:39:42Z`. IST is UTC+05:30 — a local timestamp that leaks in shifts the origin window by five and a half hours and blames the wrong ship. The schemas reject naive timestamps. |

Everything else follows from these two.

---

## 2. Quick start (every developer, day 1)

```bash
pip install pydantic rasterio pandas pyarrow numpy pytest
python contracts/make_mocks.py     # regenerates every mock file (deterministic seed)
pytest contracts/tests -q          # 43 tests — must be green before you start
```

Then point your component at `contracts/mocks/` instead of at your teammates.

Validate your own output before every handover:

```python
import sys; sys.path.insert(0, ".")
from pathlib import Path
from contracts.schemas import CONTRACTS

model, _ = CONTRACTS["slick"]                       # or origin_cloud / forecast / suspects ...
model.model_validate_json(Path("my_output.geojson").read_text())   # raises = you are not done
```

For `vessels.parquet`:

```python
import pandas as pd
from contracts.schemas import validate_vessels_df
validate_vessels_df(pd.read_parquet("vessels.parquet"))
```

---

## 3. Who produces what

| Contract | Producer | Consumers | Mock file |
|---|---|---|---|
| `scene_meta.json` + Sigma0 dB GeoTIFF | **Pavitra** | Detection, UI | `scene_meta.json`, `scene_sigma0_db.tif` |
| `/detect` response + `raw_mask.tif` | **Indhu** | Engine A, UI | `detect_response.json`, `raw_mask.tif` |
| `slick.geojson` | **Nandha** (Engine A) | Drift, UI | `slick.geojson` |
| `origin_cloud.geojson` | **Nandha** (Engine B) | Attribution, UI | `origin_cloud.geojson` |
| `forecast.geojson` | **Nandha** (Engine B) | UI | `forecast.geojson` |
| `vessels.parquet` | **Krishnan** | Attribution, UI | `vessels.parquet` |
| `suspects.json` | **Nandha** (Engine C) | UI | `suspects.json` |
| `provider_status.json` | **Pavitra, Keerthana, Krishnan** (one each) | Monitoring page | `provider_status.json` |
| `currents.nc`, `wind.nc` | **Keerthana** | Drift | see §6 — NetCDF, not JSON-schema'd |

---

## 4. Schema map

```
contracts/
├── schemas/
│   ├── common.py     enums, BBox, LonLat, Score, UTCDateTime, CRS guard
│   ├── scene.py      SceneMeta · DetectRequest · DetectResponse · Candidate
│   ├── geo.py        SlickCollection · OriginCloud · ForecastCollection
│   └── tabular.py    VESSEL_COLUMNS + validate_vessels_df · SuspectsReport · ProviderStatusFile
├── mocks/            10 valid files — Demo Scene A (Chennai/Ennore, 2017-02-02T00:39Z)
├── make_mocks.py     regenerates mocks/ deterministically
├── tests/            43 tests: schema validation, cross-file invariants, negative cases
└── README.md
```

`CONTRACTS` in `schemas/__init__.py` maps a contract name to `(model, mock_filename)` — the
main system uses it to validate any pipeline output generically.

### Field notes that bite

- **`slick.geojson`** — `orientation_deg` is the major-axis bearing, `0 = North, clockwise, [0, 180)`.
  `centroid` must lie inside its own polygon (the schema enforces this; it catches `[lat, lon]` swaps).
  `age_hours_estimate` may be `null` — a Fay-spreading proxy with a low `age_confidence` is honest,
  a confident wrong number is not.
- **`origin_cloud.geojson`** — one FeatureCollection holding two kinds of feature, discriminated by
  `properties.feature_type`: `"particle"` (Point) and `"ellipse"` (Polygon, one per timestep at a
  stated `confidence_level`). `step_index` counts **backwards**: 0 = acquisition time. Every particle
  timestamp must be ≤ acquisition — a test enforces this.
- **`forecast.geojson`** — polygons at `horizon_h` ∈ {6, 12, 24} and `confidence_level` ∈ {0.5, 0.9}.
  Area must grow with horizon; the test suite fails you if it doesn't.
- **`vessels.parquet`** — 14 columns, exactly (see `VESSEL_COLUMNS`). Sorted by `(mmsi, timestamp_utc)`.
  `timestamp_utc` is `datetime64[ns, UTC]`. `culprit` is synthetic ground truth only and must be
  `False` on every row where `source == "real"`.
- **`suspects.json`** — `weights` must sum to 1.0 and cover exactly the six factors; `total_score`
  must equal the weighted sum of `sub_scores` (a test recomputes it). Ranks are 1..N, sorted by
  descending score. `reason` is shown verbatim in the UI, so write it for a human investigator.
- **`provider_status.json`** — `active_provider` must be a member of `chain`; a `FAILED` primary must
  have handed over to a fallback.

---

## 5. Error taxonomy

Every component returns one of these instead of crashing:

`AUTH_FAILED` · `TIMEOUT` · `RATE_LIMITED` · `UNAVAILABLE` · `BAD_RESPONSE` · `NONE`

The main system reacts by falling back and setting a UI badge. **The pipeline never halts.**

---

## 6. `currents.nc` / `wind.nc` (Keerthana ↔ Nandha)

NetCDF is not covered by the Pydantic schemas, so the variable names below are the contract.
This is one of only two coordination points in the whole team — write them down and don't drift.

| File | Variables | Dims | Units |
|---|---|---|---|
| `currents.nc` | `uo`, `vo` | `(time, lat, lon)` | m s⁻¹, eastward / northward |
| `wind.nc` | `u10`, `v10` | `(time, lat, lon)` | m s⁻¹ at 10 m |

Coordinates named `time` (UTC, CF-encoded), `lat`, `lon` (degrees, WGS84). Grid must cover the scene
bbox plus a margin ≥ the maximum backtrack distance. Missing values as NaN, never `-9999`.

---

## 7. Mock scene at a glance

Demo Scene A — synthetic stand-in for Chennai/Ennore, bbox `[80.10, 12.90, 80.55, 13.35]`,
acquisition `2017-02-02T00:39:42Z`.

- A dark 9.2 km × 1.1 km slick on a 62° bearing, plus one low-wind look-alike for the screening stage
  to reject.
- A 12-hour hindcast: 300 particles × 9 timesteps drifting back up-current, with a 90% ellipse per step.
- 20 vessels, 5-minute AIS reports, of which one tanker (`MMSI 419000631`) crosses the origin cloud,
  slows from 13.6 to 5.9 kn and goes dark for 47 minutes — the injected culprit.
- `suspects.json` ranks that tanker first, and a test asserts it. **This is the end-to-end sanity check
  for the entire project**: if the real pipeline can't reproduce that ranking on synthetic data with
  known ground truth, it isn't working yet.

Everything is flagged `source: "synthetic"` so the UI badge tells the truth even in mock mode.

---

## 8. Handover gate (Indhu enforces this on everyone, including himself)

1. Runs from one command against `contracts/mocks/`. 2. Own tests green. 3. Output validates against
the schema. 4. Example input committed. 5. Example output committed. 6. Test results recorded
(success, failure, edge). 7. Error behaviour documented per class. 8. README: setup, credentials,
known issues. 9. Run instructions. 10. Integration instructions.
