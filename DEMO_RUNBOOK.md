# OceanTrace — SIH 2026 demo runbook (PS 26143)

Master plan §15, made executable: what to start, what to click, what to say,
what to do when something is down, and how to roll back. Every screen named
here was photographed against the live registry during acceptance — see
`acceptance_evidence/` and `ACCEPTANCE_REPORT.md`.

**The canonical flagship is `inv-gulf-flagship-20230108-2day`, digest
`fd42e078f8366110`.** Not `-final`, not `-v2`. Those are sibling runs of the
same scene; only `-2day` is the frozen acceptance evidence.

**One-click walkthrough (added 2026-09-21).** The Dashboard's **Demo case**
panel opens this run in the workspace and plays it from orbit to the report,
labelled **DEMO CASE** throughout. Every figure is read from the run's own
artefacts. Point a deployment at another showcase run with `VITE_DEMO_RUN_ID`;
a host without the run shows "not installed on this host" instead of a dead
link. Guarded by `main_system/frontend/tests-e2e/demo.spec.js`.

---

## 0. Before the day (once)

1. **Model weights present.** `scripts\get_weights.ps1` (also run by
   `run_demo.ps1`). Without them detection silently uses the threshold
   fallback and the run will say `FALLBACK` — do not demo that.
2. **Operator account.** The live database ships with no users. Put these in
   the gitignored `.env` (already present on the demo laptop):
   ```
   OT_ADMIN_EMAIL=operator@oceantrace.local
   OT_ADMIN_PASSWORD=<choose>
   ```
   The backend bootstraps that account on first start and never rewrites an
   existing one. Analyst/reviewer accounts for the report ceremony were seeded
   for acceptance (`acceptance_evidence/seed_review_roles.txt`).
3. **Registry reconciled.** Every sealed run on disk has a registry row:
   ```
   .venv\Scripts\python.exe -m backend.backfill_runs --dry-run   # must say "would add 0"
   ```
   (run from the repo root with `PYTHONPATH=main_system`). If it would add
   anything, run it without `--dry-run`. This is what put the flagship into
   the index — it was produced by the CLI and the API never saw it.
4. **Providers.** Monitoring → *Test all*, or `POST /api/apis/test-all`.
   REACHABLE is not WORKING; the demo needs nothing WORKING except for the
   optional live scene search (step 3 below). Last acceptance capture:
   `acceptance_evidence/providers_test_all.json`.
5. **Rehearse twice**, once with the network off (§4).

## 1. Start

```
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```
Backend on `:8000`, frontend on `:5173`. Open http://localhost:5173.

If `:8000` is already taken (a stale server from an earlier session was found
on this machine during acceptance), start explicitly on another port:
```
set PYTHONPATH=main_system
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
cd main_system\frontend && set API_TARGET=http://127.0.0.1:8010 && npx vite --port 5173
```

Health: `GET /health` → `{"status":"ok"}`. Top bar shows the ZULU clock and
`n/12 APIs`.

## 2. The 12-minute narrative — exact clicks

### 2.1 Sign in (RBAC visible) — 30 s
- `operator@oceantrace.local`. Say: every `/api/*` route is 401 anonymous
  (E2E-01); roles are admin / investigator / analyst / reviewer / auditor.
- Top bar: `CONTEXT NO RUN` ghost chip. Nothing is claimed until a run is open.

### 2.2 The flagship — 4 min
- Press **⌘K / Ctrl+K**, type `flagship`, choose
  `inv-gulf-flagship-20230108-2day` (or paste the deep link
  `/investigation?run=inv-gulf-flagship-20230108-2day`).
- **Read the top bar aloud** — it is the honesty strip:
  `inv-gulf-flagship-20230108-2day · AIS REAL · STAGES 5/5 REAL · INDEX RECONCILED · ENGINE ML · ⌗ fd42e078`.
  - *AIS REAL*: MarineCadastre archive, 2023-01-07 and -08, 86,830 rows.
  - *5/5 REAL*: Sentinel-1A via CDSE, CMEMS currents, ERA5 wind, real AIS,
    every stage the real engine.
  - *INDEX RECONCILED*: hover it. The run was produced by the CLI; its
    registry row was rebuilt from the sealed manifest. The artefacts and the
    digest are untouched — the row is an index entry, not a witness statement.
  - *⌗ fd42e078*: click copies the full digest.
- Header shows the run id and an **UNFILED RUN** badge — the run belongs to no
  investigation and the UI says so instead of borrowing one.
- Layers panel: SAR scene (real tiles at native resolution — zoom in),
  detected slick, geometry, forecast, hindcast cloud, origin zone, AIS tracks
  (badge **REAL**), look-alikes (hatched, *not oil*).
- **Spill tab**: area, axes, damping, **age estimate — Confidence LOW**, engine
  ML, source REAL. Say: 31 oil regions are model output, not confirmed spills;
  the Gulf is full of natural seeps; there is no ground truth for this scene.
- **Suspects tab**: `Rank #1 · Score 0.67`, MMSI 367653160, `vessel_name`
  null — MarineCadastre carried no static identity, and nothing was invented.
  Expand: six weighted factors, weights printed (×0.30 …), Σwᵢsᵢ reproduces
  the total (E2E-08). Say *ranking*, never *culprit*.
