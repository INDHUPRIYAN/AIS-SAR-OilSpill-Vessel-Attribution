# P20 — End-to-end validation & demo freeze

The deliverables live at the repo root, where a judge or an operator looks
first:

- **`ACCEPTANCE_REPORT.md`** — verdict (13 PASS · 2 PARTIAL · 0 FAIL over
  E2E-01…15), the flagship reconciliation, the UAT P0 re-run summary, nine
  defects found and fixed during acceptance, and the honest-labels screenshot
  set.
- **`DEMO_RUNBOOK.md`** — §15 narrative with exact clicks, offline fallbacks,
  rollback.
- **`acceptance_evidence/`** — one directory per E2E test with `verdict.json`
  (every measured value), captures and screenshots; the flagship run card
  (`FLAGSHIP_RUN_CARD.md`, rendered from the artefacts by
  `scripts/flagship_run_card.py`); `UAT_P0_RERUN.md`; the full regression and
  frontend suite outputs; the reconciliation before/after record.

Harness: `scripts/acceptance_e2e.py` (API, repeatable), `scripts/acceptance_shots.mjs`
(UI), both documented in the report's §8.

## The one thing to know

The frozen flagship (`inv-gulf-flagship-20230108-2day`, digest `fd42e078f8366110…`)
was **not in the registry** — a CLI run that the existing `backfill_runs`
reconciliation had never been run over. It is now, via that same tool, with
its artefacts and digest untouched (verified before and after), a
`registry_source = reconciled` stamp on the row, and nothing invented for the
fields the artefacts do not know. See report §2.

## Tag

`v1.0.0-rc1` on the commit that carries this evidence.

## Regression

`acceptance_evidence/regression_pytest.txt`: 1006 passed, 4 skipped, 0 failed.
`acceptance_evidence/vitest.txt`: 72 passed.
