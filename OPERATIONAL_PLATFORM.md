# OceanTrace — operational platform layer

Written for: the next developer picking this up (and for the demo rehearsal).

This document covers the work that turned the completed SIH analytical pipeline
(P00–P20, see `HANDOFF.md`) into an operational platform: zones, roles, live
AIS, automatic incidents with routing, the 3D globe, and the ops surfaces. It
is additive — **no existing engine, contract or artefact was modified**, and
the flagship run was not re-run or touched.

Read `HANDOFF.md` first for the pipeline, the flagship and the standing rules.
Every one of those rules still holds; two of them are now satisfied by
different facts, and those two are called out below.

---

## 1. Test position

| suite | before | after |
|---|---|---|
| Python | 956 passed | **1067+ passed** |
| Frontend (vitest) | 72 passed | **96 passed** |

**There is an environment blocker, and it is not a code regression.**

```
ImportError: DLL load failed while importing lib:
An Application Control policy has blocked this file.
```

A Windows Application Control policy on this machine blocks **pyarrow's**
native DLL. That fails 98 tests (27 failures + 71 errors), every one of them
`ImportError: Unable to find a usable engine; tried using: 'pyarrow',
'fastparquet'`. Before debugging anything, run:

```
.venv/Scripts/python.exe -c "import pyarrow"
```

If it raises that error, the 98 are environmental. Verified:

- `fastparquet` (the alternate pandas engine) is blocked identically, on
  `cencoding`. It was installed to test this, then uninstalled.
- Both `.venv` and `.venv-drift` are affected — it is machine-wide.
- `numpy`, `pandas`, `shapely`, `pyproj`, `rasterio`, `onnxruntime` still
  import, so it is not all native code.

**One failure hides its cause.** `test_tiles.py::test_a_bad_bbox_is_refused`
reports `assert 404 == 422`, not an ImportError, because
`analytics.vessels_geojson` wraps `pd.read_parquet` in `except Exception:
continue` and then 404s on "no readable vessels.parquet" *before* validating
the bbox. Same root cause.

Fixing it needs a policy change (Smart App Control / WDAC exclusion, or
reinstalling the pyarrow build that was previously allowed), not a code
change. **Do not add a CSV fallback** — `vessels.parquet` is Parquet in the
frozen contract, and changing that to dodge a machine policy would be a
contract change.

---

## 2. What was built

### Operational zones (`[ZONES]` d5b2976)

`Zone`, `ZoneAssignment`, `ZoneRevision`. A zone is **not** a second `Aoi`: an
AOI answers "where should the scheduler search", a zone answers "whose desk
does this land on".

- **Bay of Bengal is seeded** as one `jurisdiction` (protected) plus six
  `operational` divisions. The divisions are **computed** — each is the
  intersection of the theatre with a lat/lon band — so containment holds by
  construction and moving the theatre re-derives them instead of breaking
  them. Measured: 2,139,498 km², divisions tile it to **coverage 1.0**.
- The theatre is an **offshore monitoring boundary, not a coastline and not a
  maritime claim**, jurisdiction code `INTL`. Every seeded division carries
  "Asserts nothing about EEZ, territorial sea or national jurisdiction" in its
  `notes`, and the UI renders that text.
- **Protected boundaries**: only `super_admin` may change a jurisdiction's
  geometry. An `admin` may rename it. A `zone_officer` may reshape only zones
  they are assigned to. Enforced in `services/zones.py`, over HTTP, with a
  403 that names which zones the caller does own.
- Geometry is GeoJSON text with Shapely predicates — **not PostGIS**, see §4.
- Optimistic concurrency: a geometry edit must carry the `revision` it was
  drawn against, or it is a 409.

### Live AIS (`[LIVE-AIS]` 8abd7ce)

AISStream was registered `NOT_DEPLOYED`. It is deployed now, and the original
objection — "a stream cannot answer questions about a scene acquired in the
past" — is answered by **archiving** it into the same day-partitioned
`AISStore` the bulk providers write to.

Verified against the real provider. North Sea, 40 s: **1021 messages → 827
positions, 186 statics, 6 duplicates caught, 799 unique vessels, 781 named,
368 (46%) with heading NULL.** Full evidence: `dev_evidence/LIVE_AIS/README.md`.

**Three defects found by running it**, all fixed and tested:
1. Go timestamps (`2026-09-12 04:33:21.123456789 +0000 UTC`) defeated
   `fromisoformat` on three counts. The first implementation returned None for
   **every real message** — a stream that connects, reports healthy and
   ingests nothing.
2. `SubscriptionConfirmation` was counted as traffic, so `functionally_working`
   held from the instant of connection. It reported **working with zero
   vessels stored**.
3. `StaticDataReport` nests fields under `ReportA`/`ReportB`, so every Class B
   vessel silently had no name.

### Automatic incidents and zone routing (`[ROUTING]` 4be99bb)

`sealed run → validation gate → Incident → point-in-polygon → Zone →
responsible officer → routed Alert`, hooked into the run-completion path.

