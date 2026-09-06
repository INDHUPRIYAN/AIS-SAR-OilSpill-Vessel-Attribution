# ais_service — AIS ingest, cleaning, synthesis and benchmark (Krishnan / Dev 4)

Produces **`vessels.parquet`**, the frozen Contract 6 consumed by the
attribution engine (Engine C) and the UI. Sources: bulk AIS archives
(MarineCadastre, DMA), or the synthetic generator with a planted culprit —
the designed ground-truth path for scenes where no public AIS exists.

## The contract, honoured at source

Every output path of this service emits **exactly** the 14 columns of
`contracts/schemas/tabular.py::VESSEL_COLUMNS` and passes
`validate_vessels_df()` before handover:

```
mmsi int64 (9-digit) · timestamp_utc (tz-aware UTC) · lat · lon ·
sog_kn [0,60] · cog_deg [0,360] · heading_deg (AIS 511 -> NaN) ·
vessel_type {tanker,cargo,bulk,fishing,passenger,tug,other} ·
length_m · width_m · draught_m · source {real,synthetic} ·
interpolated bool · culprit bool          — sorted by (mmsi, timestamp_utc)
```

The old internal spellings (`timestamp`, `draft_m`, `gap_flag`, Title-case
vessel types) are gone from the outputs; `ais/contract.py::to_contract()` is
the single funnel that renames, maps types, fixes dtypes and sorts. The main
system keeps a *tolerant* shim
(`main_system/backend/services/pipeline/run.py::engine_native_vessels`) only
so that OLD run artefacts still on disk keep working; new files need no
renaming.

## Module map

| Module | Role |
|---|---|
| `ais/mc_ingest.py` | MarineCadastre bulk-CSV parser (US waters). Renames raw headers to contract names, maps numeric AIS ship-type codes. |
| `ais/dma_ingest.py` | Danish Maritime Authority bulk-CSV parser (`web.ais.dk`, day-first timestamps). |
| `ais/clean.py` | Dedupe, sort, bbox/coordinate bounds, **MMSI 9-digit validation**, heading-511 -> NaN, SOG clamp/drop outside [0,60], COG wrap, >60 kn jump rejection. Drop counts land in `df.attrs["clean_stats"]`. |
| `ais/interpolate.py` | 5-min resampling; `interpolated=True` on every row that was filled in rather than transmitted (any empty bin, not just >15-min gaps). |
| `ais/generator.py` | Kinematically integrated synthetic traffic with a culprit planted at the computed origin (slowdown, dump-and-turn, real AIS gap = missing rows), plus configurable **hard negatives**. |
| `ais/contract.py` | `to_contract()`: projection of any internal frame onto the frozen 14-column contract. |
| `ais/benchmark.py` | 50-scenario benchmark builder (deterministic, master seed 1337, ocean-only origins). |
| `ais/status.py` | Real reachability probes of the archive endpoints; writes `provider_status.json` per the `ProviderStatusFile` contract. Never crashes offline. |
| `ais/index.py` | **Spatial + temporal index** over the AIS archive (design doc v2 §8, §12). Partitioned Parquet store, per-partition bbox and time span in `_index.json`, buffered-origin-cloud query with a shapely `STRtree` fine pass. |
| `ais/cli.py` | The commands below. |

## The spatial index (design doc v2 §8)

> "The spatial index is mandatory for production. A full scan over 20 mock
> vessels is instant; over a real coastal month it is minutes per attribution.
> Partition by region + time, index on (geom, timestamp_utc), and query the
> buffered origin cloud directly."

`ais/index.py` implements the **partitioned-Parquet** arm of §12
("TimescaleDB / partitioned Parquet"). PostGIS was not chosen because the demo
must boot with zero credentials and no server process (§19, §28); the query
surface is deliberately the one a PostGIS store would expose, so swapping the
backend later is a new class rather than a rewrite.

```
data/ais/store/
├── _index.json                       ← the index: per-partition bbox,
│                                       time span, row/MMSI counts, sha256
├── region=denmark/
│   ├── date=2026-02-01/part.parquet   ← contract-shaped, 14 columns
│   └── date=2026-02-02/part.parquet
└── region=chennai/
    └── date=2017-02-01/part.parquet
```

