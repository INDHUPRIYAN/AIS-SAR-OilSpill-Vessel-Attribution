# OCEANTRACE — ARCHITECT DECISIONS & PROMPT-PACK AMENDMENTS (v1.1)

In response to the reconnaissance report. Findings B1, B2, B3, H1–H6, M1–M8 are **accepted** unless amended below. This document supersedes the conflicting parts of `OCEANTRACE_CLAUDE_CODE_PROMPTS.md`; everything not mentioned stands.

---

## PART A — THE FOUR DECISIONS

### D1 · Flagship run (B3) → **SCENE-FIRST, with a detect-first refinement and a two-day AIS window**

Do not force the 2023-01-01 parquet. Revised procedure, executed as amended PROMPT 12 → 12b → 14:

1. **Search** CDSE for Sentinel-1 IW GRDH scenes over **US waters covered by MarineCadastre** (Gulf of Mexico preferred; any US EEZ acceptable), any date in 2023 where the archive exists. Prefer acquisitions **after ~12:00 UTC** so a backward origin window stays inside the same UTC day.
2. **Detect before you commit.** Download 3–5 candidate scenes and run **detection only** on each. Pick a scene where the deployed model genuinely fires (≥1 oil candidate surviving look-alike rejection, confidence recorded). This converts the risky step — "does a slick exist?" — into a cheap measured filter *before* any AIS download.
3. **Then ingest AIS for that scene's date AND the preceding day** (two daily MarineCadastre archives, ~600 MB total). Two days is deliberate insurance: it guarantees the origin window is covered regardless of acquisition hour, and removes the trap `run.py:558` documents.
4. Run the full pipeline. Seal, verify, record the run id.

**Honesty guardrails, binding.** If the flagship yields `NO_VESSELS_IN_WINDOW`, or a low-scoring top candidate, **that is the result** — record it, present it, do not tune gates or weights to manufacture a culprit. A real run that honestly finds no strong suspect is still a far stronger SIH artefact than a synthetic run that finds one, and it demonstrates the exclusion ledger doing its job.

