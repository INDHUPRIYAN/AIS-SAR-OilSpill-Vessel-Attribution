# OceanTrace — Personal Developer Handbook

## Developer 5 of 5 — KRISHNAN

**Role:** API Developer — AIS Data Service (real AIS + synthetic AIS generator)

| | |
|---|---|
| Event | SIH 2026 · Problem Statement 26143 (NTRO) |
| Title | Leveraging satellite imagery to determine oil spills at sea with AIS correlation to identify the responsible vessel |
| Category / Theme | Software / Space Technology |
| Project codename | OceanTrace |
| Companion docs | `PS26143_System_Specification` · `PS26143_Team_Split_Handbook` |
| Datasets locked | DARTIS + Trujillo (Zenodo Sentinel-1) |
| Tooling | Claude Code as primary development assistant |

> **How to use this handbook.** This is your single working document. You own the vessel-track data source: real AIS ingestion + cleaning + interpolation, AND the synthetic AIS generator. The generator is mandatory infrastructure, not a fallback — it supplies the ground truth (known culprit) for the whole team's attribution benchmark. Everything you need is here: the parquet contract with the exact schema, a phased build plan, the 50-scenario benchmark spec, the must-pass tests (including the schema-equality test), and the handover rules. Public archives + a self-contained generator — you never wait for a teammate.

**Contents:** 1 Project in one page · 2 Your mission · 3 Ground rules · 4 Contracts · 5 Day-0 setup · 6 Build plan · 7 Frozen interfaces · 8 Testing · 9 Pitfalls · 10 Integration & handover

---

## 1. The Project in One Page

### 1.1 What the finished system does

The system takes a Sentinel-1 SAR satellite scene, detects and characterises any oil slick in it, uses ocean-current and wind data to trace the slick backwards to its probable origin point and time (hindcast) and forwards to its future spread (forecast), reconstructs historic AIS vessel traffic around that origin window, filters irrelevant vessels, scores the remaining candidates, and presents a ranked, explainable suspect list on a GIS investigation interface. API Monitoring and Key Management pages make external dependencies observable; every stage has a fallback and the last fallback in every chain is dependency-free.

### 1.2 Full pipeline and ownership

```
Sentinel-1 scene acquisition ............ Pavitra
        ↓  scene_meta.json + calibrated GeoTIFF
Oil-spill detection (2-stage ML) ........ Indhu
        ↓  raw mask
Characterisation (Engine A) ............. Nandha
        ↓  slick.geojson       ◄── currents.nc + wind.nc .. Keerthana
Drift hindcast + forecast (Engine B) .... Nandha
        ↓ origin_cloud.geojson ◄── vessels.parquet .. KRISHNAN ◄◄ you
Filter + Score + Rank (Engine C) ........ Nandha
        ↓  suspects.json
GIS UI + Monitoring + Integration ....... Indhu
```

### 1.3 Your module highlighted

```
EXTERNAL: Danish DMA / MarineCadastre (real) — Generator (synthetic)
        ↓
╔══════════════════════════════════════════╗
║ 6. AIS DATA SERVICE                      ║ ◄◄◄ YOUR MODULE
║    ingest → clean → interpolate          ║
║    + synthetic generator (known culprit) ║
╚══════════════════════════════════════════╝
        ↓ vessels.parquet
Attribution (Nandha) + UI vessel layers → suspects.json → UI (Indhu)
```

## 2. Your Mission

### 2.1 Component owned

- **Real AIS path:** Danish Maritime Authority open archive (proof scene — dense EU traffic) and/or MarineCadastre bulk CSV (US waters only). Note: NO real AIS exists for Indian waters — that is exactly why the synthetic path exists. Processing: parse, dedup, outlier removal, interpolation to 5-minute steps, per-vessel trajectory assembly, AIS-gap detection fields. Schema reference: MarineCadastre CSV (MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, VesselType, Status, Length, Width, Draft).
- **Synthetic path (mandatory infrastructure):** a generator producing shipping-lane-style traffic plus one culprit whose behaviour matches a discharge (passes through a given origin region at a given time, optional slowdown and AIS blackout), in the exact same schema.
- **The 50-scenario benchmark:** 50 simulated spill events, each with one planted culprit, reproducible from a seed — this is what the team's top-1/top-3 attribution hit-rate metric is computed on.
- **Optional stretch:** AISStream.io live websocket for a separate "live" demo tab.

