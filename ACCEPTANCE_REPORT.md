# OceanTrace — acceptance report (P20)

**SIH 2026 · PS 26143 · branch `feat/indhu-detection-pipeline` · executed 2026-09-08 (UTC)**
**Release candidate tag:** `v1.0.0-rc1`
**Plan:** master plan §14 (E2E-01…15) + OT-UAT-26143 P0 re-run + §15 gates.

## 1. Verdict

| | |
|---|---|
| E2E-01…15 | **13 PASS · 2 PARTIAL · 0 FAIL · 0 BLOCKED** (`acceptance_evidence/summary.json`) |
| UAT P0 (41 cases) | **36 PASS · 3 MISSING/declared · 0 FAIL · no PASS regressions**; two verdicts improved (`acceptance_evidence/UAT_P0_RERUN.md`) |
| Regression | **1006 passed, 4 skipped, 0 failed** on the final code (`acceptance_evidence/regression_pytest.txt`); frontend **72 passed** (`vitest.txt`). Baseline was 654; P19 left it at 979 |
| Flagship | `inv-gulf-flagship-20230108-2day` · digest **`fd42e078f8366110`…** unchanged before and after registry reconciliation; verifies over 8 artefacts; now addressable through the API, search, runs list, deep link and vessel index (`FLAGSHIP_RUN_CARD.md`) |
| §15 gates | providers `test-all` captured (CDSE + MarineCadastre WORKING; S2 + AISStream NOT_DEPLOYED); guilt-language grep: no accusatory use (benchmark ground-truth field `culprit` remains, documented); runbook rehearsed on the live registry twice (the screenshot script *is* the rehearsal) |

The two PARTIALs are stated, not smoothed: E2E-05's provenance annex cannot carry AIS provenance for a manifest written before the `ais` block existed, and E2E-15's "pipeline ≤ 60 s" is not met by a full-resolution scene (225–349 s measured). Details in §3.

Every verdict was produced by `scripts/acceptance_e2e.py` against the live registry, with the individual measured values in `acceptance_evidence/<ID>/verdict.json`; the honest-labels screenshot set was produced by `scripts/acceptance_shots.mjs` with the on-screen text read back from the DOM (`acceptance_evidence/ui_transcript.txt`). Nothing in this report was typed from memory.

## 2. The flagship: from unindexed artefacts to an addressable record

**What was wrong.** The frozen flagship existed as verified artefacts on disk and did not exist as far as the API was concerned: `GET /api/runs/inv-gulf-flagship-20230108-2day` → 404, `/api/search?q=flagship` → 0 results, `/api/vessels/367653160` → 404, top-bar strip `RUN UNREADABLE`.

**Why (inspected, not assumed).** Of 162 run directories, 105 are sealed (have a manifest). Every sealed run whose manifest was generated at or before `2026-09-07T02:01:08Z` had a registry row; every one generated after (`06:25:56Z` onward — exactly the five Gulf runs) had none, with no interleaving. Cause: only the API-driven path inserts the `runs` row; the Gulf family was produced by the CLI (`backend.services.pipeline.run`) and the existing reconciliation tool, `backend.backfill_runs`, had last been executed between those two timestamps. A **reconciliation omission on a known CLI-vs-API gap** — not a migration defect, not a schema problem. (`acceptance_evidence/reconcile_before.txt`)

**What was done, and the distinction preserved.**

| kind | what | touched? |
|---|---|---|
| sealed evidence | `data/runs/<id>/` artefacts + manifest + `artefact_digest` | **No.** Manifest sha256 `7e64a665…` and digest `fd42e078…` identical before/after; `/verify` ok (8) before/after (`reconcile_before.txt`, `reconcile_after.txt`). The digest is computed from files alone, so a registry write *cannot* move it — asserted by test, not assumed |
| index metadata | the `runs` row + `vessel_appearances` | Rebuilt from the manifest by the existing tool (`reconcile_run.txt`, `reconcile_vessels.txt`) |
| how the row exists | new nullable column `runs.registry_source` | `reconciled` for the five; `api` for API-observed runs from now on; `NULL` on the 99 pre-existing rows, which cannot say which they were |

Nothing was invented to make the row look complete: `investigation_id`, `incident_id` and `region` are NULL because the artefacts do not know them; `started_utc` is derived as `generated_utc − total_seconds` (07:58:11 → 08:04:36 = 384.3 s) rather than the previous "start = finish". The UI shows the run under its own id with an **UNFILED RUN** badge and an **INDEX RECONCILED** chip whose tooltip says exactly this, instead of borrowing an unrelated investigation's name.

