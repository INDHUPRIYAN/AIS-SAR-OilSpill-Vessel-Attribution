# P19 — Command-center shell: palette, MAP tools, ZULU clock, provenance strip

The workspace could show a run. What it could not do was let anyone *reach* one
without navigating by hand, say at a glance what produced the thing on screen,
or measure a distance on it. This closes those three, plus the keyboard and
reduced-motion rules from the UX spec.

Two halves of the prompt were already committed as `6c8bbe3` — the search
endpoint and the geodesy utility, both untested at that point. This finishes
the prompt and puts tests under all of it.

**Python: 979 passed, 4 skipped, 0 failed** (`pytest_full.txt`).
**Frontend: 70 passed** (59 new — `vitest_full.txt`).
Baseline was 957 passed / 5 skipped; +21 are the new search tests, and one
provider test that was skipping on a network outage ran and passed this time.

## Deviation: 3D globe mode is dropped, not deferred-in-place

PROMPT 19 opens with a `3D · 2D · MAP · SAT` mode switcher over a deck.gl
`_GlobeView`. Architect decision **D3** overrides it:

> optional polish, last in, first out… If the schedule tightens, drop 3D
> entirely; the audit and the master plan both name it first to cut, and
> nothing depends on it.

The schedule has tightened — P20 is the deliverable that matters — so the globe
is dropped whole rather than half-built. There is no mode switcher and no globe
view. Recorded in `docs/LIMITATIONS.md` under *Explicitly out of scope*.

One consequence worth naming: the prompt's acceptance test was *"mode switch
preserves camera / `t` / selection"*. With no mode switch, that test has nothing
to exercise, so it is **not** claimed as passing. What replaces it is the thing
that mattered underneath — **run context survives navigation** — which is tested
(`commandPalette.test.jsx › run in context`) and visible in the screenshots.

## What was built

| Piece | File | Tests |
|---|---|---|
| Shell state: routes, keymap, run context, command registry | `frontend/src/lib/shell.jsx` | via the palette suite |
| ⌘K command palette + `?` shortcut overlay | `frontend/src/components/CommandPalette.jsx` | `commandPalette.test.jsx` — 19 |
| ZULU clock + provenance chip strip | `frontend/src/components/TopBarStatus.jsx` | `topbar.test.jsx` — 15 |
| MAP-mode measure readout | `frontend/src/components/workspace/MeasureTool.jsx` | `measureTool.test.jsx` — 10 |
| Per-leg geodesy (`segments`) | `frontend/src/lib/geodesy.js` | `geodesy.test.js` — 15 |
| Measure layers + crosshair on the map | `frontend/src/components/workspace/WorkspaceMap.jsx` | covered above |
| Search endpoint (committed in `6c8bbe3`) | `backend/api/search.py` | `main_system/tests/test_search.py` — 21 |

## Four things the tests exist to stop

**1. A palette that guesses.** An MMSI is nine digits and a run id is a slug.
Matching is substring and case-insensitive in both halves — the server's rows
and the local commands use the same rule — and a near-miss returns nothing:

```
test_a_near_miss_on_an_mmsi_is_not_a_result   (367653162 against 367653160/1)
palette › filters commands by substring, not by fuzzy guessing  ("vessle")
```

The endpoint states its own matching rule in every response, the palette renders
that sentence on an empty result (`shots/04-palette-no-fuzzy.png`), and a test
asserts the sentence beside the behaviour it describes — so the UI cannot
advertise a matching behaviour the server does not implement.

**2. A failed search rendering as "no results".** Same class of defect as an
empty EO list standing in for `NOT DEPLOYED`. A failed request prints
*"Search failed — … This is not an empty result: the query did not complete."*

**3. A provenance strip that flatters the run.** The chips describe the **run in
context**, read from `GET /api/runs/{id}` and its sealed manifest — never global
provider health, which can be green over a run whose AIS was synthetic. Every
silence is rendered as a silence:

