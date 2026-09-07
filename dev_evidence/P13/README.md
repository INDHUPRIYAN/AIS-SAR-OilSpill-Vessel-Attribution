# P13 — Investigation v2: AOI registry, jobs, cancellation, rerun, SSE

Four capabilities that between them turn "run the pipeline" from a CLI
operation into something an operator can drive, watch and stop.

## What existed, and what was extended rather than replaced

Standing rule 10 says search for an equivalent before creating anything. What
was found:

| needed | found | what happened |
|---|---|---|
| `api/aois.py` CRUD | `api/scheduler_routes.py` already served `GET /api/aois` | **extended it.** A second AOI module would have split one resource across two files. |
| `aois` table | `AoiWatch` held poll *state*, keyed by `aoi_id` | **added `Aoi` for definitions**, left `AoiWatch` alone. The split is why deleting an AOI can keep its high-water mark. |
| AOI validation | `scheduler/aoi.py` validated YAML bboxes | **added `scheduler/geometry.py`** for drawn polygons. The YAML check is right for a file a developer reviews; it does not catch a dragged corner or a pasted `[lat, lon]`. |
| job progress | `status.json` written after every stage | **read from it**, rather than keeping a second copy of pipeline state. |

Genuinely new: `services/jobs.py`, `api/events.py`,
`scheduler/registry.py`, `scheduler/geometry.py`,
`frontend/src/lib/useRunEvents.js`, `frontend/src/components/NewInvestigation.jsx`.

## The AOI registry is a table now

`config/aois.yaml` is migrated in once, on first use, and each row records
`source: "yaml" | "api"`. The reason is narrow: an operator drawing a box on a
map cannot edit a file on the server's disk, and two operators editing one file
cannot merge. The YAML is still a perfectly good way to define AOIs for a fixed
deployment and nothing about it was deleted.

* the migration **never overwrites an existing row** — editing an AOI through
  the API and restarting must not silently revert it to what the YAML said;
* YAML-migrated rows have `geometry: null`. The file only ever had a bbox, and
  writing a rectangle would claim someone drew one;
* deleting an AOI **keeps its `AoiWatch` row**. That is the high-water mark of
  scenes already handled; discarding it would turn delete-then-recreate into a
  burst of duplicate investigations.

### What an AOI is refused for

`POST /api/aois` returns **422 with the reason**, not a generic "invalid":

| drawn | refused because |
|---|---|
| a box over Kansas | all 144 sampled points are land — an oil slick cannot appear there |
| `[[27.3, -92.9], …]` | latitude −92.9 is outside [−90, 90] — a swapped `[lat, lon]` pair lands here |
| a 3-position ring | the exterior ring is not closed |
| 40° × 30° | larger than the 20° maximum — every pass over the basin would open an investigation |
| 0.001° × 0.001° | smaller than the 0.01° minimum — no scene footprint would cover it, so the watch would poll forever and see nothing |
| a `Point` | the scene search takes one footprint |

The at-sea test uses `global-land-mask`, the same package the calibration land
mask uses, so the two agree about where the coast is. It samples a 12×12 grid:
a sanity check for "you have outlined Kansas", not a coastline-accurate GIS
operation, and `sea_check` says `tested: false` when the package is absent
rather than refusing an AOI because an optional dependency is missing.

## Cancellation is cooperative, and a cancelled run is not sealed

`run_pipeline` gained a cancel check consulted inside `flush_status` — after
the status write, so a cancelled run still publishes the stage it completed.
Between stages only: killing a worker mid-write would leave a half-finished
GeoTIFF that a later run could mistake for a real artefact.

The important property is what a cancelled run *is not*:

```
manifest.json      absent  -- sealing is what makes a run quotable as evidence
status.json        run_status: "cancelled", with a note saying it is not sealed
runs.status        "cancelled", distinct from "failed"
```

"No manifest" is an absence, not a statement, so `jobs.mark_cancelled` rewrites
`status.json` to say so explicitly. Completed stages keep their real result;
only `pending`/`running` rows become `cancelled`.

A cancel check that throws **fails open** — the run continues. The worst case
of failing open is a run that finishes; the worst case of failing closed is a
healthy run stopped by a broken flag.

## Rerun always gets a new id

`POST /api/runs/{id}/rerun` launches a new run with the inputs the original was
*given* — from the job record, not the manifest. The two differ whenever the
pipeline resolved or substituted a path, and reading the manifest would make
"rerun" quietly mean "run whatever the fallback picked last time". When no job
record exists (runs that predate this) the manifest is used and `inputs.source`
says so.

Re-running into the same id is never offered: it would overwrite the artefacts
an investigation was concluded from, which `provenance.assert_writable` refuses
anyway (§12).

## SSE, with polling kept as a real fallback

`GET /api/events/runs/{run_id}` tails the same `status.json` the pipeline
writes. Nothing is held in memory, so a browser connecting halfway through gets
the current status immediately rather than only future transitions. The stream
ends by itself on a terminal state.

`useRunEvents` prefers SSE and **falls back to polling**, reporting which via
`transport`. The failure being guarded against is not "SSE broke" — that happens
behind proxies that buffer `text/event-stream`. It is a UI that stops updating
without saying so, which looks exactly like a hung pipeline.

The endpoint also emits an explicit `waiting` event when no `status.json`
exists yet, because silence and "the run has not started" are indistinguishable
to a client.

## The wizard

`NewInvestigation` replaces a button that created every investigation against
`contracts/mocks/scene_meta.json` and called it "New investigation" — so every
case in the list pointed at the same fabricated scene.

Four steps: **Area** (registered AOI or pasted GeoJSON, refusal message shown
verbatim) → **Window** (reversed windows refused) → **Scene** (search results,
with anything not in the local cache marked `not downloaded` and
**unselectable**) → **Review**.

A catalogue hit is not a downloaded scene. `POST /api/investigations` with an
uncached `scene_product_id` returns 404 saying exactly that, rather than
creating an investigation that fails at run time.

## Tests

23 backend (`main_system/tests/test_investigation_v2.py`) + 5 frontend
(`tests-unit/runEvents.test.jsx`).

Beyond the four P13 named cases (land AOI rejected, bad window rejected, cancel
seals nothing, SSE delivers 5 stage events, rerun gets a new id), these pin the
judgement calls: a broken cancel check does not stop a healthy run; cancelling
an already-finished job is reported as 409 rather than pretended; YAML-migrated
AOIs carry no invented geometry; deleting an AOI keeps its watch state.

Evidence: [`pytest_full.txt`](pytest_full.txt), [`vitest.txt`](vitest.txt).

## One thing changed outside P13's list

`GET /api/investigations/{id}` returned `bbox` only from a completed run's
`scene_meta.json`. An investigation created from an AOI has a footprint from
the moment it exists, and returning `null` left the map unable to show where
the case was. It now falls back to the stored AOI bbox and reports
`bbox_source: "scene" | "aoi" | null` so the two are never confused.
