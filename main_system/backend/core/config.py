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

        # --- public evaluator view (SIH) ------------------------------------
        # When on, a visitor with NO session is served as a fixed, password-less
        # evaluator account instead of being sent to the sign-in form, so a
        # judge can open the URL and see every screen. A real sign-in still
        # takes precedence, so production RBAC stays demonstrable from the
        # Login button. Off by default: a deployment must opt in explicitly.
        self.public_evaluator = (
            os.getenv("OT_PUBLIC_EVALUATOR", "false").lower() == "true")
        self.evaluator_email = os.getenv(
            "OT_EVALUATOR_EMAIL", "evaluator@oceantrace.public").strip().lower()
        # admin sees every screen and can run every workflow; super_admin would
        # also let an anonymous visitor redraw protected jurisdiction boundaries.
        self.evaluator_role = os.getenv("OT_EVALUATOR_ROLE", "admin")

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
            # AISStream was deliberately absent while live AIS was
            # NOT_DEPLOYED -- offering a key field for a capability that did
            # not exist invited operators to configure nothing. The live
            # ingest worker now consumes this key, so the field is real.
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

    # Adapters that exist and are NOT wired to the pipeline. Listing them as
    # NOT_DEPLOYED is the point: leaving them out entirely would let a reader
    # assume optical and live AIS were never considered, and showing them
    # alongside working providers would imply they are available. Neither is
    # true, and the difference is the roadmap.
    {"name": "Sentinel2", "purpose": "Optical confirmation of SAR detections",
     "owner": "Pavitra", "chain": [], "needs_credentials": False,
     "kind": "optical", "deployment": "NOT_DEPLOYED",
     "not_deployed_reason":
         "The adapter exists and is unit-tested (scene_service/satellite/"
         "s2_adapter.py) but no optical data is wired into the pipeline and no "
         "accuracy has been measured for it. /api/scenes/search?source=S2 "
         "returns 501 rather than an empty list, because an empty list would "
         "read as 'we looked and found none'."},
    # Live AIS was NOT_DEPLOYED on the grounds that a stream "cannot answer
    # questions about a scene acquired in the past, which is every question
    # this system asks". That objection was correct about a stream ALONE and
    # is answered by archiving it: the ingest worker appends every observation
    # to the same day-partitioned AISStore the historical providers write to,
    # so today's stream is next month's archive and an investigation can query
    # it exactly as it queries MarineCadastre.
    #
    # It does NOT retroactively cover a past scene. A Bay of Bengal
    # investigation into an acquisition from before ingestion started still
    # has no AIS, and PROVIDER_COVERAGE below says so rather than implying the
    # archive is complete.
    {"name": "AISStream", "purpose": "Live AIS over WebSocket, archived for "
                                     "later investigation",
     "owner": "Krishnan", "chain": ["AISStream"], "needs_credentials": True,
     "kind": "ais"},
]

# Where each provider actually has data. Stated so the catalogue can say "this
# provider does not cover your AOI" instead of letting a search return nothing
# and leaving the operator to guess whether that is an outage.
PROVIDER_COVERAGE: Dict[str, dict] = {
    "CDSE": {"dataset": "SENTINEL-1 GRD (IW)", "bbox": [-180, -90, 180, 90],
             "temporal": "2014-10-03 .. present", "resolution": "10 m",
             "note": "global"},
    "ASF": {"dataset": "SENTINEL-1 GRD (IW)", "bbox": [-180, -90, 180, 90],
            "temporal": "2014-10-03 .. present", "resolution": "10 m",
            "note": "global; NASA Earthdata mirror"},
    "LocalCache": {"dataset": "downloaded scenes on this host",
                   "bbox": None, "temporal": "whatever has been fetched",
                   "resolution": "10 m", "note": "see /api/scenes/local"},
    "CMEMS": {"dataset": "GLORYS / Analysis-Forecast surface currents",
              "bbox": [-180, -80, 180, 90], "temporal": "1993 .. present",
              "resolution": "1/12 deg, daily means",
              "note": "daily means: no tidal or sub-daily structure"},
    "HYCOM": {"dataset": "GOFS 3.1 surface currents", "bbox": [-180, -80, 180, 90],
              "temporal": "1994 .. present", "resolution": "1/12 deg",
              "note": "OPeNDAP; used when CMEMS is unavailable"},
    "ERA5": {"dataset": "ERA5 single-levels 10 m wind",
             "bbox": [-180, -90, 180, 90], "temporal": "1940 .. present (~5 day lag)",
             "resolution": "0.25 deg, hourly", "note": "CDS queue can be slow"},
    "OpenMeteo": {"dataset": "ERA5-derived 10 m wind (point API)",
                  "bbox": [-180, -90, 180, 90], "temporal": "1940 .. present",
                  "resolution": "hourly; sampled per point",
                  "note": "point API -- the adapter samples bbox corners, so "
                          "the field is spatially coarse"},
    "DMA": {"dataset": "Danish Maritime Authority AIS archive",
            "bbox": [3.0, 53.0, 17.0, 59.0], "temporal": "2006 .. present",
            "resolution": "1 s .. 1 min reports",
            "note": "Danish/Baltic waters ONLY; adapter not yet exercised on a "
                    "real archive"},
    "MarineCadastre": {"dataset": "NOAA AISDataHandler daily archives",
                       "bbox": [-180, 0, -60, 75], "temporal": "2009 .. present",
                       "resolution": "1 min reports",
                       "note": "US waters ONLY; exercised end to end by the "
                               "flagship run"},
    "SyntheticGenerator": {"dataset": "generated around a run's own origin",
                           "bbox": None, "temporal": "any",
                           "resolution": "configurable",
                           "note": "every row is flagged SYNTHETIC"},
    "Sentinel2": {"dataset": "Sentinel-2 L2A (adapter only)",
                  "bbox": [-180, -90, 180, 90], "temporal": "n/a -- not deployed",
                  "resolution": "10 m", "note": "NOT DEPLOYED"},
    "AISStream": {"dataset": "live AIS WebSocket, archived to AISStore",
                  # Bounded by the ingest worker's subscription, not by the
                  # provider: AISStream is global, but this deployment only
                  # ever subscribed to the zones it was configured for, so the
                  # archive covers those and nothing else.
                  "bbox": None,
                  "temporal": "from the moment ingestion was first started on "
                              "this deployment -- see /api/ais/status for the "
                              "actual archive span. NOT retroactive: a scene "
                              "acquired before ingestion began has no live AIS.",
                  "resolution": "2 s .. 3 min reports (Class A), 30 s .. 3 min "
                                "(Class B)",
                  "note": "positions are relayed by volunteer receivers, so "
                          "coverage is dense near coasts and sparse in open "
                          "ocean; a vessel absent from the archive was not "
                          "necessarily absent from the water"},
}

PROVIDER_BY_NAME = {p["name"]: p for p in PROVIDERS}