### 2.2 Where it fits

You feed Nandha's attribution engine and the UI's vessel layers. Your `source` flag drives the UI's REAL/SYNTHETIC badge; your benchmark is the team's headline attribution metric; your `provider_status.json` entries feed Indhu's Monitoring page.

### 2.3 What you own

Both data paths, the cleaning/interpolation pipeline, the generator, the benchmark set and its seed, the schema guarantee (real ≡ synthetic), provider status, tests and README.

### 2.4 What you do NOT own

Attribution scoring and filtering (Nandha). Origin estimation (Nandha). Detection (Indhu). Scenes (Pavitra). Met-ocean (Keerthana). UI (Indhu).

### 2.5 Why you are never blocked

Real archives are public bulk downloads; the generator needs nothing external; the "origin region" input for synthetic scenarios is just a lat/lon/time JSON — mocked until Nandha's drift output exists.

## 3. Ground Rules (identical for all five developers)

| Rule | Meaning |
|---|---|
| Contract is law | If your output matches the schema in `contracts/`, integration works without touching your code. Changes to a frozen contract require team sign-off. |
| WGS84 everywhere | All positions in EPSG:4326 (lon/lat). Convert only at ingest boundaries; one assert per boundary. |
| UTC everywhere | All timestamps UTC. Beware IST = UTC+05:30 — archive timestamps must be verified UTC at parse time. |
| Error taxonomy | Standard classes: `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `BAD_RESPONSE`, plus yours in §4.4. Structured errors, never crashes. |
| Mocks first | `contracts/mocks/` exists from day 1 (Indhu creates). Everyone develops against mocks. |
| No blocking | Every developer builds, runs and tests alone. |
| Freeze rule | Once a component is integrated and green, nobody edits it without Indhu's sign-off. |
| Honesty | Synthetic data is flagged in the schema and labelled in the UI — never passed off as real. |

## 4. Contracts — Your Inputs and Outputs

### 4.1 Input — request from the main system (mocked as fixed JSON files during development)

```json
{ "bbox": [80.10, 12.90, 80.55, 13.35],
  "start": "2017-02-01T00:00:00Z",
  "end":   "2017-02-02T06:00:00Z",
  "mode": "synthetic",
  "n_vessels": 40,
  "culprit": {
    "origin": { "lat": 13.048, "lon": 80.301,
                "window_start_utc": "2017-02-01T14:00:00Z",
                "window_end_utc":   "2017-02-01T18:00:00Z" },
    "behaviour": { "slowdown": true, "ais_gap_minutes": 47 } },
  "seed": 42 }
```

`culprit` and `seed` apply to synthetic mode only. In `mode: "real"` the same bbox/window selects archive rows.

### 4.2 Output — `vessels.parquet` (the exact schema; identical for real and synthetic)

| Column | Dtype | Notes |
|---|---|---|
| `mmsi` | int64 | vessel id |
| `timestamp` | timestamp (UTC) | 5-min interpolated steps |
| `lat`, `lon` | float64 | WGS84 |
| `sog_kn` | float32 | speed over ground |
| `cog_deg`, `heading_deg` | float32 | 0–360 |
| `vessel_name` | string | may be null |
| `imo` | int64 (nullable) | |
| `vessel_type` | string | e.g. Tanker, Cargo, Fishing, Passenger |
| `length_m`, `width_m`, `draft_m` | float32 (nullable) | |
| `status` | string | nav status |
| `gap_flag` | bool | this step falls inside a detected transmission gap |
| `source` | string | `"real"` or `"synthetic"` — drives the UI badge |
| `culprit` | bool | always present; True only for the planted vessel in synthetic data, always False for real |

Schema-equality is a hard requirement: real and synthetic parquet must have identical columns and dtypes (Nandha's engine and the UI must not care which they get).

### 4.3 Output — the 50-scenario benchmark

`data/ais/benchmark/scenario_<001–050>/` each containing `vessels.parquet` + `truth.json` (`{"culprit_mmsi": ..., "origin": {...}, "seed": ...}`). The whole set regenerates identically from its master seed.

### 4.4 Output — `provider_status.json` entries + your error classes

```json
{ "provider": "DMA", "purpose": "historical real AIS (EU waters)",
  "status": "WORKING", "last_code": 200, "last_latency_ms": 890,
  "last_success_utc": "2026-08-24T12:40:00Z",
  "last_failure_utc": null, "last_error_class": null,
  "chain": ["DMA", "MarineCadastre", "SyntheticGenerator"],
  "active_provider": "DMA" }