**Rules added to the reconciliation** (16 tests, `main_system/tests/test_run_reconciliation.py`): only runs whose artefacts verify against their manifest are adopted (`--allow-unverified` says so in the output); adoption also indexes the run's ranked vessels (the flagship's rank-1 MMSI was unreachable for lack of this); `--dry-run` writes nothing; an API-observed row is never rewritten; idempotent.

**Result:** E2E-04 PASS on every check — searchable at tier 0, listed, deep link opens with REAL/RECONCILED chips, provenance correct, `/verify` ok, digest prefix `fd42e078f8366110`. MMSI 367653160 resolves with 4 appearances, rank #1 in each, `identity_available: false` (E2E-09).

## 3. E2E-01…15

Evidence directories under `acceptance_evidence/`. "ui-" files are screenshots from the fixed build.

| ID | Test | Status | What held / what did not |
|---|---|---|---|
| E2E-01 | Anonymous access | **PASS** | 24 `/api/*` probes → 401; `/health`, `/` → 200; route×role matrix suite green (`rbac_matrix_pytest.txt`) |
| E2E-02 | Investigator journey | **PASS** | incident → investigation (Chennai S1A) → run over the API the UI uses → SSE cascade shows all 5 stages → sealed, verifies; manifest carries git SHA + both model sha256; run filed under `INC-2026-002`; live CDSE search returned products; S2 → 501. Re-scored on the completed run `inv-9480a327d1-081049` after the harness process was interrupted (its live captures reused; stated in the verdict) |
| E2E-03 | UI never mock-runs | **PASS** | `stages_real == stages_total == 5`, `stages_mock == 0`, every stage source `real`; AIS for the Chennai scene is `synthetic` and is *labelled* so (`ui-journey-workspace.png`: `AIS SYNTHETIC`) |
| E2E-04 | Flagship real run | **PASS** | §2. `ui-flagship-workspace.png`, `ui-flagship-suspects.png` (`#1 MMSI 367653160`, Σwᵢsᵢ = 0.6691), `ui-palette-flagship.png`, `ui-runs-list.png` |
| E2E-05 | Synthetic honesty | **PARTIAL** | Bali run `inv-final-audit`: every vessel row `source: synthetic`, `suspects.source: synthetic`, report body says synthetic, gold `SYNTHETIC` badges on the map and rows (`ui-synthetic-workspace.png`). **Miss:** the provenance annex records no AIS provenance because this manifest predates the `ais` block; the top-bar chip therefore reads `AIS UNRECORDED` (the honest label for an absent record) rather than `SYNTHETIC`. Newer manifests carry the block (E2E-02's does) |
| E2E-06 | Hindcast uncertainty | **PASS** | 25 ellipses, all non-zero; `origin_uncertainty_km 0.3658` (the plan's 0.99 is the Chennai scene's value); method string says *not ML*; final ellipse within 0.5–5× the particle 1-σ spread; `/api/metrics.drift` experimental/not applied/evaluated negative, no accuracy claimed |
| E2E-07 | Metrics truth | **PASS** | `unet-r34-fullcorpus-e48` + sha256, `0.5723`, `0.4445`, `280/5,248` all on Analytics (`ui-analytics.png`); drift residual EXPERIMENTAL on the page; nothing named YOLOv8; S2 NOT_DEPLOYED in the catalogue, 501 on search |
| E2E-08 | Attribution integrity | **PASS** | weights Σ = 1.0; Σwᵢsᵢ reproduces every total (max error < 0.001); ranks monotone; funnel 32 → 4 ranked / 28 filtered; served suspects == disk; zero-candidate behaviour evidenced by the engine's own tests (no sealed zero-suspect run exists and none was manufactured — rule 11) |
| E2E-09 | Vessel dossier | **PASS** | 367653160: 4 appearances across the flagship family, rank #1 in each, name null / `identity_available false`, tracks endpoint answers, found by MMSI in search |
| E2E-10 | Report lifecycle | **PASS** | analyst drafts → submit → analyst cannot publish (403) → reviewer publishes → second publish 409 (immutable) → export JSON/CSV stamped with digest + version → revise creates v2 draft; annex lists 8 artefact hashes == manifest == `/verify` |
| E2E-11 | Runs reconcile | **PASS** | every sealed run has a row; no sealed run marked in flight; no row claims running without a live job; 50 unsealed directories correctly absent; paged listing (pages of 5) == brute force, no overlap/gap, monotone; status and text filters == brute force |
| E2E-12 | Alerts | **PASS** | dry-run poll `errors: []`, opened nothing; live poll on `danish-straits` raised 3 new-scene alerts each linking an investigation → ack → assign to a real user → reads `assigned`; SSE delivery covered in-suite (an SSE endpoint never ends, so it is not consumed from the harness) |
| E2E-13 | Audit chain | **PASS** | 13 distinct event types; every session-originated event has actor + IP (12 system/CLI events carry no IP — recorded as such); `/api/audit/verify` ok; a tampered `detail` on the newest chained row of a **copy** of the database fails verification |
| E2E-14 | Cancel | **PASS** | cancel accepted mid-detect → job `cancelled` → no manifest → `/verify` says "never completed" → absent from replay list → UI badge **CANCELLED** (`ui-cancelled.png`; it read COMPLETE before the fix in §5) |
| E2E-15 | Tiles & perf | **PARTIAL** | tiles z8/10/12/14: 24.5 / 45.2 / 63.7 / 53.9 KB (≤ 256 KB); `origin_cloud?lite` 432 KB vs 1.59 MB and faster (medians 69 vs 109 ms, 5 samples); regression 1006 passed. **Miss:** pipeline ≤ 60 s — measured **225.57 s / 229.72 s** uncontended and 349.31 s with the acceptance suite running alongside, on a full-resolution 28072×21162 Sentinel-1 scene. The 60 s figure predates full-resolution scenes; nothing was tuned to approach it |

## 4. UAT P0 re-run

`acceptance_evidence/UAT_P0_RERUN.md`: 41 P0 cases, 36 PASS (23 re-executed today, 13 carried on unchanged evidence, each labelled as such), 3 MISSING/declared (OT-B-05 EO, OT-F-06 hindcast ML, OT-N-01/02 verdicts), **0 FAIL, no PASS regressions**. OT-H-06 and OT-N-03 (real historic AIS) moved from UNPROVEN to **PROVEN** on the flagship's MarineCadastre run, which is now also a registry record. The compliance verdicts are quoted verbatim: EO not deployed; hindcast ML experimental, evaluated negative (0/6), disabled; real AIS executed end-to-end.

## 5. Defects found by running the acceptance — all fixed, all with tests

Each was invisible until real data, real load or a screenshot hit it.

1. **The flagship family had no registry rows** (§2). Reconciliation now verifies before adopting, indexes vessels, stamps `registry_source`. 16 tests.
2. **The default runs list showed 18 of 110 runs.** `runs.archived` had been added by `ALTER TABLE`, leaving 92 rows NULL, and the listing filtered `archived IS FALSE`. Every run older than the column vanished without anyone archiving it — including the real Chennai run and the Bali run this plan names. `init_db()` now gives such rows their default and the filter is NULL-safe. `test_schema_catchup.py` reproduces the exact ALTER TABLE history.
3. **A deep-linked run showed another run's data.** `/investigation?run=<flagship>` had the flagship's title and chips over the Chennai run's slick, spill panel and suspects — the status poll targeted the selected investigation's latest run, not the URL's. Caught by the screenshot transcript (centroid 13.37 N 80.43 E under a Gulf run). Status is now polled for the run on screen.
4. **A cancelled run read COMPLETE.** `status.json` is never rewritten on cancel (the writer stops — that is the point), so its `state` stayed `running` and the badge logic saw "stages ran, none failed". The registry row is now the authority once a run has ended (backend) and the badge has a CANCELLED state (UI). `test_investigation_page.py`.
5. **Four concurrent pipelines crashed the process.** An AOI poll found `auto_run: true` on `danish-straits` in the live registry and started three full pipelines on top of one already executing; the backend died in HDF5 with a segmentation fault (`exit 139`, log in §6). Pipeline execution is now serialised through one gate (`OT_MAX_CONCURRENT_RUNS`, default 1) on every launch path, so a fan-out queues. `test_crash_recovery.py`.
6. **Dead runs claimed to be running forever.** After the crash, three rows stayed `running` with no live thread. A boot-time sweep marks in-flight rows with no manifest `failed` with the reason ("server restarted while this run was in flight"), and their jobs with them; sealed-but-unmarked rows are reported for `backfill_runs --refresh`. Same test file; live effect in `sweep_after_crash.txt`.
7. **Analytics did not show the checkpoint identity, its sha256, or the drift EXPERIMENTAL status**, and rounded the C-10 numbers to three decimals (0.572 ≠ 0.5723 on the deck). Now rendered as the API states them.
8. **Slick age showed without LOW confidence** ("6.9 h" read as a measurement). Standing rule 7; now `23.7 h · LOW confidence`.
9. **A suite test depended on the developer's `.env`** (`test_bootstrap_does_nothing_without_credentials` failed once `OT_ADMIN_*` was set — the documented production setup). The test now controls its own inputs.

Harness-side, for whoever re-runs this: the session cookie is `Secure`; httpx drops it on plain `http://` while curl keeps it, so every authenticated check 401'd on the first pass while login succeeded. The harness carries the cookie explicitly (`client()` docstring).

## 6. Recorded, not fixed

- **Pipeline duration** ≈ 225 s on a full-resolution scene vs the plan's 60 s (E2E-15). The deployed segmenter walks the whole raster; a faster figure would mean a different model or a cropped scene, and neither was done.
- **HDF5 reads are not thread-safe.** Serialising pipelines is a mitigation; the metocean readers themselves were not made re-entrant. `OT_MAX_CONCURRENT_RUNS` above 1 reintroduces the crash.
- **Scheduler-started runs have no `jobs` row**, so they are not cancellable from the UI and their progress is not reported through `/api/jobs`. The sweep handles them on restart; the gap remains.
- **A manifest written before the `ais` block** yields `AIS UNRECORDED` in the strip and no AIS line in the annex (E2E-05). Honest, but coarser than a newer run.
- **Data & Models** shows deployed/NOT_DEPLOYED status but not the model sha256 (Analytics does).
- **On a run whose attribution stage was mocked** (`inv-gulf-rehearsal-20230108`) the top-bar AIS chip reads `REAL` while the AIS layer badge and suspect rows read `SYNTHETIC`. Both are true of different things — the manifest's `ais` block records the archive *selected* for the run; the layer badge reflects the vessels the mocked stage actually *served*. The chip's tooltip carries the manifest's own sentence. A chip that also reflected the served layer would be the better strip; recorded here rather than changed after the evidence was captured.
- **A stale backend from an earlier session was found on `:8000`** on this machine; acceptance ran on `:8010`. The runbook covers both.
- The three `aoi-danish-straits-*` investigations opened by the live poll (E2E-12) remain in the registry as `failed` with the restart reason — they are real records of what happened, left as such.

## 7. Honest-labels screenshot set

`acceptance_evidence/*/ui-*.png`, text read back in `ui_transcript.txt`:

| file | shows |
|---|---|
| E2E-01/ui-signin-form.png | sign-in gate |
| E2E-04/ui-flagship-topbar.png | `inv-gulf-flagship-20230108-2day · AIS REAL · STAGES 5/5 REAL · INDEX RECONCILED · ENGINE ML · ⌗ FD42E078` |
| E2E-04/ui-flagship-workspace.png | title = run id, **UNFILED RUN**, `SCENE REAL`, Gulf slick (27.81 N −90.19 E) |
| E2E-04/ui-flagship-suspects.png | "investigative support, not proof of guilt"; weights printed; `#1 MMSI 367653160` |
| E2E-04/ui-palette-flagship.png, ui-runs-list.png | ⌘K and the runs list reach the flagship |
| E2E-05/ui-synthetic-*.png | gold `SYNTHETIC` layer badges and rows; `MV DEMO ALPHA` labelled synthetic |
| E2E-06/ui-flagship-spill-panel.png | `Age estimate 23.7 h · LOW confidence`, engine ML, source REAL |
| E2E-07/ui-analytics.png, ui-catalog-models.png | identity + sha, 0.5723 / 0.4445 / 280/5,248, EXPERIMENTAL; NOT DEPLOYED rows |
| E2E-10/ui-report.png | digest on the report, `#rank`, no guilt words |
| E2E-12/ui-alerts.png | three real new-scene alerts |
| E2E-02/ui-monitoring-providers.png | REACHABLE vs WORKING, NOT DEPLOYED rows |
| E2E-14/ui-cancelled.png | **CANCELLED**, `STAGES NO MANIFEST` |
| E2E-02/ui-journey-workspace.png | the UI-launched run: `INC-2026-002 ▸ … · AIS SYNTHETIC · 5/5 REAL` (no INDEX chip: the API watched it) |

## 8. How to reproduce

```
# backend on the live registry (see DEMO_RUNBOOK.md §0–1 for accounts)
set PYTHONPATH=main_system && .venv\Scripts\python.exe -m uvicorn backend.main:app --port 8010
cd main_system\frontend && set API_TARGET=http://127.0.0.1:8010 && npx vite build && npx vite preview --port 5175

.venv\Scripts\python.exe scripts\acceptance_e2e.py                 # E2E-01,04–13,15
.venv\Scripts\python.exe scripts\acceptance_e2e.py --live-runs     # + 14, 02, 03 (minutes)
node scripts\acceptance_shots.mjs http://localhost:5175            # screenshot set
.venv\Scripts\python.exe scripts\flagship_run_card.py > acceptance_evidence\FLAGSHIP_RUN_CARD.md
.venv\Scripts\python.exe -m pytest > acceptance_evidence\regression_pytest.txt
```

Environment: Windows 11, Python 3.10 `.venv`, Node 24, ONNX Runtime 1.23.2 on CUDA. Accounts used: `operator@` (admin), `analyst@`, `reviewer@` — `@oceantrace.local`, seeded per `acceptance_evidence/seed_review_roles.txt`; credentials live in the gitignored `.env`.
