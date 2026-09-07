"""Application settings, loaded from the environment.

Credentials never live in code and never reach the frontend bundle. They are
read from `.env` (or the real environment in deployment) and, once the Key
Management page is used, from the encrypted key table -- the DB wins, because
that is what an operator can change without a redeploy.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader. Real environment variables always win, so a
    deployment can override the file without editing it."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


class Settings:
    """Runtime configuration. Read once, at import."""

    def __init__(self) -> None:
        self.app_name = "OceanTrace"
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8000"))
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # Anchored to the repo, never to the working directory. A relative
        # DATA_ROOT like "./data" resolved differently for the API server
        # (started from main_system/) than for the CLI tools, so the
        # monitoring page reported LocalCache DEGRADED while the scenes were
        # sitting on disk the whole time.
        raw_root = Path(os.getenv("DATA_ROOT", str(DATA_ROOT)))
        self.data_root = raw_root if raw_root.is_absolute() else (REPO_ROOT / raw_root).resolve()
        self.runs_root = self.data_root / "runs"
        self.mocks_root = REPO_ROOT / "contracts" / "mocks"
        self.database_url = os.getenv(
            "DATABASE_URL", f"sqlite:///{(self.data_root / 'oceantrace.db').as_posix()}")

        # Admin auth for key management. Generated per-process if unset so a
        # fresh checkout is never silently protected by a well-known password.
        self.admin_token = os.getenv("ADMIN_TOKEN") or self._ephemeral_token()
        self.admin_token_is_ephemeral = not os.getenv("ADMIN_TOKEN")
        # The shared admin header is superseded by real sessions. It keeps
        # working for one release, but only when a deployment opts in --
        # a deprecation nobody can switch off is not a deprecation.
        self.allow_legacy_admin_token = (
            os.getenv("OT_ALLOW_LEGACY_ADMIN_TOKEN", "false").lower() == "true")

        # Symmetric key for encrypting stored credentials at rest.
        self.secret_key = os.getenv("SECRET_KEY", "")

        # --- sessions -----------------------------------------------------
        # Session tokens are signed with their own secret where one is given,
        # falling back to SECRET_KEY so a working deployment does not need two
        # variables on day one. They are separable because they protect
        # different things: rotating the session secret logs everyone out,
        # rotating the vault key makes every stored credential unreadable.
        self.jwt_secret = os.getenv("JWT_SECRET") or self.secret_key
        self.jwt_algorithm = "HS256"
        self.jwt_ttl_hours = int(os.getenv("JWT_TTL_HOURS", "8"))
        self.session_cookie = os.getenv("SESSION_COOKIE", "oceantrace_session")
        # Secure cookies require HTTPS, which the dev server does not speak.
        # Default follows DEBUG rather than being hardcoded off, so a
        # production deployment does not silently ship a non-Secure cookie.
        secure_env = os.getenv("SESSION_COOKIE_SECURE")
        self.session_cookie_secure = (
            secure_env.lower() == "true" if secure_env is not None else not self.debug)

        # First-run administrator. Absent by default: a checkout with no
        # credentials set gets NO account rather than a well-known one.
        self.admin_email = os.getenv("OT_ADMIN_EMAIL", "")
        self.admin_password = os.getenv("OT_ADMIN_PASSWORD", "")

        # Browser origins allowed to call the API with credentials. The dev
        # ports are the default; a deployment sets CORS_ORIGINS. Never "*":
        # a wildcard is invalid with allow_credentials and would silently
        # disable the cookie the whole session model depends on.
        self.cors_origins = [
            o.strip() for o in os.getenv(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:8501,http://127.0.0.1:8501").split(",")
            if o.strip() and o.strip() != "*"]

        self.health_interval_seconds = int(os.getenv("HEALTH_INTERVAL", "60"))
        self.health_enabled = os.getenv("HEALTH_ENABLED", "true").lower() == "true"

        # STAGE 0, the AOI watcher (design doc v2 §4, §27·8). Off by default:
        # it issues outbound provider searches and can start pipeline runs on
        # its own, so a fresh checkout or a demo laptop must opt in rather than
        # discover it. `SCHEDULER_INTERVAL` is how often the loop WAKES; each
        # AOI's own poll_minutes decides whether it is actually due.
        self.scheduler_enabled = os.getenv("SCHEDULER_ENABLED", "false").lower() == "true"
        self.scheduler_interval_seconds = int(os.getenv("SCHEDULER_INTERVAL", "300"))

    @staticmethod
    def _ephemeral_token() -> str:
        import secrets
        return secrets.token_urlsafe(24)

    def env_credentials(self, provider: str) -> Dict[str, Optional[str]]:
        """Credentials for a provider as currently present in the environment."""
        mapping = {
            "CDSE": ("CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET", "CDSE_USERNAME", "CDSE_PASSWORD"),
            "ASF": ("EARTHDATA_USER", "EARTHDATA_PASS", "ASF_USERNAME", "ASF_PASSWORD"),
            "CMEMS": ("CMEMS_USERNAME", "CMEMS_PASSWORD"),
            "ERA5": ("CDSAPI_KEY", "CDSAPI_URL"),
            "OpenMeteo": (),
            "HYCOM": (),
            "DMA": (),
            "MarineCadastre": (),
            "AISStream": ("AISSTREAM_API_KEY",),
        }
        return {name: os.getenv(name) for name in mapping.get(provider, ())}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------
# Provider registry -- the single source of truth for the monitoring page
# --------------------------------------------------------------------------

PROVIDERS: List[dict] = [
    {"name": "CDSE", "purpose": "Sentinel-1 scene search + download",
     "owner": "Pavitra", "chain": ["CDSE", "ASF", "LocalCache"],
     "needs_credentials": True, "kind": "satellite"},
    {"name": "ASF", "purpose": "Sentinel-1 fallback via ASF Vertex",
     "owner": "Pavitra", "chain": ["CDSE", "ASF", "LocalCache"],
     "needs_credentials": True, "kind": "satellite"},
    {"name": "LocalCache", "purpose": "Pre-downloaded scenes (guaranteed path)",
     "owner": "Pavitra", "chain": ["CDSE", "ASF", "LocalCache"],
     "needs_credentials": False, "kind": "satellite"},

    {"name": "CMEMS", "purpose": "Ocean currents (GLORYS multiyear)",
     "owner": "Keerthana", "chain": ["CMEMS", "HYCOM", "StaticCache"],
     "needs_credentials": True, "kind": "currents"},
    {"name": "HYCOM", "purpose": "Ocean currents fallback via OPeNDAP",
     "owner": "Keerthana", "chain": ["CMEMS", "HYCOM", "StaticCache"],
     "needs_credentials": False, "kind": "currents"},
    {"name": "ERA5", "purpose": "Historical 10 m wind for drift",
     "owner": "Keerthana", "chain": ["ERA5", "OpenMeteo", "StaticCache"],
     "needs_credentials": True, "kind": "wind"},
    {"name": "OpenMeteo", "purpose": "Wind fallback, no key required",
     "owner": "Keerthana", "chain": ["ERA5", "OpenMeteo", "StaticCache"],
     "needs_credentials": False, "kind": "wind"},

    {"name": "DMA", "purpose": "Real AIS, Danish Maritime Authority archive",
     "owner": "Krishnan", "chain": ["DMA", "MarineCadastre", "SyntheticGenerator"],
     "needs_credentials": False, "kind": "ais"},
    {"name": "MarineCadastre", "purpose": "Real AIS, US waters",
     "owner": "Krishnan", "chain": ["DMA", "MarineCadastre", "SyntheticGenerator"],
     "needs_credentials": False, "kind": "ais"},
    {"name": "SyntheticGenerator", "purpose": "Synthetic AIS with known culprit",
     "owner": "Krishnan", "chain": ["DMA", "MarineCadastre", "SyntheticGenerator"],
     "needs_credentials": False, "kind": "ais"},
]

PROVIDER_BY_NAME = {p["name"]: p for p in PROVIDERS}
