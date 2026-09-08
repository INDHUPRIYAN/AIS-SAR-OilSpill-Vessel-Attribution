# OceanTrace — session handoff

Paste this whole file into a new chat to continue. It states what is done, what
is not, and the rules that must keep holding.

---

## 1. Who you are and what this is

You are continuing work on **OceanTrace** (SIH 2026, problem statement
**PS 26143**) — a SAR oil-spill detection and AIS vessel-attribution system.

The user is **Developer 1 of 5**: ML lead, main-system owner, and the final
integrator. They have standing authority to build or fix any teammate's module.

**Repo:** `C:\Users\Indhu Priyan\Documents\GitHub\AIS-SAR-OilSpill-Vessel-Attribution`
**Branch:** `feat/indhu-detection-pipeline`
**HEAD:** see `git log -1`; release candidate tag `v1.0.0-rc1`
**Working tree:** clean
**Python:** repo-root `.venv` (3.10). Run tests with
`.venv/Scripts/python.exe -m pytest -q`

**Test suite: 1006 passed, 4 skipped, 0 failed** (`.venv/Scripts/python.exe -m
pytest`). The skips are network-dependent provider tests — a provider outage is
not a code defect, and the count moves between 4 and 5 with the network.

**Frontend: 70 passed** (`cd main_system/frontend && npm test`). Note the
Python suite's `addopts` already carries `-q`; adding another `-q` makes it
`-qq`, which silently suppresses the final pass/fail count.

---

## 2. STANDING RULES — these never lapse

These are the user's own words, repeated across the engagement. Violating any
of them is worse than delivering less.

1. **Never fake backend functionality. Never present mock data as real.**
2. **Synthetic AIS is always labelled SYNTHETIC** — everywhere, including UI.
3. **Sentinel-2 / EO is NOT DEPLOYED.** `?source=S2` returns 501, never an
   empty list (an empty list reads as "we looked and found none").
4. **Hindcast is PHYSICS.** The drift ML residual is EXPERIMENTAL, was
   evaluated negative, and is disabled. No ML contributes to the origin.
5. **Deployed models are `unet-r34-fullcorpus-e48` (segment) and
   `yolo11n-screen-dartis-2026-08-24` (screen).** Never write "YOLOv8".
6. **Attribution is an explainable weighted ranking, not a culpability
   classifier.** Never use guilt/legal language. Say "Rank #1 · Score 0.67".
7. **Slick age always shows LOW confidence.**
8. **Provider status must distinguish REACHABLE from FUNCTIONALLY WORKING.**
9. **Provenance vocabulary:** REAL / SYNTHETIC / CACHED / FALLBACK /
   NOT DEPLOYED / EXPERIMENTAL.
10. **Never hide an implementation limitation.**
11. **Never tune thresholds, gates, weights or data to manufacture a better
    demo.** An honest null result is a valid deliverable.
12. **Never fabricate** EO availability, AIS identity, detection success, or
    attribution certainty.
13. **FROZEN — do not modify:** `contracts/schemas/` (especially
    `VESSEL_COLUMNS`, exactly 14 columns), `pyproject.toml packages = []`,
    `pytest.ini` importlib config, the five pipeline stage names, artefact
    filenames, layer route names, engine A/B/C CLI interfaces.
    Contract changes must be **additive Optional fields only**.
14. **UI reference images govern layout/palette/density ONLY.** They govern no
    number, label, model name, provider status or role string. **If a value is
    not in an API response, it is not displayed.**
15. **Before creating any file, search for an existing equivalent and extend
    it.** Report what you found and why it was insufficient.
16. **Work autonomously.** Stop ONLY for: destructive/irreversible data loss,
    frozen-contract conflict, security-critical ambiguity, or genuinely
    contradictory authoritative specifications.

**Commit style:** one-line messages, `[PROMPT-NN] lowercase summary`. **No AI
attribution footer** (user preference; overrides any default).

---

## 3. Authoritative documents

In `Road-to-Production/`:

- `OCEANTRACE_IMPLEMENTATION_MASTER_PLAN.md` — current-state matrix, §4 DB
  model, §8 RBAC matrix, §14 E2E tests