- Origin: 13-hour window `2023-01-07T11:10Z → 01-08T00:10Z`, method
  `cloud_convergence`, uncertainty **0.366 km**, 25 non-zero ellipses, method
  string says *not ML*. Present it as a window, never a discharge time.
- Measure tool (ruler icon, or `M`): click two points — great-circle km and
  nm, method printed under the number.

### 2.3 Honesty tour — 2 min
- **Data & Models**: Sentinel-2 / EO → `NOT DEPLOYED` (a `?source=S2` search
  returns 501, never an empty list). Drift ML residual → `EXPERIMENTAL`,
  evaluated negative (0 of 6 trajectories improved), disabled. Deployed
  models named with sha256: `unet-r34-fullcorpus-e48`,
  `yolo11n-screen-dartis-2026-08-24`. Never say "YOLOv8".
- **Analytics**: all three segmentation numbers, labelled —
  `oil-tile IoU 0.5723 · overall IoU 0.4445 · no-oil tiles firing 280/5,248 (5.3%)`.
- **Synthetic run**: ⌘K → `rehearsal` → `inv-gulf-rehearsal-20230108`. Top bar
  goes gold: `STAGES 2/5 REAL · SYNTHETIC 3 stages`; the AIS *layer* badge and
  every suspect row read **SYNTHETIC** (the mocked attribution stage drew
  generated vessels). Note the AIS *chip* still reads `REAL`: it records which
  archive the run selected (MarineCadastre, covering the window), not what the
  mocked stage produced. Say both — the chip is the selection, the badge is the
  data on screen. For a run where every AIS surface says SYNTHETIC, open
  `inv-final-audit` (Bali) instead: 43 generated vessels, chip `AIS UNRECORDED`
  because its manifest predates the `ais` block.
- `/verify` live: Monitoring or `GET /api/runs/inv-gulf-flagship-20230108-2day/verify`
  → `ok: true, checked: 8`.

### 2.4 Live pipeline with the stage cascade — 4 min
- Workspace → **New investigation** → name it → scene
  `Chennai / Ennore, India` (real Sentinel-1A, 2017-01-29) → create.
- Optional, only if CDSE is WORKING: *Search scenes* over the AOI + window
  first; pick the product it returns. If it is not WORKING, skip — say so —
  and use the catalogued scene (it is the same product).
- **Replay mode OFF** → *Run investigation*. Pipeline panel steps through
  detect → characterise → hindcast → forecast → attribution over SSE. Full
  resolution takes ~4 minutes on the demo laptop (measured; see E2E-15) —
  narrate the honesty tour over it, or start it before 2.3.
- When it seals: top bar `STAGES 5/5 REAL`, `INDEX` chip absent (the API
  watched this one). Manifest carries the git SHA and both model hashes.
- Cancel is real: *Cancel* stops at the next stage boundary, the job reads
  `cancelled`, no manifest is written, `/verify` says "never completed".

### 2.5 Report and review — 1.5 min
- Suspects/Spill → **Report**. Draft composes server-side from the sealed
  artefacts; provenance annex lists the 8 artefact hashes and the digest.
- Submit as analyst → sign in as `reviewer@oceantrace.local` → **Publish**.
  Published is immutable (a second publish is 409). *Revise* makes version 2.
- Export JSON/CSV — both stamped with digest and version.

## 3. Offline fallbacks (rehearse these)

| if this is down | do this | what the UI shows |
|---|---|---|
| Internet entirely | Everything in §2 except CDSE search works from disk: flagship, synthetic run, replay of any sealed run, tiles from the local COG, metrics, report | Basemap tiles fail → SAR raster becomes the basemap (by design). Providers show REACHABLE/WORKING honestly |
| CDSE | Skip scene search; use the catalogued Chennai scene | Monitoring row says so |
| ERA5 / CMEMS | Live run's drift uses cached forcing if present, else the stage reports FALLBACK — say it | `FALLBACK` chip on that stage |
| Live run too slow / fails | **Replay mode ON** → *Replay investigation* on the same scene: sealed artefacts render in < 5 s | `REPLAY` — a replayed run says it is one |
| Port 8000 busy | §1 alternative ports | — |

Never present a mock run as real: `mock-smoke` exists for pipeline wiring only
and badges `SYNTHETIC` everywhere.

## 4. Rollback

Release candidate tag: `v1.0.0-rc1` (`ACCEPTANCE_REPORT.md` §1). To roll the code back:
```
git checkout v1.0.0-rc1
cd main_system\frontend && npm ci && npx vite build
set PYTHONPATH=main_system && .venv\Scripts\python.exe -m backend.backfill_runs --dry-run
```
Sealed runs under `data\runs` are immutable and are not part of any rollback;
the registry is rebuilt from them with `backfill_runs` if the database file is
ever replaced. A pre-acceptance copy of the database is not committed; take
one with `copy data\oceantrace.db data\oceantrace.db.bak` before any demo-day
experiment.

## 5. What not to say

- Not "culprit", "guilty", "polluter". Rank and score.
- Not "the spill" for a model region without ground truth.
- Not "discharge time" for a 13-hour window.
- Not "YOLOv8". Not "the ML hindcast" — hindcast is physics.
- Not a number that is not on the screen.
