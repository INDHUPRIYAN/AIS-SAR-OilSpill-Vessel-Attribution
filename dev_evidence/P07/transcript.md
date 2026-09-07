# P07 - RBAC enforcement, admin-token retirement, vitest, CORS

Executed: 2026-09-07T02:12:35Z

## Enforcement model

Authentication is applied ONCE, to whole routers, in `main.py`:
```
_authenticated = [Depends(authenticated)]
app.include_router(router,           prefix="/api", dependencies=_authenticated)
app.include_router(analytics_router, prefix="/api", dependencies=_authenticated)
... (6 routers)
app.include_router(auth_router,      prefix="/api")   # login must stay reachable
```
The audit's finding was not that a guard was wrong -- it was that per-route
guards get MISSED, and an unguarded route looks identical to one that never
needed a guard. A router-level dependency cannot be forgotten by a route that
does not exist yet, so new endpoints are guarded by default.

Authorisation is necessarily finer-grained and sits on the routes needing
elevation. `test_elevated_list_covers_every_mutating_route` fails if a new
POST/PUT/DELETE appears without a role guard or an explicit classification.

## Route x role matrix (generated from the live route table)
```

ROUTE x ROLE MATRIX  (generated from the live route table)
======================================================================================
METHOD  PATH                                                ACCESS
--------------------------------------------------------------------------------------
GET     /                                                   PUBLIC
GET     /api/aois                                           any authenticated
POST    /api/aois/poll                                      admin,analyst,investigator
GET     /api/aois/{aoi_id}                                  any authenticated
POST    /api/aois/{aoi_id}/poll                             admin,analyst,investigator
GET     /api/apis/status                                    any authenticated
POST    /api/apis/test-all                                  admin,analyst
GET     /api/apis/{provider}/calls                          any authenticated
POST    /api/apis/{provider}/test                           admin,analyst
GET     /api/attribution/weights                            any authenticated
POST    /api/auth/login                                     PUBLIC
POST    /api/auth/logout                                    PUBLIC
GET     /api/auth/me                                        any authenticated
GET     /api/auth/roles                                     any authenticated
GET     /api/decisions                                      any authenticated
GET     /api/investigations                                 any authenticated
POST    /api/investigations                                 admin,analyst,investigator
GET     /api/investigations/{investigation_id}              any authenticated
GET     /api/investigations/{investigation_id}/layers/{layer}any authenticated
POST    /api/investigations/{investigation_id}/replay       admin,analyst,investigator
POST    /api/investigations/{investigation_id}/run          admin,analyst,investigator
GET     /api/investigations/{investigation_id}/status       any authenticated
GET     /api/investigations/{investigation_id}/suspects     any authenticated
GET     /api/keys                                           admin
PUT     /api/keys                                           admin
GET     /api/keys/audit                                     admin
POST    /api/keys/{provider}/test                           admin
GET     /api/layers/{run_id}/{layer}                        any authenticated
GET     /api/metrics                                        any authenticated
GET     /api/replay/runs                                    any authenticated
GET     /api/runs                                           any authenticated
GET     /api/runs/{run_id}                                  any authenticated
GET     /api/runs/{run_id}/decisions                        any authenticated
POST    /api/runs/{run_id}/decisions                        admin,analyst,investigator
GET     /api/runs/{run_id}/export                           any authenticated
GET     /api/runs/{run_id}/forcing_field                    any authenticated
GET     /api/runs/{run_id}/mask_png                         any authenticated
GET     /api/runs/{run_id}/scene_png                        any authenticated
GET     /api/runs/{run_id}/verify                           any authenticated
GET     /api/runs/{run_id}/vessels_geojson                  any authenticated
GET     /api/scenes/local                                   any authenticated
GET     /api/scenes/local/{scene_id}                        any authenticated
GET     /docs                                               PUBLIC
GET     /docs/oauth2-redirect                               PUBLIC
GET     /health                                             PUBLIC
GET     /healthz                                            PUBLIC
GET     /openapi.json                                       PUBLIC
GET     /readyz                                             PUBLIC
GET     /redoc                                              PUBLIC
```

## Three bugs the tests caught (two of them in the tests themselves)

**1. The route walker was blind.** This FastAPI keeps `include_router` results as
`_IncludedRouter` wrappers instead of flattening them into `app.routes`, so the
first version of `test_no_route_is_reachable_anonymously` saw **15 of 49** routes
and passed while checking almost nothing. An RBAC test that silently checks
nothing is worse than no RBAC test. Fixed via `effective_candidates()`, and
`test_the_walker_sees_the_whole_api` now fails if discovery ever regresses.

**2. The role matrix was starting real pipeline runs.** Probing
`POST /api/investigations/{id}/run` to find out who may start a run *started
runs*; the daemon thread was still opening netCDF files as pytest tore the
process down, segfaulting the interpreter. The matrix is now static: it reads
the declared guard off each route's dependency tree. That answers the same
question with no side effects, and answers it about the declaration rather than
about whatever a handler happened to return.

**3. The legacy admin token was dead on arrival.** Blanket `current_user` at
router level rejected the token before `require_admin` ever saw it -- a
deprecation that was really a breaking change. The router gate is now
`authenticated`, which accepts a session or (only when opted in) the token.

## Admin-token retirement
```
default                              -> 401  (OT_ALLOW_LEGACY_ADMIN_TOKEN unset)
OT_ALLOW_LEGACY_ADMIN_TOKEN=true     -> 200  (one release only)
admin session, no token at all       -> 200  (the replacement path)
investigator/analyst/reviewer/auditor -> 403 on /api/keys
```
The shared token identified nobody, so an audit row it produced could only ever
say "admin". `require_admin` now returns the actor's email.

## Frontend
```
localStorage admin token   REMOVED (audit AD-06)
  - api.js has no getAdminToken/setAdminToken and no X-Admin-Token header
  - every request carries credentials: "include"; the cookie is HttpOnly,
    so page JavaScript cannot read the session at all
SessionProvider + useSession   asks GET /api/auth/me; no client-side copy to go stale
SignIn page                    shows the server's uniform 401 message verbatim
Keys page                      admin session gates it; the token form is deleted
App shell                      gated -- a signed-out user sees sign-in, not a
                               frame full of failed panels
```

## Vitest (moved here from P19 per v1.1 amendment 6)
```
npm test  ->  6 passed
  asks the server who is signed in, rather than trusting local state
  sends credentials so the HttpOnly cookie actually rides along
  treats a 401 as signed out, not as an error page
  shows 'checking' first, so a reload does not flash the login form
  keeps no token anywhere JavaScript can read
  Unauthenticated is distinguishable from an ordinary failure

npm run build -> built in 16.65s
```

## CORS (v1.1 amendment 6)
Origins move to `CORS_ORIGINS`; `*` is stripped because it is invalid alongside
`allow_credentials` and would silently disable the cookie the session depends on.