| situation | chip |
|---|---|
| manifest records `ais.data_source: real` | `AIS REAL` (teal) |
| manifest records `synthetic` | `AIS SYNTHETIC` (gold, filled — loudest chip in the system) |
| manifest has no `ais` block | `AIS UNRECORDED` (ghost) — never defaults to real |
| no manifest at all | `STAGES NO MANIFEST` |
| run cannot be read | `RUN UNREADABLE`, and **no other chip is shown** |
| no run open | one ghost `CONTEXT NO RUN` |

Beside the `n/m REAL` count sits one chip per source that was **not** real, so a
mocked stage cannot hide inside "4/5". Chips are grouped by source rather than
one per stage — see *Defects found while capturing evidence*, below.

**4. A ruler that measures the projection.** Web-mercator stretches east–west by
1/cos(latitude): a screen-distance ruler reads ~13% long in the Gulf and ~84%
long in the Baltic — plausible numbers, wrong scale, in the two basins this
project works in. Distances are great-circle, pinned against constants from
outside this codebase (a degree of latitude = 111.195 km, = 60.04 nm by the
nautical mile's definition, half the circumference = 20015.11 km), and one test
asserts a degree of longitude *shrinks* with latitude by exactly the cosine
ratio — which a projected ruler could not do.

The map's per-leg labels and the panel's total come from the same function
(`pathLengthKm` is defined as the sum of `segments`), so a reader who adds up
the labels lands on the total. The method line — `great-circle (haversine),
spherical earth R=6371.0088 km` — is rendered beside the number, so a screenshot
of a measurement carries its own geodesy.

## Keyboard and motion (UX spec §8.6, §10.7)

The `?` overlay is **generated from the live keymap**, which is also what the
global handler dispatches from — so a binding that is removed disappears from
the help, and one a view contributes appears in it. Asserted directly:
`the ? overlay › is generated from the live keymap`, and `lists nothing the
shell cannot dispatch`.

| binding | scope |
|---|---|
| `⌘K` / `Ctrl+K` | open the palette |
| `?` | shortcut overlay |
| `Esc` | close the palette or overlay — including from inside the palette's own input |
| `↑ ↓ ⏎` | move and open, palette-scoped |
| `M` | measure tool, contributed by the workspace while it is mounted |

A shortcut never fires while a field has focus (`Esc` excepted, or the palette
would be a trap). Tested: `bind their own key, and that key does not fire while
typing`.

Reduced motion is honoured on both sides: `<MotionConfig reducedMotion="user">`
covers every framer-motion animation at once, and a `prefers-reduced-motion`
block in `styles.css` collapses CSS durations to ~0 rather than removing
animations — anything whose end state is set by a transition still lands on that
end state. Focus rings are `:focus-visible`, 1px cyan, 2px offset, never
suppressed.

## Screenshots — real data, real archive

Captured against a **copy** of `data/oceantrace.db` (so the live database was
never written to) with `DATA_ROOT` pointed at the real run archive, at
1600 × 900. `shots/`:

| file | shows |
|---|---|
| `01-topbar-no-run.png` | the strip with nothing open: one `CONTEXT NO RUN` ghost chip |
| `02-palette-routes.png` | ⌘K with an empty query — 12 rows, one per declared route |
| `03-palette-search.png` | `inv-final-audit` found over `/api/search`, context line built from real columns (`complete · 5/5 real · S1A_IW_GRDH_BALI…`) |
| `04-palette-no-fuzzy.png` | `inv-final-audti` → no match, and the endpoint's own matching rule quoted |
| `05-shortcuts.png` | the `?` overlay, generated from the keymap |
| `06-chips-real-run.png` | `inv-final-audit` · `AIS UNRECORDED` · `STAGES 5/5 REAL` · `ENGINE ML` · `⌗ 24842276` |
| `07-chips-synthetic-stages.png` | `inv-001` · `STAGES 2/5 REAL` · gold `SYNTHETIC 3 STAGES` |
| `08-palette-layer-commands.png` | the workspace's own layer toggles inside the palette |
| `09-measure.png`, `10-measure-full.png` | three points measured: legs `15.96 km / 8.62 nm / 122°` and `12.66 km / 6.84 nm / 35°`, total `28.63 km · 15.46 nm · 85°`, method line printed |
| `11-flagship-no-db-row.png` | the flagship: `RUN UNREADABLE` — see below |

## Defects found while capturing evidence — all fixed

Each was invisible until the app was photographed with real data in it.

**1. The top bar overflowed and dropped the right-hand chrome.** With the chips
added, the ZULU clock, the ⌘K trigger, the API count and the theme toggle were
all pushed off the right edge at 1600px — and the last provenance chip was
clipped. A provenance chip you cannot see is precisely what spec §8.7 calls a
defect. The nav now gives up its labels first (icons only below 1660px, with a
`title` on every link so nothing becomes unidentifiable), and the chip row is
capped rather than allowed to push.

**2. Per-stage chips could not fit.** A run with three synthetic stages produced
three chips and overflowed again. Chips are now grouped by source — one chip
reading `SYNTHETIC 3 stages`, with the stage names in its tooltip — which is
also what the spec's four-slot chip row describes.

**3. `?run=` deep links opened a different run.** `runId` was
`replayRunId || status?.run_id || params.get("run") || …`, and `status.run_id`
is the *selected investigation's* latest run — so `/investigation?run=X` showed
whatever investigation #0 last did. **Every run row in the command palette is
such a link**, so the palette would have opened the wrong run every time. The
URL now beats `status`, while the two ids the page produces itself (a replay,
and a run it just started) still win, so starting a run from a deep-linked page
does not pin the old one.

**4. Opening the measure tool collapsed the Layers panel to nothing.** The left
column is a fixed-height flex column; the new panel squeezed the layer toggles
to zero height while leaving them mounted and "on" — visible state, invisible
control. The column now scrolls instead of compressing its panels.

## Found, not fixed: the flagship has no database row

`shots/11-flagship-no-db-row.png` is honest evidence of a gap that belongs to
P20, not to this prompt. Measured against the live archive:

```
GET /api/runs/inv-gulf-flagship-20230108-2day          404
GET /api/search?q=flagship                             0 results
GET /api/layers/inv-gulf-flagship-20230108-2day/slick  200
GET /api/runs/inv-gulf-flagship-20230108-2day/verify   200
GET /api/tiles/inv-gulf-flagship-20230108-2day/info    200
```

The run directory, its artefacts and its manifest are all present and verify;
what is missing is the `runs` row. `data/runs/` holds 162 run directories and
the database holds 99 rows, and **all five Gulf flagship-family runs are among
the missing** — so this is systematic, not a one-off.

The flagship is therefore reachable as *artefacts* but not as a *record*: it
cannot be found in ⌘K, does not appear in the runs list, and the provenance
strip over it reads `RUN UNREADABLE` — which is the correct, honest behaviour
for a run the API says does not exist, but not the behaviour anyone wants during
a demo.

Deliberately **not** fixed here: writing a row for the frozen flagship is a
decision about acceptance evidence, and P20 owns that. It is the first thing
P20 should resolve.

## Known outstanding

- **Route-level code-splitting** (carried from P18). `vite build` still warns
  that `index` (~1.76 MB) and `maplibre-gl` (802 kB) exceed 500 kB. Load-time
  only, no correctness dimension.
- The palette does not carry camera state — see the D3 note above.
- With a run deep-linked by `?run=` alone, the header still names the
  investigation selected in the dropdown rather than the run's own. Layers,
  chips and panels are all run-scoped and correct; only that one label can
  disagree.

## One reuse fix in the committed endpoint

`search.py` held a second copy of the scene-catalogue path and of the JSON
parsing around it (`data_root.parent / "main_system" / "config" / …`), which
`scenes.py` already computes. It now loads through the scenes module. The two
copies agreed, so nothing was broken — but a divergence would have surfaced as
scenes quietly missing from search results, which is an empty list, never an
error.