**The gate introduces no tuned constant** (standing rule 11). Confidence is
compared against `tiling.detect_threshold` from the frozen
`normalisation.yaml` — the same 0.5 the segmenter was calibrated at from a
recorded sweep. Area is **not** a gate (`AUTO_INCIDENT_MIN_AREA_KM2` defaults
to 0.0, off). The gate that does real work is `engine == "ml"`: a run whose
segmenter fell back to threshold-morphology does **not** open a case.

Three counts are never conflated, because on the flagship they are 31, 361 and
**62**: screen-classified oil, screen-classified look-alike, and segmenter
regions (which carry no class at all). Every verdict carries a
`counting_note` saying so.

Routing has four named outcomes: `zone`, `escalated`, `unzoned` (no zone
covers it), `unassigned` (a zone does, nobody holds it). The last two both
mean "nobody will see this" and are distinguished because the fixes differ.
Neither is quietly handed to an administrator to tidy the queue.

### 3D globe and the zone-splitting editor (`[ROUTING]` 4be99bb)

Built to D3's revised scope: deck.gl `_GlobeView` with a real sphere mesh
occluding the far hemisphere, the same layer definitions as the 2D view, and a
canvas swap rather than a basemap morph (`_GlobeView` genuinely cannot host
MapLibre). 2D remains the default.

The same canvas is the boundary editor, because zone splitting is a spatial
judgement made against the traffic and incidents being divided. Cursor lat/lon
at **six decimals** (what will actually be stored), degrees-decimal-minutes
alongside, coordinate search accepting both, self-intersection detected **while
drawing** with the offending edges highlighted, undo/redo with branch
invalidation, and the server's own 422/409 text shown verbatim.

### Accounts, roles and the hindcast benchmark (`[ACCOUNTS+BENCHMARK]` 6087c01)

`super_admin` and `zone_officer` joined `ROLES`. `super_admin` is implicit in
`require_role` everywhere `admin` was, so **no existing route changed meaning**;
what it holds exclusively is `require_super_admin`.

User CRUD did not exist, which made the officer model undeliverable. It exists
now, with the escalation rules enforced server-side: only `super_admin` grants
a role, an `admin` cannot create or modify an `admin`, nobody deactivates
themselves, and the last active `super_admin` cannot be removed.

**The hindcast benchmark is honest about not having run.** `/api/models/hindcast`
reports `selection_basis`, which distinguishes three cases a UI would otherwise
render identically:

| basis | meaning |
|---|---|
| `measured` | a benchmark ran and this is its winner |
| `untested_candidate` | ML is trained, nothing was compared; the primary is a default |
| `candidate_untrained` | ML has no weights and has never predicted anything |

Current state is **`candidate_untrained`**. v1 (per-step residual) is a real
completed benchmark with a real loser — 6 of 6 held-out fields degraded, mean
−359.7%, physics 20.3 m vs ML 220.7 m — and is disabled on that evidence. v2
(`ml_origin.py`, origin correction) is implemented, **untrained**, and
therefore unmeasured. Physics is primary *because v2 was never measured*, not
because it won. Saying otherwise would fabricate a benchmark, which cuts both
ways.

### Ops surfaces

- **`/api/logs`** — there was no log store at all; diagnostics went to stdout
  via `print()`. Now a bounded ring buffer on the root logger, filterable by
  level (a **minimum**, so filtering for WARN cannot hide a CRITICAL), logger,
  substring, run, job and incident. The boot `print()`s were converted to
  logging so the buffer is not hollow at startup.
  The response states its own limits: lost on restart, bounded, cannot see
  `print()`, and **is not the audit trail** (`/api/audit` is, and it is
  hash-chained).
- **`/api/workers`** — the background threads this process really runs, with
  measured liveness. It does **not** invent a fleet: a disabled worker is
  listed as disabled with the setting that would enable it, and **GPU reports
  `measured: false`** rather than 0%, because nothing here measures one.

### A latent bug this work exposed

`audit.record(..., commit=False)` called twice in one transaction left **both**
rows with `prev_hash = GENESIS`, because `SessionLocal` is `autoflush=False`
so the second call never saw the first pending row. `/api/audit/verify` went to
`ok=False` — for a tamper-evidence chain, the worst available failure, since it
makes an intact system indistinguishable from an altered one. Surfaced the
moment automatic incident creation began emitting `incident.create` and
`alert.route` together. Fixed at source (`db.flush()` on the no-commit path)
with two regression tests.

---

## 3. Standing rules — two now rest on different facts

Both were true when written and are no longer:

- **Rule 3 / provider honesty: "AISStream is NOT_DEPLOYED."** It is deployed.
  `test_provider_honesty` asserted the old truth and failed, which is the suite
  working. The tests were **rewritten to assert the new truth**, not deleted —
  including the caveat that replaced the deployment one (receiver coverage,
  below). Sentinel-2 is still `NOT_DEPLOYED` and `?source=S2` still returns
  501.
