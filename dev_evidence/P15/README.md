# P15 — Alerts and tasking

The watcher already opened investigations by itself. What it could not do was
tell anyone.

An investigation appearing in a list is not a notification, because nobody
watches a list. An alert is the row that says *this happened, it is yours, and
here is how long it has been waiting*.

## The queue

`alerts` table, `api/alerts.py`, and one call added to the watcher's sweep. The
alert is raised **beside** the investigation, not instead of it, and a failure
to raise one is caught and logged rather than losing the investigation that was
just opened — the alert is a notification about work, not the work itself.

```
GET  /api/alerts                  the feed, newest first, severity-ordered
GET  /api/alerts/summary          counts for the top-bar bell
POST /api/alerts/{id}/ack         acknowledge (audited)
POST /api/alerts/{id}/assign      assign to a real user (audited)
POST /api/alerts/{id}/dismiss     dismiss WITH a reason (audited)
GET  /api/events/alerts           SSE feed
```

## Four design choices, each defending against a specific failure

**Dismissal requires a reason.** `POST /dismiss` with no reason — or under
three characters — is a **422, not a default**. A queue that clears with one
unexplained click becomes a queue people clear rather than read, and the record
of why nobody acted disappears with it. The reason is stored on the row and in
the audit log.

**Assignment requires a real user.** Assigning to a nonexistent id is a 400.
Assigning to nobody looks handled and is not, which is worse than untouched.

**Severity is derived, never typed in.** A new scene is `info` because nothing
has been detected in it yet — the system has not looked. A free-text severity
field drifts into a mood ring.

**Age is computed on read, never stored.** A stored age column is wrong the
moment it is written and needs a job to keep it wrong more slowly. A test
asserts the `Alert` model has no `age_seconds` attribute.

## Deduplication

Alerts are deduplicated on `(kind, scene_id)` **while an earlier one is still
open**. A watcher polling hourly must not raise the same scene sixty times: a
queue full of duplicates is a queue nobody reads.

The scoping to *open* alerts matters. Once an alert is dismissed, the same
scene can raise a new one — because a fresh pass after a case was closed is
genuinely new information, not a duplicate.

## A test that could have hung the suite

The first version of the SSE test consumed `/api/events/alerts` over
`TestClient.stream()`. It hung — and produced zero output for several minutes,
which is worse than failing, because a hang gives no diagnosis.

The cause is structural, not a bug in the endpoint: **an SSE endpoint is an
infinite loop by design.** Consuming one over HTTP in a test means relying on
client-side cancellation to end a stream built never to end. The test now
drives the async generator directly with a request stub that reports
disconnected, asserts the initial queue frames, and stops. A test that can wedge
the suite is worse than no test.

The same run surfaced two tests using `AuditLog.event_type`; the column is
`action`. Both fixed.

## Frontend

`/alerts` renders the queue with severity, computed age, status and actions.
Dismissal opens an **inline reason field** and the confirm button stays disabled
under three characters — the same rule the server enforces, stated in the UI as
*"An alert cleared without one erases the record of why nobody acted."* This is
deliberate friction, not client-side politeness; the server refuses either way.

`AlertBell` in the top bar reads `/api/alerts/summary`, so the badge count and
the queue can never disagree — the count is the server's, not a length computed
from a possibly-filtered client list.

## Tests

15, in `main_system/tests/test_alerts.py`, covering P15's five named cases
(poll → alert row, dismiss requires reason, assign requires user, ack audited,
SSE delivery) plus: dedupe while open, re-raise after dismissal, age is
computed not stored, dismissing twice is refused, a dismissed alert cannot be
acknowledged, critical sorts above info, and the summary counts only open
alerts.

Evidence: [`pytest_full.txt`](pytest_full.txt).
