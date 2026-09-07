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

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402

from backend.api.analytics import router as analytics_router  # noqa: E402
from backend.api.replay import router as replay_router  # noqa: E402
from backend.api.investigation_page import router as invpage_router  # noqa: E402
from backend.api.auth import bootstrap_admin, router as auth_router  # noqa: E402
from backend.api.routes import router  # noqa: E402
from backend.api.scenes import router as scenes_router  # noqa: E402
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
    # First-run administrator, only when both env vars are set and the
    # user table is empty. A checkout with neither gets no account at all
    # rather than a well-known one.
    note = bootstrap_admin()
    if note:
        print(f"[auth] {note}")
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

# The UI is served from a different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501",
                   "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(replay_router, prefix="/api")
app.include_router(invpage_router, prefix="/api")
app.include_router(scheduler_router, prefix="/api")
app.include_router(scenes_router, prefix="/api")
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
            "keys": "/api/keys  (X-Admin-Token required)",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port,
                reload=settings.debug)
