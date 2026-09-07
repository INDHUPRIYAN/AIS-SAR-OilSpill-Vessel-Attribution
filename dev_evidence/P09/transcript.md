# P09 - incidents: the case file a run is evidence for

Executed: 2026-09-07T02:51:46Z

## Why an incident is not a run

A spill event can span several scenes and several runs, so the run cannot be
the unit of accountability -- it is sealed, hashed, immutable evidence, and
evidence does not have a status. The incident is the mutable case around it.

## Lifecycle is the substance, not the CRUD
```
open -> investigating -> attributed -> closed -> archived

investigator/analyst : may open a case and move it to 'investigating'
reviewer/admin       : may set 'attributed' or 'closed'
```
`attributed` and `closed` are statements about who polluted, so concluding a
case is a reviewer decision (master plan section 8). The check is
**value-dependent** -- it inspects the status being written -- so it cannot
live in a route-level guard, which is why it has dedicated tests.

That gap is declared rather than left implicit: `test_rbac_matrix` now carries
a `VALUE_GUARDED` set, so `PATCH /api/incidents/{id}` having no route guard is
a recorded decision instead of something that looks forgotten.

## Schema (additive)
```
incidents          id (INC-<year>-<seq>) - readable, sortable, quotable in a report
                   title, geometry_json, detected_utc, status, region,
                   assignee_id, notes, created_by, created_utc, updated_utc
investigations   + incident_id (nullable), created_by
runs             + incident_id (nullable, STAMPED at creation)
```
`runs.incident_id` is stamped from the investigation when the run is created,
not joined at read time: re-filing an investigation under a different case
later must not rewrite what a sealed run was evidence for. A test asserts
exactly that.

## Promote-from-run

`POST /api/incidents/from_run/{run_id}` seeds the case geometry from the run's
own slick centroid rather than asking anyone to retype a coordinate, so the
case is anchored to what the pipeline actually detected. When the artefact is
missing or unreadable the incident is still created **without** geometry: an
incident with no shape is honest, one with an invented shape is not. Filing the
same run twice returns 409.

## Correctness checked against brute force

Filter and pagination tests compare against a full unfiltered listing, not
against a second query of the same shape -- a wrong WHERE clause cannot agree
with itself. Pagination is asserted to be a stable partition: every record
appears exactly once across pages, no gaps and no repeats.

## Two defects found

**1. My own test measured the wrong thing.** `test_a_no_op_patch_writes_no_audit_row`
counted total audit rows, but switching accounts to read the log writes
`auth.login` rows of its own -- it was measuring the test, not the patch. Now
scoped to edit events for that incident.

**2. The migration report understated itself.** It printed `tables added: none`
while the counts beside it showed `incidents 0 (new table)`, because the report
only listed tables the file had anticipated. Now it reports every table that
actually appeared. A small dishonesty in a migration report erodes trust in the
whole report.

## A guard against a repeat of P08's leak

`conftest.seed_admin` now refuses outright to write into
`data/oceantrace.db`, with a message naming the fix. In P08 a test account with
a known password reached the live database because two suites legitimately use
the real DATA_ROOT; chasing which module did it fixes one case, refusing fixes
the class. Verified: the live `users` table is empty after a full suite run.

## Regression

```
780 passed, 4 skipped   (757 before P09; +23 incident tests)
frontend: npm run build clean
live DB: 138 investigations / 92 runs / users 0, incidents table added,
         no pre-existing table lost rows
```