```

| Class | When | Main-system reaction |
|---|---|---|
| `ARCHIVE_UNAVAILABLE` | Archive site down / file missing | `mode=real` falls back to `mode=synthetic`, badge switches to SYNTHETIC |
| `EMPTY_REGION` | No vessels in bbox/window | UI message; widen window |
| `PARSE_ERROR` | Malformed archive rows beyond tolerance | Structured error with row counts |

## 5. Day-0 Setup

### 5.1 Accounts and credentials

None required (DMA and MarineCadastre are open bulk downloads; the MarineCadastre AIS format reference is at marinecadastre.gov/accessais). Optional stretch: AISStream.io free API key for the live tab — goes into `.env` and later Key Management.

### 5.2 Environment

```
pip install pandas pyarrow numpy shapely geopandas requests \
            pydantic pytest
```

### 5.3 Your folders

```
backend/app/services/ais/
├── dma_ingest.py          # Danish DMA CSV parser
├── mc_ingest.py           # MarineCadastre CSV parser
├── clean.py               # dedup, outlier removal
├── interpolate.py         # 5-min steps, tracks, gap detection
├── generator.py           # synthetic lanes + culprit planting
├── benchmark.py           # 50-scenario builder (seeded)
├── status.py              # provider_status.json writer
└── cli.py                 # fetch-ais / generate-ais
data/ais/                  # raw archives (gitignored),
                           # cleaned parquet, benchmark/
tests/                     # sample DMA day, mock origin-region JSON
```

### 5.4 Mock/test inputs

One sample day of Danish DMA CSV; a mock origin-region JSON for culprit planting; a fixed random seed for reproducible generation.

## 6. Build Plan — Phase by Phase

| Phase | Deliverable |
|---|---|
| 0 | Sample DMA day downloaded; environment up |
| 1 | Frozen parquet schema module + mock origin-region JSON |
| 2 | Real parser + cleaner (DMA first, MarineCadastre second) |
| 3 | Interpolation + track assembly + gap detection |
| 4 | Generator: lane traffic |
| 5 | Generator: culprit planting (region/time hit, slowdown, AIS gap) |
| 6 | 50-scenario benchmark, seeded and reproducible |
| 7 | Provider status + error classes + real→synthetic fallback |
| 8 | Tests, README, handover |

### Phase 2 — Real ingest + clean

☐ Parse raw CSV → typed frame; map columns to the §4.2 schema; verify timestamps are UTC at parse time.
☐ Dedup (mmsi + timestamp); drop impossible positions (on land, out of bbox) and physically impossible jumps (implied speed threshold).

### Phase 3 — Interpolation + gaps

☐ Per MMSI: sort, resample/interpolate positions to 5-minute steps (linear lat/lon is fine at this scale); recompute SOG/COG where needed.
☐ Gap detection: raw-report silence above a threshold (e.g. >15 min) marks the interpolated steps in that span with `gap_flag=True` — Nandha's AIS-gap suspicion factor reads this.
☐ Monotonic timestamps per MMSI; assemble per-vessel trajectories.

### Phase 4–5 — Generator

☐ Lane traffic: sample N vessels with types/dims/speeds from realistic distributions; move them along a few great-circle-ish lanes crossing the bbox plus some coastal noise; emit 5-min steps directly in the §4.2 schema with `source="synthetic"`.
☐ Culprit planting: one vessel whose track passes through the requested origin region within the requested window; apply optional behaviour — slowdown (e.g. 14 → 6 kn) near the origin and an AIS blackout of the requested minutes overlapping the window (`gap_flag=True`, no raw reports in the span); `culprit=True` on that vessel only.
☐ Everything driven by `seed` — same seed, byte-identical parquet.

### Phase 6 — Benchmark

☐ 50 scenarios from a master seed: varied bboxes/windows/traffic densities/culprit behaviours; each with `truth.json`.
☐ Regeneration test: rebuild from seed → identical files.
☐ Hand the folder + seed to Nandha; his Engine C computes top-1/top-3 hit rate on it.

## 7. Frozen Interfaces You Implement

```
fetch-ais    --bbox 80.10 12.90 80.55 13.35 \
             --start 2017-02-01T00:00:00Z --end 2017-02-02T06:00:00Z \
             --source dma|marinecadastre --out vessels.parquet