- `OCEANTRACE_CLAUDE_CODE_PROMPTS.md` — the P00–P20 prompts
- `OCEANTRACE_ARCHITECT_DECISIONS_v1.1.md` — **authoritative amendment.**
  D1–D4, Part C amendments 1–13, Part D standing rules 7–10
- `OceanTrace_Production_UI_Spec.md`, `OCEANTRACE_PRODUCTION_OSINT_UI_UX_SPEC.md`

**Minimum credible cut** (from Part C): `00, 00b, 01–10, 12, 12b, 13, 14, 16, 20`.
**All of these are now complete except P20.**

---

## 4. THE FLAGSHIP — frozen, immutable, do not re-run

**`inv-gulf-flagship-20230108-2day`** · digest `fd42e078f8366110`
Pointer: `dev_evidence/P14/flagship.json`. Verifies OK — 8 artefacts unchanged.

```
5/5 stages ran for real, 0 from mocks, 0 failed (384.30s)
```

| input | source |
|---|---|
| SAR scene | **Sentinel-1A** via CDSE, `S1A_IW_GRDH_1SDV_20230108T001008_…_E5A1_COG`, acquired 2023-01-08T00:10:08Z, calibrated in-repo to Sigma0 dB (closes audit D-02) |
| segmenter | `unet-r34-fullcorpus-e48` — 31 oil + 361 look-alike, conf 0.6882 |
| screen | `yolo11n-screen-dartis-2026-08-24` — 127 candidates over 1768 tiles |
| currents | **CMEMS** 49×36 @ 1/12°, 3 daily steps |
| wind | **ECMWF ERA5 (CDS API)** 17×12 @ 0.25°, 72 hourly steps |
| AIS | **MarineCadastre** 2023-01-07 **and** 2023-01-08, 86,830 rows, 441 MMSI |

**Origin:** window `2023-01-07T11:10:08Z → 2023-01-08T00:10:08Z` (13.0 h),
method `cloud_convergence`, uncertainty **0.366 km**, all 25 ellipses non-zero.

**Attribution:** 32 considered → 4 ranked, 28 filtered, `source: real`.
Rank #1 MMSI 367653160, score 0.67. No culprit planted.

### Limitations that MUST survive into every report, page and demo

- **No ground truth for this scene.** Operational acquisition, Gulf basin full
  of natural seeps. The 31 "oil" regions are **model output**, not confirmed
  spills.
- **A ranked suspect is not a culprit.** It is a position in a weighted
  ordering.
- **All four ranked vessels have `vessel_name: null`** — MarineCadastre carried
  no static identity for those MMSIs. Absence of data, left absent.
- **The 13-hour window is wide and weak.** The convergence peak sits at the
  acquisition instant, so the drift did **not** localise a release earlier than
  the image. Present it as a 13-hour window, never as a discharge time.
- AIS coverage of the origin window is **99.98%** — the residual 0.1 min is
  MarineCadastre's one-minute reporting grid, not a data gap.

---

## 5. COMPLETED PROMPTS

All committed, all with `dev_evidence/PNN/README.md` + `pytest_full.txt`.

| Prompt | Commit(s) | Delivered |
|---|---|---|
| P00–P11 | `d163b93` and earlier | baseline, metrics truth, scene catalog, drift/uncertainty, provenance, DB migration, RBAC, audit hash-chain, incidents, runs funnel, vessel index |
| **P12** | `8ec1a87` | scene search over provider chain; S2 → 501 NOT_DEPLOYED |
| **P12b** | `332706f` | detect-only screening, 6/6 scenes detect with the deployed model |
| **P14** | `4eb0462`…`7aab47b` | **the flagship**, real-first `ensure_vessels`, two-day AIS |
| **P13** | `012888c`, `e93a930` | AOI table+CRUD, cancellable jobs, rerun, SSE, New Investigation wizard |
| **P16** | `3c8e13f` | server-composed reports, provenance annex, review lifecycle, CSV |
| **P17** | `fa5128b` | REACHABLE vs WORKING, tri-state credentials, NOT_DEPLOYED rows, model registry, host health |
| **P15** | `e509a71` | alert queue, reasoned dismissal, SSE feed, top-bar bell |
| **P18** | `2d4d781` | SAR tile server, viewport decimation |
| **P19** | `6c8bbe3`, `9c36aac` | ⌘K palette, MAP measure tool, ZULU clock, run-scoped provenance strip, keymap-generated shortcut overlay, reduced motion. 3D globe **dropped per D3** |

