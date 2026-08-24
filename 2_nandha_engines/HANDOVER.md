# Handover — Nandha's core engines

Handbook Part G's 10-point checklist, ticked with evidence, plus Part H's definition of
done. Everything below is verifiable by running the commands shown.

## Part G checklist

| # | Requirement | Evidence |
|---|---|---|
| 1 | Working code, runnable from **one command** | `python scripts/run_all.py` — all three engines, ~3.5 s, no network, no GPU |
| 2 | Input definition (schema + description) | [INTEGRATION.md](INTEGRATION.md); per-engine READMEs |
| 3 | Output definition (schema + description) | Pydantic models in `engines/schemas/`, enforced before every write |
| 4 | Example input file(s) committed | `samples/inputs/` — 1.1 MB, six files, `MANIFEST.json` |
| 5 | Example output file(s) committed | `samples/{slick,origin_cloud,forecast,suspects}` — one pipeline run |
| 6 | Test results recorded | 177 tests, `python -m pytest`; benchmark in `benchmark/RESULTS.md` |
| 7 | Error behaviour documented per class | [INTEGRATION.md](INTEGRATION.md) § "Error classes" |
| 8 | README with setup and known issues | [README.md](README.md), [KNOWN_ISSUES.md](KNOWN_ISSUES.md) |
| 9 | Instructions to run | [README.md](README.md) § Run; per-engine READMEs |
| 10 | Instructions to integrate | [INTEGRATION.md](INTEGRATION.md) |

## Part H — definition of done

| Requirement | Status |
|---|---|
| Works independently | Yes — no network, no GPU, no teammate |
| Mock input works | Yes — `samples/inputs/`, regenerable via `scripts/make_samples.py` |
| Real input works where applicable | **Untested.** No real SAR mask, NetCDF or AIS parquet has reached me yet. The readers accept the contract formats plus common aliases, but this is the open risk. |
| Output follows the contract | Yes — validated before write, and the committed samples are re-validated by `tests/test_contract_samples.py` |
| Tests exist, failure cases tested | Yes — all four error classes, plus degradation paths |
| Documentation exists | Yes |
| Another developer can consume the output using only the contract | Yes — schemas + committed samples + INTEGRATION.md |
| Demonstrable without the main system | Yes — `scripts/run_all.py` prints the ranked suspect list |

## Verify it yourself in three commands

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m pytest                     # 177 passed
python scripts/run_all.py            # end to end, prints the suspect list
```

Expected tail of the third command:

```
Slick   DEMO-A_slick_01: 14.882 km2, 7.899 x 2.399 km at 62.0 deg, age ~29.7 h (low confidence)
Origin  2017-02-01T12:39:42Z -> 2017-02-02T00:39:42Z (peak ..., cloud_convergence, euler)
Spread  +6h 16.5 km2, +12h 21.7 km2, +24h 35.3 km2

Suspects (2 ranked, 4 filtered out):
  #1  0.945  MV DEMO TRADER (Tanker)
      Passed through the 90% origin region at ..., slowed from 13.8 to 5.9 kn, had a
      55-minute AIS gap overlapping the estimated discharge window, ...
```

## What is NOT done

1. **Phase 3 — OpenDrift.** Not started; no conda on this machine. Drift runs on the
   in-house Euler integrator, which is complete and analytically validated. The
   architecture slide's "OpenDrift OpenOil" is currently aspirational, and
   `environment.yml` is the untested skeleton file. Handbook §5.2's escalation rule
   covers this: the project proceeds on the Euler fallback.
2. **Real-data validation.** Everything is verified against synthetic inputs with known
   ground truth. First contact with Pavitra's, Keerthana's and Krishnan's real files is
   still ahead.
3. **The 30-minute walkthrough** with Indhu — to be scheduled.

## Talking points for the walkthrough

- The four contract files and where each field comes from.
- Two deliberate deviations: `suspects.json` carries an extra `origin_window` block; the
  temporal gate means "in the region *during* the window", which changes what the UI's
  "outside time window" badge implies (INTEGRATION.md § Deviations).
- Why `age_hours_est` will disagree with the hindcast window, and why the hindcast window
  is the number to lead with.
- `origin_cloud.geojson` is 2.4 MB — agree a cadence before the UI opens several at once.
- The benchmark: 86% top-1 / 100% top-3, and why the hard tier is deliberately at 62%.
- Repo hygiene items that are his to fix (KNOWN_ISSUES §9): the `.gitignore` `docs/` rule
  leaving all four handbooks untracked, and `contracts/schemas/` not matching the
  handbook contracts.