Three levels of pruning, cheapest first: **partition prune** from the manifest
(a 6-hour origin window touches one or two of a month's 30 partitions),
**column prune** on read, then the exact bbox/time test — and for
`query_cloud`, a shapely `STRtree` over the surviving points queried with the
buffered origin polygons.

Two behaviours worth knowing:

* **The buffer is kilometres, not degrees.** One degree of longitude is 111 km
  at Chennai and 63 km in the Kattegat, so buffering a lon/lat geometry by
  `km/111.32` degrees would reach only `cos(lat)` of the stated distance
  east-west — 57 % of it in Danish waters — and silently drop the vessels in
  between. The buffer is applied in a locally-scaled planar frame instead.
* **`query_cloud` returns whole tracks by default.** Attribution scores
  trajectory and course change, which need the approach and departure legs;
  returning only the in-cloud fixes would flatten the trajectory factor without
  saying so.

```bash
# Load a bulk archive once (clip on the way in — MarineCadastre ships one CSV
# per day for the whole US coast)
python -m ais.cli index-ingest --store ../data/ais/store --region denmark \
    --source dma --input-file data/ais/dma_2026-02-01.csv \
    --bbox 10.2 55.2 12.6 57.2 --start 2026-02-01T00:00:00Z --end 2026-02-02T00:00:00Z

# ...or an already contract-shaped parquet
python -m ais.cli index-ingest --store ../data/ais/store --region chennai \
    --input-file vessels.parquet

# What the archive holds
python -m ais.cli index-stats  --store ../data/ais/store
python -m ais.cli index-verify --store ../data/ais/store   # hashes still match?

# Stage 6's actual query: who entered the buffered origin cloud, and when
python -m ais.cli index-cloud --store ../data/ais/store \
    --cloud ../data/runs/inv-001/origin_cloud.geojson \
    --start 2017-02-01T12:00:00Z --end 2017-02-02T01:00:00Z \
    --buffer-km 5 --out vessels.parquet

# Plain bbox + window
python -m ais.cli index-query --store ../data/ais/store \
    --bbox 10.2 55.2 12.6 57.2 \
    --start 2026-02-01T00:00:00Z --end 2026-02-01T06:00:00Z

# The index is regenerable from the partitions — it is not a single point of
# failure, it is just cached knowledge about them.
python -m ais.cli index-rebuild --store ../data/ais/store
```

Ingest is **merge, not clobber**: re-ingesting a bucket reads the existing rows
back, de-duplicates on `(mmsi, timestamp_utc)` with last-write-wins, and
rewrites with a fresh content hash — so a corrected re-ingest supersedes an
earlier one instead of duplicating it. Rows that fail the frozen contract are
dropped and **counted** in the ingest report, never silently coerced.

## Hard negatives (honest benchmarking)

`--hard-negatives N` (default 3) plants innocent vessels that NEARLY qualify
as the culprit, cycling three kinds:

1. **crosses the origin cloud, outside the time window** (right place, wrong time),
2. **inside the window, never enters the cloud** (right time, wrong place),
3. **AIS gap far from the slick** (suspicious silence, unrelated location).

They carry MMSIs `990000000+` (legacy fleet) and `culprit=False`; the
benchmark's `truth.json` lists them under `hard_negative_mmsis`. They use a
dedicated RNG stream, so the main fleet is bit-identical for a given seed
whatever the count.

## CLI

Run from `ais_service/` with the repo-root venv (`../.venv/Scripts/python.exe`):

```bash
# Parse a local bulk-CSV archive (nothing is downloaded by these commands)
python -m ais.cli fetch-ais --source marinecadastre --input-file data/ais/raw.csv \
    --bbox -91.0 28.0 -89.0 30.0 --start 2023-01-01T00:00:00Z --end 2023-01-01T06:00:00Z \
    --out vessels.parquet

# Synthetic traffic with a planted culprit + 3 hard negatives
python -m ais.cli generate-ais --bbox 80.10 12.90 80.55 13.35 \
    --start 2026-08-24T00:00:00Z --end 2026-08-24T06:00:00Z \
    --n-vessels 20 --culprit-json culprit.json --seed 42 --hard-negatives 3 \
    --out vessels.parquet

# Rebuild the 50-scenario benchmark (deterministic; ~20 s)
python -m ais.cli build-benchmark --scenarios 50 --master-seed 1337 \
    --hard-negatives 3 --out test_output/benchmark/

# Probe providers and write provider_status.json (short timeouts, offline-safe)
python -m ais.cli status --out provider_status.json
```

`culprit.json` shape:

```json
{
  "origin": {"lat": 13.05, "lon": 80.45,
             "window_start_utc": "2026-08-24T01:30:00Z",
             "window_end_utc": "2026-08-24T03:30:00Z"},
  "axis_deg": 62.0,
  "behaviour": {"slowdown": true, "ais_gap_minutes": 47}
}
```

## Tests

```bash
# from the repo root (pytest.ini wires the import roots)
.venv/Scripts/python.exe -m pytest ais_service/tests -q
```

76 tests: contract validation of generator and ingest outputs, clean.py edge
cases (511 heading, bad MMSI, out-of-range SOG/COG, dedupe, jump rejection),
`interpolated`-flag correctness, hard-negative semantics, benchmark
determinism (seed 1337 twice -> identical `truth.json` and parquet),
provider-status probing with graceful offline degradation, and 30 covering the
spatial index — partition pruning agreeing with a full scan, merge-not-clobber
re-ingest, a km buffer that is a true circle rather than a degree box, and a
missing partition degrading with a warning rather than raising. No test touches
the network.

## Data-source coverage (read before pointing this at a demo)

* **MarineCadastre** — US waters only, daily bulk CSVs (NOAA).
* **DMA** — Danish waters only (`web.ais.dk`), day-first timestamps.
* **Indian waters have no public bulk AIS archive.** That is why the
  synthetic generator is mandatory infrastructure, not a fallback: it is the
  only path with known ground truth, and every synthetic row says so in its
  `source` column.

## Reproducibility notes

* The committed benchmark under `test_output/benchmark/` was built with
  `master_seed=1337`, `hard_negatives=3` and **`global-land-mask` installed**
  (in `requirements.txt`). The land mask is part of the generator: without it
  tracks cross land, and scenario geometry differs. Rebuilding on the same
  environment is bit-identical.
* `build-benchmark` refuses to write a scenario whose culprit track lost all
  its fixes — a scenario without ground truth is worse than no scenario.
