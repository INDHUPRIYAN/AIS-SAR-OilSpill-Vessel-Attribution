# P06 - identity: users, argon2id, JWT sessions

Executed: 2026-09-07T01:30:17Z

Auth ships **alongside** the existing routes, not in front of them. Guarding is
P07: building the guard and applying it in one change makes a failure ambiguous.
Verified below that every pre-existing route still answers 200.

## H3 database guardrails (mandatory)

Two databases exist in this tree. Migrating the wrong one is not destructive,
but it 'succeeds' and leaves the backend serving an empty system.

### 1. Backup before touching anything
```
source : data/oceantrace.db  1,531,904 bytes
sha256 : 75dcb395da5c0d6ef41475b452e8ec30708714acb04e111d0d05dd87b1e60f74
backup : dev_evidence/P06/oceantrace.db.pre-m001.20260907T011949Z.bak (verified identical)
note   : *.db is gitignored, so backups stay local by design - hashes recorded here
```

### 2. The guardrail refuses the wrong database
```
$ DATABASE_URL=sqlite:///.../main_system/data/oceantrace.db \
    python -m backend.migrations.m001_production --check

REFUSED: ...main_system/data/oceantrace.db has tables but no runs or
investigations. That usually means DATABASE_URL points at the wrong file...
Re-run with --allow-empty if this really is a fresh system.
exit=2
```

### 3. The real migration, on the canonical database
```
database : C:\Users\Indhu Priyan\Documents\GitHub\AIS-SAR-OilSpill-Vessel-Attribution\data\oceantrace.db
exists   : True
row counts before:
  aoi_watch                2
  api_calls             7768
  api_keys                 0
  api_providers           10
  audit_log                1
  decisions                1
  investigations         138
  runs                    92

backup   : oceantrace.pre-m001.20260907T012256Z.bak

row counts after:
  aoi_watch                2
  api_calls             7768
  api_keys                 0
  api_providers           10
  audit_log                1
  decisions                1
  investigations         138
  runs                    92
  users                    0  (new table)

tables added: ['users']
verified   : no pre-existing table lost rows
```

Row counts are compared before and after: an additive migration must never
reduce one, so the check turns "additive by intent" into "additive by evidence".

### 4. Orphan database removed
```
main_system/data/oceantrace.db
  contents : {investigations: 0, runs: 0, api_calls: 49, api_providers: 10}
  backup   : dev_evidence/P06/orphan-main_system-oceantrace.db.20260907T012940Z.bak
  sha256   : c2f2286c804f3b4e6b1fa810715085b0...
  removed  : yes
```
Backed up before deletion even though it held no runs. It was untracked (`*.db`
is gitignored), so there is nothing for git to record - no separate commit is
possible and none is fabricated. The durable protection is the guardrail in
step 2, which now refuses this file if it is ever recreated.

## Reuse over reinvention

`init_db()` already ran `create_all()` + `_add_missing_columns()` - a working
additive migration mechanism. m001 does **not** replace it; it wraps it with the
resolve / count / back up / verify guardrails that were missing. Adding the
`User` model was therefore the whole schema change.

## Live lifecycle
```
GET  /api/auth/me                    -> 401
POST /api/auth/login  (bad password) -> 401 {"detail":"invalid email or password"}
POST /api/auth/login  (good)         -> 200
  set-cookie: oceantrace_session=<jwt>; HttpOnly; Max-Age=28800; Path=/; SameSite=lax
  body: {"user":{"id":1,"email":"...","role":"admin","active":true}}   <- no token in body
GET  /api/auth/me     (with cookie)  -> 200
POST /api/auth/logout                -> 204
GET  /api/auth/me     (after)        -> 401
```

## Existing routes still open (P06 must not guard)
```
  /api/runs             200
  /api/apis/status      200
  /api/metrics          200
  /api/scenes/local     200
  /api/investigations   200
```

## Security decisions and why

| decision | reason |
|---|---|
| argon2id, library defaults | RFC 9106 profile; hand-tuning without measuring on the host usually weakens it |
| **no** password-hash fallback | The credential vault degrades visibly to `plain:` because an unreadable provider key breaks the demo and the banner says so. A password store has no equivalent excuse - a silent downgrade is invisible forever. |
| HttpOnly cookie, token never in the body | Retires the `localStorage` admin-token hazard (AD-06). If the body carried the token a client could store it somewhere readable. |
| SameSite=Lax | Withholds the cookie from cross-site POSTs, which is what makes mutating routes safe without a separate CSRF token. Dev :5173 -> API :8000 is same-site (port is not part of same-site), so it works in development. |
| Secure defaults to `not DEBUG` | A production deployment must not silently ship a non-Secure cookie. Browsers exempt localhost, so dev is unaffected. |
| Signing secret >= 32 bytes, else refuse | Below that HS256 is not meaningfully protected (RFC 7518 3.2). Checked at issue time so a misconfigured deploy fails on login with a message, not silently. |
| Identical 401 for unknown account and wrong password | Otherwise the login form is an account-enumeration oracle - and these accounts are named investigators. A dummy argon2 verify keeps the timing the same too. |
| Role re-read from the DB every request | The token embeds a role for cheap rejection, but deactivating an account ends its session immediately rather than in up to 8 hours. |
| `admin` satisfies every guard | Listing it at each call site is how one eventually gets omitted. |
| Bootstrap never overwrites | Otherwise anyone able to set an env var could reset an operator's password by restarting the process. |
| Demo admin deleted after verification | A known-password admin account must not persist in the live database. Verified `users = 0`. |

## Two bugs the tests caught

1. `logout` built a fresh `Response`, discarding the Set-Cookie header it had just
   written - the user stayed signed in. Now the injected response is returned.
2. `delete_cookie` omitted `samesite`/`secure`, so the deleting cookie did not match
   the one it replaced and the client kept the original.

Both were only visible because the lifecycle test asserts 401 *after* logout
rather than trusting the 204.