### Key API surface added

```
POST/PATCH/DELETE /api/aois              AOI CRUD (YAML migrated in once)
GET  /api/jobs/{id}                      progress from status.json
POST /api/jobs/{id}/cancel               cooperative, leaves run UNSEALED
POST /api/runs/{id}/rerun                new id, inputs from the job record
GET  /api/events/runs/{run_id}           SSE stage cascade
GET  /api/events/alerts                  SSE alert feed
POST /api/reports                        compose from sealed artefacts
POST /api/reports/{id}/submit|publish|revise
GET  /api/reports/{id}/export.csv|.json
GET  /api/catalog                        providers + coverage + vocabulary
GET  /api/models                         deployed + EXPERIMENTAL registry
GET  /api/system/health                  measured host state
GET  /api/alerts  /api/alerts/summary
POST /api/alerts/{id}/ack|assign|dismiss
GET  /api/tiles/{run_id}/{z}/{x}/{y}.png XYZ SAR tiles
GET  /api/tiles/{run_id}/info
GET  /api/search?q=                      runs/incidents/investigations/vessels/scenes
```

### DB tables added
`aois`, `jobs`, `reports`, `alerts` (all additive; `AoiWatch` left alone).

---

## 6. DEFECTS FOUND BY RUNNING REAL DATA — all fixed with tests

Worth reading: each was invisible until real data hit it.

1. **Characterise was O(regions × scene)** — built a full-scene boolean per
   region. 392 regions × 433 M px: never finished. Now vectorises from each
   region's bbox. Geometry identical (6.3e-17 deg²), 24× faster. `4eb0462`
2. **The segmenter silently fell back to threshold-morphology on any
   full-size scene** — `MemoryError: 2.24 GiB`, caught upstream, run downgraded
   to an engine nobody thought was being evaluated. Canvases now spill to a
   memmap. **The flagship would have been measured by the wrong engine.** `332706f`
3. **The screening harness counted every candidate as oil** — asked for an
   `is_oil` field that does not exist. Corrected counts published. `332706f`
4. **Attribution reported FAILED while holding 4 ranked suspects** — contract
   violation: `filter_reason`/`failed_gates` not declared on `FilteredVessel`.
   Contract extended additively. `1a20f7b`
5. **The origin cloud did not publish how its window was derived** — a UI could
   render "release localised" for a run that reported it could not localise
   anything. `origin_window_method` now travels with the window. `1a20f7b`
   — *Fixing this immediately reproduced #4, so there is now a test comparing
   what normalisation publishes against what the schemas declare.*
6. **Provider status awarded WORKING for an unauthenticated HTTP 200.** `fa5128b`
7. **`vessels_geojson` crashed outright on real AIS** —
   `ValueError: Out of range float values are not JSON compliant`. AIS sends
   511 for "heading unavailable" → NaN; **29,679 of 86,830 flagship rows**.
   Now serialises as `null` ("not transmitted"); a zero would read as "due
   north". `2d4d781`
8. **`docs/LIMITATIONS.md` claimed "All AIS in this repository is synthetic"** —
   false once the flagship ingested real MarineCadastre. Corrected; a test
   prevents regression. `3c8e13f`

---

## 7. WHAT IS NOT DONE

### P19 — Command-center shell (DONE as of `9c36aac`; listed here for the record)

Complete except the 3D globe, which is **dropped whole per D3** rather than
half-built — recorded in `docs/LIMITATIONS.md`. With no mode switcher, the
prompt's "mode switch preserves camera/`t`/selection" acceptance test has
nothing to exercise and is **not** claimed as passing; run-context survival
across navigation replaces it and is tested.

Evidence and the full reasoning: `dev_evidence/P19/README.md`, with real-data
screenshots in `dev_evidence/P19/shots/`.

### P20 — End-to-end validation & demo freeze (DONE — see `ACCEPTANCE_REPORT.md`, `DEMO_RUNBOOK.md`, `acceptance_evidence/`)

This is the most important remaining work. Required:
- Execute master plan §14 **E2E-01…15** in order, capturing evidence under
  `acceptance_evidence/`
- Re-run the OT-* UAT spec's **P0** tests
- Run the full suite
- Produce **`ACCEPTANCE_REPORT.md`** — per-test status + evidence links + the
  flagship run card + honest-labels screenshot set