generate-ais --bbox ... --start ... --end ... --n-vessels 40 \
             --culprit-json origin_region.json --seed 42 \
             --out vessels.parquet

build-benchmark --scenarios 50 --master-seed 1337 \
                --out data/ais/benchmark/
```

Same behaviour exposed as Python functions: request dict in → parquet path out, or a structured error of §4.4.

## 8. Testing — the must-pass list

☐ Real: a raw archive day → valid parquet (no duplicates, monotonic timestamps per MMSI, UTC verified).
☐ Interpolation: a known gap filled at 5-minute steps; `gap_flag` set over the silent span.
☐ Failed/invalid request handled cleanly.
☐ Missing archive → `ARCHIVE_UNAVAILABLE`; empty region → `EMPTY_REGION`.
☐ Synthetic: planted culprit's track verifiably passes through the requested origin region and window.
☐ Schema-equality test: real and synthetic parquet have IDENTICAL columns and dtypes.
☐ Benchmark regenerates identically from its seed.
☐ Output validates against the contract schema.
☐ Cleaned real AIS exists for the proof scene; synthetic AIS exists for the headline scene.

## 9. Pitfalls and Traps

1. There is no real AIS for Indian waters — never promise it; the synthetic path with honest labelling IS the design, not a shortcut.
2. Schema drift between real and synthetic is the silent killer — one shared schema module both paths import; the equality test guards it.
3. DMA archive days are large — stream/chunk the parse (pandas `chunksize`), don't load whole files.
4. `culprit` exists in BOTH datasets (always False for real) — dropping it from real data breaks schema equality.
5. Interpolation must not bridge blackouts invisibly: the AIS-gap suspicion factor depends on `gap_flag` being set over interpolated-only spans.
6. MMSI is not unique per physical ship in dirty data (spoofing/duplicates) — dedup by (mmsi, timestamp) and drop impossible jumps rather than trusting identity blindly.
7. Longitude/latitude column order differs between archives — assert lat∈[−90,90], lon∈[−180,180] at the boundary.
8. Keep the master seed committed; an unreproducible benchmark is worthless as a metric.

## 10. Integration and Handover

### 10.1 How the main system calls you

```
Main System ── request JSON ──► [AIS DATA SERVICE]
            ◄── vessels.parquet ──
Attribution engine (Nandha) + UI vessel layers consume it.
```

On a `mode=real` failure, the main system falls back to `mode=synthetic` and switches the badge to SYNTHETIC.

### 10.2 Coordination points

Nandha — confirm the parquet columns once (already fixed by contract; his gates and AIS-gap factor read your columns). Everything else flows through files.

### 10.3 Handover checklist (before handing to Indhu)

One-command generation AND one-command real-ingest both work · benchmark committed with seed · schema tests green · README · column walkthrough with Nandha + handover walkthrough with Indhu. Then the common 10-point checklist applies: one-command run; input schema; output schema; example inputs; example outputs; test results; error behaviour per class; README; run instructions; integration instructions.

### 10.4 Definition of Done

Works independently · mock input works · real input works where applicable · output follows the contract · tests exist incl. failure cases · documentation exists · another developer can consume the output using only the contract · you can demonstrate the component without the main system.
