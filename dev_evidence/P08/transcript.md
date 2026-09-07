# P08 - audit v2: authenticated actors, event types, hash chain

Executed: 2026-09-07T02:20:28Z

Live evidence below ran against an isolated database, so the demonstration
never wrote a demo account or demo rows into the production DB.

## The actor now comes from the session

Before, the only two emit sites took the actor from the request:
```
db.add(AuditLog(action=f"decision.{body.verdict}", actor=body.actor, ...))
```
An actor a client can choose is not evidence. `audit_service.record()` resolves
the account from the session and ignores any caller-supplied name; the claimed
value is kept inside `detail` as the analyst's own label, clearly marked.

## Live log
```
ACTION                ACTOR                     IP          RESOURCE              HASH
auth.login            p08@example.invalid       127.0.0.1   p08@example.invalid   190c298dad98
investigation.create  p08@example.invalid       127.0.0.1   inv-451f28a174        d89b099e8dae
auth.login            p08@example.invalid       127.0.0.1   p08@example.invalid   ae5c718d9c0c
```

## Chain verifies, and detects tampering
```
GET /api/audit/verify
{"ok": true, "checked": 3, "total": 3, "unchained": 0,
 "broken_at": null, "note": "every row is chained"}

# then, editing row 1 directly in SQLite, behind the application:
UPDATE audit_log SET detail = "QUIETLY ALTERED" WHERE id = 1;

GET /api/audit/verify
{"ok": false, "checked": 3, "broken_at": 1,
 "reason": "row_hash does not match the row's contents
            -- this row was edited after it was written"}
```

The claim is tamper-**evidence**, not tamper-proofing: anyone with write access
to the file can rewrite rows. What they cannot do is change one and leave the
rest consistent. That is the honest claim and the one worth making.

## A bug the tests caught: the chain broke on every row

SQLite has no native timestamp type, so `DateTime(timezone=True)` writes a
string and reads back a **naive** datetime. `isoformat()` therefore produced
`...+00:00` when hashing at write time and `...` without the offset when
re-hashing at verify time, so every chain failed on its first row.

Hashing a value whose representation changes on round-trip is the classic
version of this bug, and it would have shipped as "the audit chain is broken"
rather than as "our serialisation is unstable". `_timestamp()` normalises to
UTC with an explicit format so both sides agree.

## Routes
```
GET /api/audit          filter by actor/action/resource/since/until, paginated
GET /api/audit/verify   recompute the chain, report the first break
GET /api/audit/export   CSV including each row's hash; itself an audited event

readable by  : reviewer, auditor, admin
denied to    : investigator, analyst  (403)
delete route : none exists - append-only is the whole claim
```
The log used to live under `/api/keys/audit`, which was both the wrong place
(it is no longer about credentials) and the wrong permission - an auditor had
to hold admin rights over the credential vault to read history.

## Event types instrumented
```
auth.login       (success AND failure - a burst of failures is the signal)
auth.logout
investigation.create
run.start
decision.*
key.set
data.export      (leaving with a copy of the evidence is an event)
```
`incident.*`, `report.*`, `user.change`, `model.change`, `run.complete/cancel`
are declared in `EVENT_TYPES` and wired when their features land (P09/P16/P17).

## A test-hygiene defect found and fixed

`test_metrics_truth` and `test_scene_catalog` deliberately use the REAL
`DATA_ROOT` to read real artefacts. Once P07 required a session, the shared
sign-in helper seeded `test-admin@example.invalid` into the **live** database.
A test account with a known password in the production DB is exactly the kind
of thing that ships unnoticed. Both fixtures now redirect `DATABASE_URL` to a
temp file while keeping the real data root, and the stray account was removed
(`users` back to 0).

## Two ordering defects in the test suite (found, not worked around)

P07's blanket auth made the suite order-sensitive in a way that was invisible
before, because nothing used to need a database identity to call the API.

**1. A test account reached the live database.** `test_metrics_truth` and
`test_scene_catalog` use the real `DATA_ROOT` on purpose -- they read real
artefacts. Once signing in was required, the shared helper seeded
`test-admin@example.invalid` into the production DB. Both now redirect
`DATABASE_URL` to a temp file while keeping the real data root.

**2. `test_investigation_page` was bound to a sibling's database.** It imports
`app` at module scope; `test_decisions_api` later purges `backend.*` and
repoints `DATA_ROOT`. Seeding therefore went to one engine and the app queried
another, and login failed for reasons unrelated to the file. It now pins its own
env before importing backend and seeds through its own `SessionLocal` -- same
env, same import, same database.

Neither was fixed by relaxing a guard. A test-only bypass would have meant the
suite exercising a configuration nobody deploys, which is the same class of
defect as PROMPT-03.

## The live audit log keeps its test-run rows

Before the isolation fix, test runs wrote 11 rows into the live `audit_log`.
They are left in place. They are truthful records that a process really did
sign in and act against that database, and deleting rows to make the log look
tidier is precisely the behaviour the hash chain exists to detect. The chain
over them verifies clean.

One pre-existing legacy row (id 1, written before chaining) has no hash;
`verify` reports it as `unchained` rather than counting it as verified.