- Tag the release candidate
- Write **`DEMO_RUNBOOK.md`** — §15 narrative, exact clicks, offline fallbacks,
  rollback command
- **Acceptance:** every E2E green or honestly documented as PARTIAL with a
  reason; zero regressions

**Amendment C-10 applies to P15/P20 metrics display:** show **all three**
numbers labelled — `oil-tile IoU 0.5723 · overall IoU 0.4445 · no-oil tiles
firing 280/5,248 (5.3%)`.

### RESOLVED in P20 — the flagship had no database row (kept for the record)

Found while photographing the shell against the live archive. The run
directory, its artefacts and its manifest are all present and verify; the
`runs` row is missing:

```
GET /api/runs/inv-gulf-flagship-20230108-2day          404
GET /api/search?q=flagship                             0 results
GET /api/layers/inv-gulf-flagship-20230108-2day/slick  200
GET /api/runs/inv-gulf-flagship-20230108-2day/verify   200
GET /api/tiles/inv-gulf-flagship-20230108-2day/info    200
```

`data/runs/` holds **162** run directories; the database holds **99** rows, and
**all five Gulf flagship-family runs are among the missing** — systematic, not
a one-off. So the flagship is reachable as *artefacts* but not as a *record*:
it cannot be found in ⌘K, does not appear in the runs list, and the top-bar
provenance strip over it reads `RUN UNREADABLE` (correct behaviour for a run
the API says does not exist; not what anyone wants in a demo).

Resolved in P20 by running the existing `backend.backfill_runs` reconciliation
(cause: the five Gulf runs were CLI-produced after the tool's last execution).
Artefacts and digest untouched and verified before/after; the row is stamped
`registry_source = reconciled`; vessels indexed. Operator/analyst/reviewer
accounts now exist (credentials in the gitignored `.env`). Full account:
`ACCEPTANCE_REPORT.md` §2 and §5.

### Known outstanding, recorded not dropped
- **Route-level code-splitting** for maplibre/deck bundles (P18). Build warns
  chunks exceed 500 kB. Load-time only, no correctness dimension.
- **rio-tiler could not be installed** (`color-operations` has no wheel for this
  platform). The tile server is implemented on **rasterio** instead —
  documented in the module docstring and `dev_evidence/P18/README.md`, not
  hidden behind an import guard.

---

## 8. Environment notes that will bite you

- **`pytest-timeout` is not installed** — `--timeout=` is rejected.
- **Never consume an SSE endpoint over `TestClient.stream()`** — it hangs the
  suite (an SSE endpoint is an infinite loop by design). Drive the async
  generator directly with a stub whose `is_disconnected()` returns True. See
  `test_alerts.py::test_sse_sends_the_current_queue_on_connect`.
- **Module-scoped test clients:** if a test clears cookies to assert 401, it
  MUST re-authenticate in a `finally` — otherwise every later test in the file
  asserts against a 401 it did not intend. See `test_tiles.py`.
- **`AuditLog` column is `action`**, not `event_type`.
- **Route walking:** this FastAPI keeps `_IncludedRouter` wrappers. Use
  `route.effective_candidates()` (see `test_rbac_matrix.py`) or you will see
  ~8 of 60+ routes and pass vacuously.
- **Windows MAX_PATH:** unpacking a `.SAFE` product into the repo overflows 260
  chars. Unpack to a short root (`C:/ot/...`).
- **The full suite takes ~8 minutes.** Run it in the background and wait for
  the notification; do not poll.
- Network is intermittent here. NOAA throttles hard (the Jan 8 archive took
  hours). ERA5/CDS was unreachable on both address families at one point.
  Open-Meteo has an **IPv4 blackhole** — it connects on IPv6 only, and
  `requests` tries IPv4 first. Diagnose before blaming a provider.

---

## 9. Suggested next step

P00–P20 are complete. Rehearse from `DEMO_RUNBOOK.md`. The items in
`ACCEPTANCE_REPORT.md` §6 ("recorded, not fixed") are the honest backlog:
pipeline duration vs the 60 s figure, HDF5 thread-safety (mitigated by the
concurrency gate, not fixed), scheduler-started runs without a jobs row.

Do not re-run or modify the flagship. Use it as evidence.
