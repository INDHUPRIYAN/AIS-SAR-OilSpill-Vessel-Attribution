# P10 - runs history hardening and the corrected funnel

Executed: 2026-09-07T03:02:19Z

## RH-01..05: the listing

Before, `/api/runs` returned a bare list truncated at 50 with no total, no
filters and no sort. A caller could not tell a short page from the end of the
data.

```
GET /api/runs  ->  {total, offset, limit, items[]}
  filters : q, status, incident, region, from, to, archived
  sort    : started_utc | finished_utc | seconds | status | top_score | slick_area_km2
  order   : asc | desc
```

Indexes added on `status`, `started_utc`, `incident_id`, `archived`, `region`.

`archived` hides a run from the default listing and is never a delete. A run is
sealed, hashed evidence; hiding it is a listing preference, not a lifecycle.
The archive route is role-gated and audited.

## Denormalised outcome

`top_suspect_mmsi`, `top_score`, `slick_area_km2` are written onto the run row
at seal time, so a 99-row history page shows what each run found without
opening 99 artefact bundles. Read from the sealed files, never recomputed, and
**silent on failure** -- a summary that cannot be read stays null rather than
being guessed, because a wrong top suspect in a listing is worse than a blank.

## Reconciliation (RH-05)

`backend/backfill_runs.py` already existed and was well-shaped, so it was
extended rather than replaced: a new `_denormalise()` pass fills the summary
for rows that predate the columns, without touching anything the manifest would
rewrite (refreshing an API-created row would move its start time to its finish
time, which the script's own docstring warns about).

```
registered 7, refreshed 0, summarised 92 (0 already current)

DB rows           : 99
run dirs on disk  : 144
  with manifest   :  99
  replayable      :  99
manifest dirs with no DB row : 0
DB rows with no manifest     : 0
```

Converged. The audit's 89 DB / 93 replay / 96 disk divergence is closed: every
sealed run has a row and every row has its artefacts. The 45 remaining
directories carry no manifest -- they are partial or abandoned runs, correctly
absent from a listing of completed work.

## H5: the funnel

The funnel the UI needs could not be built, because normalisation kept only the
humanised sentence ("course ran 54 deg off the slick's axis") and dropped the
engine's own `filter_reason` and `failed_gates`. Counting gates by
pattern-matching English prose is not a contract, and the wording is exactly
the kind of thing someone improves.

Both are now carried through, and `GET /api/runs/{id}/funnel` returns six
counts rather than the four first sketched -- the AIS index and the three gates
are different things, and collapsing them hides where the population fell away:

```
found -> indexed -> after_spatial -> after_temporal -> after_trajectory -> candidates
```

### One honesty note the UI must not lose

The gates are evaluated **together**, not in sequence: a vessel can fail
several at once and `failed_gates` lists all of them. The cumulative counts
therefore answer *"how many would remain if the gates were applied in this
order"* -- a presentation of one evaluation, not a record of three passes. The
response says so in `gates_are_sequential: false` and carries
`exclusive_by_gate`, each gate's own toll independent of ordering, so a vessel
failing two gates is not silently attributed to whichever came first.

`found` comes from the AIS index note in the manifest. When a run did not
record it the response returns `null` with `found_note` explaining why, rather
than inventing a top-of-funnel that would overstate the filtering performed.

## Regression

```
797 passed, 4 skipped   (780 before P10; +17)
frontend: npm run build clean, 6 vitest passing
```

`/api/runs` changed shape, so `api.listRuns()` now unwraps `items` for the two
existing callers and `listRunsPaged()` exposes the full envelope.
