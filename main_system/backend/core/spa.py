"""Serve the built React app from the API process (single-container deploys).

Local development and docker-compose keep the UI on its own server (Vite or
nginx) and proxy /api to this process. A single-container host -- a Hugging
Face Space, or any VM running one image -- has one port and no proxy, so the
API serves the SPA itself, which keeps the browser same-origin exactly as the
proxies do: no CORS, and the session cookie never crosses an origin.

Opt-in via OT_FRONTEND_DIST (the `npm run build` output directory). Unset, the
app is unchanged, so the test suite and the compose stack never see this.
"""
from __future__ import annotations

from pathlib import Path

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

# Paths the SPA fallback must never swallow: an unknown API route has to stay
# a JSON 404, not come back as index.html with a 200 the client then parses.
_BACKEND_PREFIXES = ("api/", "ws/", "health", "healthz", "readyz",
                     "docs", "redoc", "openapi.json")


class SPAStaticFiles(StaticFiles):
    """StaticFiles that answers unknown client-side routes with index.html,
    so a deep link like /investigation?run=... survives a page reload."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Starlette hands over an OS-normalised path (backslashes on Windows).
            if (exc.status_code != 404
                    or path.replace("\\", "/").startswith(_BACKEND_PREFIXES)):
                raise
            return await super().get_response("index.html", scope)


def frontend_dist(raw: str | None) -> Path | None:
    """The build directory, or None when unset or not actually built."""
    if not raw:
        return None
    dist = Path(raw)
    return dist if (dist / "index.html").is_file() else None


def mount_spa(app, dist: Path) -> None:
    """Mount LAST: routes registered before the mount win over it."""
    app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="spa")
