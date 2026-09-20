# BASELINE — regression contract (Phase 0)

These flows work end-to-end at HEAD `3838f88`. They must still work after
every phase. A flow may change its URL or layout; it may not lose capability
or start showing values that differ from the API.

## Executed starting point — 2026-09-21, HEAD `e999d5d`, before any P1 change

| Suite | Result |
|---|---|
| Unit (`npm test`) | **183 passed** / 13 files, 6.9 s |
| Backend (`pytest`, repo root) | **1302 passed, 4 skipped**, 0 failed, 5 m 37 s |
| E2E (`npx playwright test`) | **15 passed** / 15, 1.7 m |
| Build (`npm run build`) | **OK** — `index` 2,984 kB (gzip 779 kB), `maplibre-gl` 802 kB, CSS 217 kB |

Green start: any red after this point is a regression from this work.

E2E was run on the isolated stack, never the live DB: backend on :8010 with a
temp `DATABASE_URL` and a bootstrapped harness admin, `backfill_runs
--allow-unverified` to index `data/runs` (110 runs), `vite preview` on :5176
with `API_TARGET=http://127.0.0.1:8010`, then `E2E_BASE_URL`, `E2E_EMAIL`,
`E2E_PASSWORD` set. E2E was run alone (test 1 has a 5 s render budget that
concurrent pytest/vitest skews). The harness shares the real `data/` root;
`inv-*` run folders it creates are removed afterwards.

## How to check

| Suite | Command (from `main_system/frontend` unless noted) | Needs |
|---|---|---|
| Unit | `npm test` | — (~178 cases, 13 files) |
| E2E | `npx playwright test` | backend on :8000, real login, GPU flags per `playwright.config.js` |
| Backend contract | `pytest main_system/tests` (repo root, `.venv`) | — |
| Build | `npm run build` | — |

When a phase changes a URL, the e2e spec is updated to the canonical URL **and**
a redirect test for the legacy URL is added.

## A. Core investigation (must never regress)

| # | Flow | Guard |
|---|---|---|
| A1 | Create investigation → Replay → all stages done < 5 s → lands on attribution; every layer toggle enabled | e2e `investigation.spec.js` 1+10 |
| A2 | Characterisation numbers equal `/api/layers/{run}/slick` | e2e 2 |
| A3 | Ranking equals `suspects.json`; rank 1 opens breakdown with real weights; wording "Highest-Ranked Candidate", no culpability language | e2e 3 |
| A4 | Export bundle downloads the contract zip | e2e 3b |
| A5 | Printable report renders metadata, weights, suspects | e2e 3c |
| A6 | Time scrub moves the clock, page stays alive | e2e 4 |
| A7 | Stage navigation; `?stage=` survives reload | e2e 5 |
| A8 | Presentation plays beat by beat from real artefacts, holds on unfinished stages, stops on failure | e2e 6; unit `cinematic`, `useCinematic` |
| A9 | Works with all non-localhost traffic blocked | e2e 9 |
| A10 | Sidebars collapse independently; map takes the space | e2e W1 |
| A11 | Right panel shows the run's own numbers (hindcast, forecast areas, funnel, ranking, segmentation) | e2e W2 |
| A12 | SAR database → Analyse with deployed models → Find vessels → same run plays in workspace | e2e W6 |
| A13 | Live run start, cooperative cancel, re-run with same inputs | pytest `test_investigation_v2.py` (UI path untested) |
| A14 | Status endpoint survives corrupt artefacts; cancelled is not stale | pytest `test_investigation_page.py` |
| A15 | No mock fallback: unavailable stage = failed + no output | pytest `test_no_fake_fallback.py` |

## B. Evidence, incidents, reports

| # | Flow | Guard |
|---|---|---|
| B1 | Evidence: digest verify, per-stage provenance, decisions append-only ("guilty" is not a verdict) | pytest `test_decisions_api.py` |
| B2 | Auto-incident gate preview, create, recorded override, zone routing, alert | pytest `test_incident_auto.py` |
| B3 | Report compose → submit → publish (reviewer) → immutable; revise; CSV/JSON export | pytest `test_report_v2.py`; unit `incidentReport` |
| B4 | Report states what was not produced; LOW-confidence age; SYNTHETIC vs HISTORICAL labels | unit `incidentReport` |

## C. Registers and situational awareness

| # | Flow | Guard |
|---|---|---|
| C1 | Investigations register: 20+ records, each with position basis; filters; 403 on `/users` tolerated | e2e W3; unit `investigations` |
| C2 | SAR database lists real scenes, filters | e2e W4 |
| C3 | Upload without metadata is asked for it, never given it | e2e W5 |
| C4 | Vessels: search, dossier, live position, "no coverage" distinct from empty | manual |
| C5 | Globe: zones, incidents, live AIS with truncation notice; honest empty AIS state over Bay of Bengal | manual; unit `globe` |
| C6 | Zone create / reshape with revision check, self-intersection refusal, officer assignment | unit `globe`; manual |
| C7 | Alerts ack / dismiss-with-reason; unrouted banner | manual |
| C8 | Incident replay on map and globe | manual |

## D. System

| # | Flow | Guard |
|---|---|---|
| D1 | BAYES-TRACK: demo + from_run, 7 engines in order, WS feed with poll fallback, failing engine skips downstream | pytest `test_hindcast_api.py`, `_engines`, `_science` |
| D2 | Provider test / test-all, WORKING vs REACHABLE vocabulary | manual |
| D3 | Keys save + test (admin); audit verify + CSV; logs filter + clear | manual |
| D4 | Users: role change, deactivate with unrouted warning, zone assignment | manual |

## E. Shell and auth

| # | Flow | Guard |
|---|---|---|
| E1 | Cookie session, 401 = signed out, no token in storage | unit `session` |
| E2 | Public evaluator: no login, badge + Login button, refused writes show the server's reason | pytest `test_public_evaluator.py` |
| E3 | RBAC enforced server-side on every route | pytest `test_rbac_matrix.py` |
| E4 | ⌘K palette: one command per route, entity search, error ≠ empty, `?` overlay | unit `commandPalette` |
| E5 | Provenance chips, Zulu clock, health pulse with server vocabulary | unit `topbar` |

## Known not-working at baseline (not regressions if still failing before their phase)

The 7 BROKEN rows in the functionality matrix: four deep links, `Keys.jsx`
super_admin lockout, `?run=undefined`, blank `/report`.

## Manual flows

Rows marked "manual" have no automated guard today. P1 adds a Playwright smoke
spec that loads every route and asserts no console error and a non-empty main
region, so later phases have at least a tripwire for them.