- **D3 / "the 3D globe is dropped."** The new requirement mandates it; it is
  built to D3's own revised scope.

Everything else holds unchanged. In particular rule 4 (**hindcast is
PHYSICS**) is intact: no ML contributes to any origin, and the benchmark
endpoint says why in terms of what was and was not measured.

---

## 4. Known gaps — recorded, not hidden

### AISStream has no coverage of the Bay of Bengal

Measured 2026-09-12: the seeded theatre returned **zero messages in 60 s**
while a globally-bounded subscription on the same key delivered a firehose
immediately. AISStream is relayed by volunteer receivers and has effectively
none over the northern Indian Ocean.

Consequences, which a demo must not paper over:
- The live vessel layer over the Bay of Bengal **will be empty**, and the UI
  shows the coverage sentence rather than an empty map.
- The live archive is **not** a record of Bay of Bengal traffic.
- Neither bulk provider covers it either (MarineCadastre is US waters, DMA is
  Danish/Baltic).
- For a live-AIS demonstration, point the subscription at covered water. The
  zone model makes that a matter of drawing a zone.

### PostgreSQL + PostGIS is not done

The spec asks for it; this runs on SQLite. Geometry is GeoJSON text with
Shapely predicates, which behaves identically on both, so migrating is a
`DATABASE_URL` change rather than a rewrite. What is given up is server-side
spatial indexing; the `bbox_json` prefilter in `zone_for_point` is the
substitute. **This is a deployment task, and nothing in the repo claims
PostGIS.**

### Not built

- Alert routing **configuration** (`/api/alert-rules`): routing works and is
  derived from zones and assignments; there is no UI for severity thresholds
  or escalation rules.
- The v2 hindcast model is **untrained**. Training needs real CMEMS + ERA5
  forcing and time; `python -m engines.drift.train_origin` is wired, and
  writing `docs/qa/evidence/final/ml_hindcast/origin_correction_benchmark.json`
  makes `/api/models/hindcast` report a real comparison with no code change.
- The **archive write path is not executed end-to-end** — it is implemented and
  its failure behaviour is tested (a failed flush re-buffers and loses
  nothing), but Parquet is blocked on this machine. One test is SKIPPED with
  that reason; re-run `pytest main_system/tests/test_ais_live.py -rs` once
  Parquet works.
- Route-level code-splitting for the maplibre/deck bundles (pre-existing P18
  backlog item; the build still warns over 500 kB).

---

## 5. New API surface

```
GET    /api/zones                       list (geometry off by default)
GET    /api/zones/geojson               one serialiser for 2D and globe
GET    /api/zones/mine                  your zones + report scope
GET    /api/zones/lookup?lon=&lat=      which zone owns a point, and whose desk
GET    /api/zones/{id}                  + children, can_edit, reason
GET    /api/zones/{id}/revisions        who moved this boundary
POST   /api/zones                       draw (top-level = super_admin only)
PATCH  /api/zones/{id}                  rename / restatus / reshape (revision required)
DELETE /api/zones/{id}                  refused if children or incidents exist
POST   /api/zones/{id}/assignments      assign an officer (admin)
DELETE /api/zones/{id}/assignments/{uid}

GET    /api/ais/live                    live picture (states `truncated`)
GET    /api/ais/live/geojson
GET    /api/ais/live/{mmsi}
GET    /api/ais/status                  connected vs functionally_working + archive span
GET    /api/ais/zones/summary           per-zone vessel counts (zeros included)
POST   /api/ais/stream/start|stop|flush admin
POST   /api/ais/live/prune              admin

GET    /api/incidents/auto/preview/{run_id}   the gate's verdict, acts on nothing
POST   /api/incidents/auto/{run_id}           run the gate (?force= to overrule)
POST   /api/incidents/backfill-zones          admin

GET    /api/users  /api/users/{id}  /api/users/{id}/zones    admin+
POST   /api/users                                             admin+
PATCH  /api/users/{id}                                        admin+ (role = super_admin)
GET    /api/roles                                             any role

GET    /api/models/hindcast             the benchmark and the selection basis
GET    /api/logs                        searchable application log
POST   /api/logs/clear                  admin, audited
GET    /api/workers                     threads, jobs, host; GPU not measured
```

New frontend routes: `/globe`, `/my-desk`, `/zones`, `/officers`.

---

## 6. Where to pick up

1. **Unblock pyarrow** — it gates the AIS archive, the contract tests and the
   flagship's vessel layer. Nothing else can be closed out until it works.
2. Train and benchmark the v2 hindcast, so `/api/models/hindcast` reports
   `measured` instead of `candidate_untrained`.
3. For the demo: decide whether live AIS is shown over covered water (honest,
   works) or the Bay of Bengal (honest, empty, with the coverage note). Do not
   show the second while describing it as the first.
4. Alert routing configuration, if the demo needs severity thresholds.

Run the suites:

```
.venv/Scripts/python.exe -m pytest            # ~7 min; run it in the background
cd main_system/frontend && npm test
```