**Accepted fallback (plan for it now, don't improvise later).** If no candidate scene detects a slick within the time budget, split the flagship into two honest artefacts rather than faking one: (a) the **detection flagship** — the existing real S1 scene with real detection; (b) the **real-AIS flagship** — pipeline run over a real Gulf scene with real MarineCadastre AIS where the slick may be weak or absent. Label each for exactly what is real. Two honest runs beat one overstated one.

### D2 · Weight-profile editor (H4) → **DEFER to post-SIH. Struck from scope.**

Ship for SIH: read-only `GET /api/attribution/weights` + a `profile_hash` = sha256 of the `weights:` block of `analysis_engines/config/attribution_weights.yaml`, recorded into `manifest.json` at run time, displayed as a badge (`profile default-v1 · a3f9…`). No `weight_profiles` table, no editor, no publish flow, no "current v5" comparison. **Do not build a half-version.** P16 Administration scope = users/roles/credentials/config-read only. Amend both UI specs' P16/P10 text accordingly at build time.

### D3 · 3D mode (M3) → **CUT the basemap morph. 3D is optional polish, last in, first out.**

You are right that `_GlobeView` cannot host a MapLibre basemap; the 450 ms projection morph in the OSINT spec §2.2 is therefore **struck**. Revised scope for PROMPT 19: a **globe-with-deck.gl-layers view** — dark sphere, landmass outline/hex fill, graticules, and the *same* deck.gl layer definitions the 2D view already uses (incidents, vessels, slick, origin, forecast). Mode switch is a **canvas swap** that preserves camera target, timeline `t` and selection — not a morph. 2D remains the default and the fallback.

Landmass data: a slimmed Natural Earth 110m countries GeoJSON (~250 KB, properties stripped) vendored into the frontend as a static asset — no new runtime dependency.

**Build it only if PROMPT 17 lands with time to spare.** If the schedule tightens, drop 3D entirely; the audit and the master plan both name it first to cut, and nothing depends on it.

### D4 · Working tree (M1) → **YES, checkpoint it — but as three commits, not one blob.**

Before PROMPT 00b:
1. `git tag pre-production-<date>` (or a branch) on current HEAD — the rollback anchor.
2. **Gitignore, don't commit, the data outputs.** Any `.parquet`, `.json`, `.tif`, run directories, or test artefacts under `data/`, `*/test_output/`, `uat_evidence/`, `audit_evidence/` go into `.gitignore` and stay untracked. Commit the `.gitignore` change alone.
3. Commit remaining *source* changes as one checkpoint: `[PROMPT-00b] checkpoint: pre-production working tree`.
4. Report the final `git status` (expected: clean) and the file counts moved to each bucket in `dev_evidence/P00b/`.

If any untracked file is ambiguous (source or output?), list it and ask — do not guess.

---

## PART B — CORRECTIONS TO THE UI-VIOLATION TABLE

The table is accepted and is the highest-value output of the review. Three amendments, all in the direction of *more* precision:

- **#32 "Dark Period" → RELABEL, not remove.** AIS-gap detection genuinely exists (`gap_min_minutes: 15`, `gap_saturation_min: 30`; live evidence `ais_gap_minutes: 50`, sub-score 1.0). The defect is the invented vocabulary, not the capability. Correct label: **"AIS Gap"** with the measured minutes. The three *behaviour* anomalies remain slowdown / course-change / loitering, and must not be conflated with the gap factor — they are separate scored factors (0.10 vs 0.15).
- **#27 CMEMS resolution → state the cached grid, not the nominal product.** GLORYS12 is nominally 1/12° (≈0.083°), but the audited run's cached currents grid was **11 lat × 11 lon × 3 daily steps**. The honest UI string names what was actually used, e.g. `CMEMS GLORYS12 · cached · 11×11×3 grid`. Nominal resolution alone would overstate fidelity.
- **#20 thickness and #21 oil type → removals confirmed.** Thickness is an assumed constant (`assumed_thickness_m = 1.0e-3`) that *sets the entire age scale* and is never emitted; no oil-type classifier exists in any of the five services. If you want a belt-and-braces check, grep `oil_type|crude|emulsi|classif` across `analysis_engines/`, `scene_service/`, `main_system/backend/services/` and record the null result in `dev_evidence/P00/`. Absent evidence to the contrary, both stay REMOVE.

Everything else in the table stands as written, including the four most dangerous entries: **#4 XGBoost attribution** (attribution is deterministic Σwᵢsᵢ — presenting it as a learned model is the single worst claim in the set), **#18 the arithmetic that doesn't sum** (0.747 ≠ 0.82 — every displayed total must be computed from real sub-scores), **#17 fabricated vessel identity** (owner/operator/year-built/photographs are not derivable from a 14-column AIS contract), and **#19 the missing SYNTHETIC badge**.

**Rule to apply globally:** the images govern layout, density, palette and composition. They govern **no** number, label, model name, provider status, or role string. Every such value comes from the API or is not displayed.

---

## PART C — AMENDMENTS TO THE PROMPT PACK

| # | Prompt | Amendment |
|---|---|---|
| 1 | **NEW P00b** | Working-tree hygiene per D4. Runs after P00, before P01. |
| 2 | **P01, P05** | Pin absolute paths: weights YAML = `analysis_engines/config/attribution_weights.yaml`; model card = `main_system/backend/services/detection/weights/model_card.md`. **Explicitly forbid** `weights/previous_poc/model_card.md` and `data/runs/training/unet-r34-fullcorpus/model_card.md`. Sum==1.0 validation applies to the **`weights:` block only** — not `gates:`, `priors:`, or `scoring:`. |
| 3 | **P03** | Strike the pyproject option entirely (`packages = []` is deliberate). Copy the in-repo bootstrap pattern at `services/pipeline/ais_index.py:25-27`. Cover **`scene_service`, `ais_service`, `metocean_service`**. Smoke test must import without `pytest.ini`'s pythonpath. |
| 4 | **P05** | Add `profile_hash` of the `weights:` block into the manifest (D2). |
| 5 | **P06** | DB guardrails per H3: assert resolved DB path + row counts before migrating; refuse a DB with 0 runs unless `--allow-empty`; delete/gitignore the `main_system/data/oceantrace.db` orphan as a separate commit. |
| 6 | **P07** | Add **vitest** setup here (first frontend touch), not P19. Also: make CORS origins configurable via env with `Secure` cookie flag off-localhost (M5). |
| 7 | **P10** | Funnel contract redefined (H5): `{found, indexed, after_spatial, after_temporal, after_trajectory, candidates, reasons_histogram}` — six honest counts, sourced from `gates.py:209 apply_gates` per-gate failures. Wire this shape into P09's UI too. |
| 8 | **P11** | **Do not touch `VESSEL_COLUMNS` or `contracts/schemas/tabular.py`.** Capture `name/imo/call_sign` at the raw-CSV boundary in `ais_service/ais/mc_ingest.py` (existing `col_map`) and write them **into the `vessels` DB table** as a side channel. Synthetic runs leave them null — never fabricate. |
| 9 | **P12 ⇄ P14** | Reordered per D1: P12 (scene search) → **new P12b** (candidate scene detect-only screening) → P14 (ingest matching AIS days + flagship run). P12 joins the minimum credible cut. |
| 10 | **P15** | Metrics display must show **all three** numbers labelled (H2): `oil-tile IoU 0.5723 · overall IoU 0.4445 · no-oil tiles firing 280/5,248 (5.3 %)`. Amend E2E-07 to assert all three. |
| 11 | **P19** | 3D scope reduced per D3 (canvas swap, no morph, no basemap). Vitest moved out to P07. |
| 12 | **P00** | Re-baseline all counts into the acceptance file (routes 38, investigations 137, runs 91, api_calls 7,647, etc.). **Never quote the plan's audit-time numbers as current** (M2). |
| 13 | **All** | Add `pytest-timeout` (M4). Master plan §13 = the companion prompt pack, intentionally not a section (M8). Run-compare side-by-side (M6) and temporal scene comparison (#24) are **deferred and struck from scope**. |

**Revised minimum credible cut:** `00, 00b, 01, 02, 03, 04, 05, 06, 07, 08, 09, 10, 12, 12b, 13, 14, 16, 20`.

---

## PART D — STANDING RULES ADDENDUM (append to the standing-rules block)

```
7. FROZEN — do not modify, tidy, or "improve": contracts/schemas/ (esp. VESSEL_COLUMNS, 14 cols),
   pyproject.toml packages = [], pytest.ini importlib config, the five pipeline stage names,
   artefact filenames, layer route names, engine A/B/C CLI interfaces.
8. UI reference images govern layout/palette/density ONLY. They govern no number, label, model
   name, provider status, or role string. If a value is not in an API response, it is not displayed.
9. Never tune gates, weights, or thresholds to produce a more impressive result. An honest null
   result is a valid deliverable and must be reported as-is.
10. Before creating any file, search for an existing equivalent and extend it. Report what you
    found and why it was insufficient if you create anything new.
```

---

## PART E — ANSWERS TO THE TWO OPEN OFFERS

- **Publish as a shareable page:** yes — publish the recon report **plus this decision memo** as one page, with the UI-violation table as its centrepiece. That table is the artefact the whole team needs in front of them while building.
- **Draft the CLAUDE CODE MASTER EXECUTION INSTRUCTIONS block:** yes — draft it as the standing-rules block (original 6 + addendum 7–10 above) followed by the amended P00→P20 index with the D1–D4 decisions inlined. Show it to me before Phase 0 begins.

**Then start Phase 0: P00 → P00b → P01–P05.**
