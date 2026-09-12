"""OceanTrace main system -- FastAPI application entry point.

    uvicorn backend.main:app --reload --port 8000

Serves the investigation API, the layer files the GIS UI renders, the API
monitoring endpoints and the admin key-management routes. The background
health scheduler probes every provider on an interval so the monitoring page
shows current state rather than whatever the last user action happened to hit.
"""
from __future__ import annotations

import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from backend.core.authz import authenticated  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402

from backend.api.analytics import router as analytics_router  # noqa: E402
from backend.api.replay import router as replay_router  # noqa: E402
from backend.api.investigation_page import router as invpage_router  # noqa: E402
from backend.api.audit import router as audit_router  # noqa: E402
from backend.api.auth import bootstrap_admin, router as auth_router  # noqa: E402
from backend.api.incidents import router as incidents_router  # noqa: E402
from backend.api.routes import router  # noqa: E402
from backend.api.scenes import router as scenes_router  # noqa: E402
from backend.api.alerts import router as alerts_router
from backend.api.search import router as search_router
from backend.api.zones import router as zones_router
from backend.api.ais_live import router as ais_live_router
from backend.api.tiles import router as tiles_router
from backend.api.catalog import router as catalog_router
from backend.api.events import router as events_router
from backend.api.reports import router as reports_router
from backend.api.vessels import router as vessels_router  # noqa: E402
from backend.api.scheduler_routes import router as scheduler_router  # noqa: E402
from backend.core.config import get_settings  # noqa: E402
from backend.models.db import SessionLocal, init_db, utcnow  # noqa: E402

settings = get_settings()
_health_stop = threading.Event()
_watcher = None


def _health_loop() -> None:
    """Probe every provider on an interval.

    Runs in a daemon thread rather than a task queue because the POC has one
    process and the probes are cheap. Failures here must never take down the
    API, so everything is swallowed and retried on the next tick.
    """
    from backend.services.providers import health

    # Let the app finish starting before the first sweep.
    _health_stop.wait(5)
    while not _health_stop.is_set():
        try:
            with SessionLocal() as db:
                health.probe_all(db)
        except Exception as exc:                      # pragma: no cover
            print(f"[health] sweep failed: {type(exc).__name__}: {exc}")
        _health_stop.wait(settings.health_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Nothing is running at boot. Any row that says otherwise belongs to a
    # process that died; mark it failed with the reason rather than let the UI
    # show work in progress that does not exist.
    from backend.models.db import SessionLocal
    from backend.services import jobs as jobs_service

    with SessionLocal() as db:
        swept = jobs_service.sweep_dead_runs(db)
    if swept["swept"]:
        print(f"[jobs] marked {len(swept['swept'])} dead in-flight run(s) failed: "
              f"{', '.join(swept['swept'])}")
    if swept["sealed_but_unmarked"]:
        print(f"[jobs] {len(swept['sealed_but_unmarked'])} run(s) sealed but still marked "
              f"in-flight; run backfill_runs --refresh: {', '.join(swept['sealed_but_unmarked'])}")
    # First-run administrator, only when both env vars are set and the
    # user table is empty. A checkout with neither gets no account at all
    # rather than a well-known one.
    note = bootstrap_admin()
    if note:
        print(f"[auth] {note}")

    # Operational zones. Seeded ONLY into a database with no zones at all, for
    # the same reason the AOI YAML is migrated once: a seed that re-ran every
    # boot would resurrect a zone an operator deleted and undo a boundary they
    # moved. A deployment that wants a different theatre deletes these and
    # draws its own; nothing here will put them back.
    from backend.services.zone_seed import seed_if_empty

    with SessionLocal() as db:
        try:
            zone_note = seed_if_empty(db)
        except Exception as exc:                   # noqa: BLE001
            # A bad seed polygon must not stop the API from serving. It shows
            # up as an empty zone list, which the UI reports honestly.
            zone_note = None
            print(f"[zones] seed failed: {type(exc).__name__}: {exc}")
    if zone_note:
        print(f"[zones] {zone_note}")
    if settings.admin_token_is_ephemeral:
        # Printed once, never logged again. Without this a fresh checkout would
        # either have no admin auth or a guessable default -- both worse.
        print("\n" + "=" * 62)
        print("  ADMIN TOKEN (ephemeral, set ADMIN_TOKEN in .env to fix it):")
        print(f"    {settings.admin_token}")
        print("  Send it as the X-Admin-Token header on /api/keys routes.")
        print("=" * 62 + "\n")

    thread = None
    if settings.health_enabled:
        thread = threading.Thread(target=_health_loop, daemon=True)
        thread.start()

    # STAGE 0: watch registered AOIs for new Sentinel-1 passes (§4, §27·8).
    # Opt-in via SCHEDULER_ENABLED, because this makes outbound provider calls
    # and can start pipeline runs unattended.
    global _watcher
    if settings.scheduler_enabled:
        from backend.api.scheduler_routes import build_watcher

        try:
            _watcher = build_watcher()
            _watcher.start(settings.scheduler_interval_seconds)
            print(f"[scheduler] AOI watcher started "
                  f"(wakes every {settings.scheduler_interval_seconds}s)")
        except Exception as exc:
            # A broken AOI registry must not stop the API from serving.
            print(f"[scheduler] not started: {type(exc).__name__}: {exc}")

    try:
        yield
    finally:
        _health_stop.set()
        if _watcher is not None:
            _watcher.stop()


app = FastAPI(
    title="OceanTrace",
    description="SAR oil-spill detection with AIS vessel attribution "
                "(SIH 2026 · PS26143)",
    version="0.1.0",
    lifespan=lifespan,
)

# The UI is served from a different origin during development. Origins are
# configurable (CORS_ORIGINS) because the session cookie rides on these
# requests: a deployment on a real hostname must be able to allow itself
# without a code change, and a wildcard is rejected outright since it is
# invalid alongside allow_credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication is applied HERE, once, to whole routers -- not decorated onto
# individual routes. The audit found per-route guards get missed, and a route
# that forgets its dependency looks exactly like one that never needed it. A
# router-level dependency cannot be forgotten by a route that does not exist
# yet, so new endpoints are guarded by default and have to opt out on purpose.
#
# Authorisation (which role may do what) is necessarily finer-grained and sits
# on the routes that need elevation. `test_route_role_matrix` enumerates every
# mounted route and asserts the result, so an omission fails the suite rather
# than shipping.
_authenticated = [Depends(authenticated)]

app.include_router(router, prefix="/api", dependencies=_authenticated)
app.include_router(analytics_router, prefix="/api", dependencies=_authenticated)
app.include_router(replay_router, prefix="/api", dependencies=_authenticated)
app.include_router(invpage_router, prefix="/api", dependencies=_authenticated)
app.include_router(scheduler_router, prefix="/api", dependencies=_authenticated)
app.include_router(scenes_router, prefix="/api", dependencies=_authenticated)
app.include_router(audit_router, prefix="/api", dependencies=_authenticated)
app.include_router(incidents_router, prefix="/api", dependencies=_authenticated)
app.include_router(vessels_router, prefix="/api", dependencies=_authenticated)
app.include_router(events_router, prefix="/api", dependencies=_authenticated)
app.include_router(reports_router, prefix="/api", dependencies=_authenticated)
app.include_router(catalog_router, prefix="/api", dependencies=_authenticated)
app.include_router(alerts_router, prefix="/api", dependencies=_authenticated)
app.include_router(tiles_router, prefix="/api", dependencies=_authenticated)
app.include_router(search_router, prefix="/api", dependencies=_authenticated)
app.include_router(zones_router, prefix="/api", dependencies=_authenticated)
app.include_router(ais_live_router, prefix="/api", dependencies=_authenticated)
# Public by necessity: /auth/login is how a session is obtained. The routes in
# here that need a session (/auth/me, /auth/roles) declare it themselves.
app.include_router(auth_router, prefix="/api")


@app.get("/health")
@app.get("/healthz")
def health_check():
    """Liveness probe for the process itself, not for the providers."""
    return {"status": "ok", "app": settings.app_name, "utc": utcnow()}


@app.get("/readyz")
def readiness_check():
    """Readiness probe: the DB must answer before traffic is routed here."""
    try:
        with SessionLocal() as db:
            db.execute(sa_text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"db not ready: {type(exc).__name__}")
    return {"status": "ready", "app": settings.app_name, "utc": utcnow()}


@app.get("/")
def root():
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "endpoints": {
            "investigations": "/api/investigations",
            "runs": "/api/runs",
            "layers": "/api/layers/{run_id}/{layer}",
            "monitoring": "/api/apis/status",
            "auth": "/api/auth/login",
            "keys": "/api/keys  (admin session required)",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port,
                reload=settings.debug)
