"""Database models and session handling.

SQLite for the POC, Postgres-ready: nothing here uses a SQLite-only type, so
switching is a `DATABASE_URL` change.

The `api_calls` table is what makes the monitoring page real rather than
decorative -- every outbound provider call is recorded with its latency and
outcome, so the page reports measured history instead of a live ping that says
nothing about the last hour.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, create_engine, func)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from backend.core.config import get_settings

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Investigation(Base):
    """One spill investigation: a scene plus every run against it."""

    __tablename__ = "investigations"

    id = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    scene_id = Column(String(200))
    scene_path = Column(Text)
    # The scene_meta the investigation was created with. Without this the path
    # was read once for scene_id and then discarded, so every run silently fell
    # back to the demo scene no matter which scene the user selected.
    scene_meta_path = Column(Text)
    bbox = Column(String(200))
    created_utc = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    notes = Column(Text)

    runs = relationship("Run", back_populates="investigation",
                        cascade="all, delete-orphan")


class Run(Base):
    """A single execution of the pipeline, and how honest its output was."""

    __tablename__ = "runs"

    id = Column(String(64), primary_key=True)
    investigation_id = Column(String(64), ForeignKey("investigations.id"))
    scene_id = Column(String(200))
    status = Column(String(32), default="pending")     # pending|running|complete|failed
    started_utc = Column(DateTime(timezone=True), default=utcnow)
    finished_utc = Column(DateTime(timezone=True))
    seconds = Column(Float, default=0.0)

    # Provenance summary, so a listing can show honesty at a glance without
    # opening the manifest.
    stages_total = Column(Integer, default=0)
    stages_real = Column(Integer, default=0)
    stages_mock = Column(Integer, default=0)
    stages_failed = Column(Integer, default=0)

    detect_engine = Column(String(32))
    manifest_path = Column(Text)
    error = Column(Text)

    investigation = relationship("Investigation", back_populates="runs")


class ApiProvider(Base):
    """Current health of one external dependency."""

    __tablename__ = "api_providers"

    name = Column(String(64), primary_key=True)
    purpose = Column(Text)
    owner = Column(String(64))
    kind = Column(String(32))
    chain = Column(Text)                                # comma-separated
    status = Column(String(16), default="UNKNOWN")      # WORKING|DEGRADED|FAILED|UNKNOWN
    active_provider = Column(String(64))
    last_code = Column(Integer)
    last_latency_ms = Column(Integer)
    last_success_utc = Column(DateTime(timezone=True))
    last_failure_utc = Column(DateTime(timezone=True))
    last_error_class = Column(String(32), default="NONE")
    consecutive_failures = Column(Integer, default=0)
    circuit_open_until = Column(DateTime(timezone=True))
    needs_credentials = Column(Boolean, default=False)
    has_credentials = Column(Boolean, default=False)


class ApiCall(Base):
    """Every outbound provider call. Powers the monitoring page's history."""

    __tablename__ = "api_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), index=True)
    endpoint = Column(Text)
    status = Column(String(16))                          # ok|failed
    http_code = Column(Integer)
    latency_ms = Column(Integer)
    error_class = Column(String(32))
    error_detail = Column(Text)
    occurred_utc = Column(DateTime(timezone=True), default=utcnow, index=True)


class ApiKey(Base):
    """A stored credential. The plaintext never leaves the server."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(64), index=True)
    field = Column(String(64))                           # e.g. CDSE_CLIENT_SECRET
    ciphertext = Column(Text)                            # encrypted at rest
    last_four = Column(String(8))                        # all the UI ever sees
    updated_utc = Column(DateTime(timezone=True), default=utcnow)
    updated_by = Column(String(64), default="admin")


class AuditLog(Base):
    """Who changed which credential, and when. Never records the value."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(64))
    provider = Column(String(64))
    field = Column(String(64))
    actor = Column(String(64), default="admin")
    detail = Column(Text)
    occurred_utc = Column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# engine / session
# --------------------------------------------------------------------------

_settings = get_settings()
_settings.data_root.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False}
    if _settings.database_url.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True)


def _add_missing_columns() -> None:
    """Additive schema catch-up for existing SQLite files.

    create_all() only creates missing TABLES, never missing COLUMNS, so a
    database created before a column was added keeps working while silently
    lacking it. Adding columns here keeps existing runs and keys intact
    instead of requiring the file to be deleted.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in insp.get_table_names():
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
            with engine.begin() as conn:
                conn.execute(text(ddl))


def init_db() -> None:
    """Create tables and seed the provider registry."""
    Base.metadata.create_all(engine)
    _add_missing_columns()
    from backend.core.config import PROVIDERS

    with SessionLocal() as db:
        for spec in PROVIDERS:
            row = db.get(ApiProvider, spec["name"])
            if row is None:
                row = ApiProvider(name=spec["name"])
                db.add(row)
            # Description fields are refreshed from the registry on every boot
            # so the code stays the source of truth; health fields are left
            # alone because they are measured, not declared.
            row.purpose = spec["purpose"]
            row.owner = spec["owner"]
            row.kind = spec["kind"]
            row.chain = ",".join(spec["chain"])
            row.needs_credentials = spec["needs_credentials"]
            if row.active_provider is None:
                row.active_provider = spec["chain"][0]
        db.commit()


def get_db():
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def record_call(db, provider: str, endpoint: str, status: str,
                latency_ms: Optional[int] = None, http_code: Optional[int] = None,
                error_class: Optional[str] = None,
                error_detail: Optional[str] = None) -> None:
    """Log one provider call and roll the provider's current health forward.

    Health is derived from real traffic rather than only from a health-check
    ping, because a provider that answers /ping but fails every download is
    not working, and the monitoring page should say so.
    """
    db.add(ApiCall(provider=provider, endpoint=endpoint, status=status,
                   http_code=http_code, latency_ms=latency_ms,
                   error_class=error_class, error_detail=error_detail))

    row = db.get(ApiProvider, provider)
    if row is None:
        row = ApiProvider(name=provider, purpose="(unregistered)")
        db.add(row)

    row.last_code = http_code
    row.last_latency_ms = latency_ms
    if status == "ok":
        row.last_success_utc = utcnow()
        row.last_error_class = "NONE"
        row.consecutive_failures = 0
        row.status = "WORKING"
    else:
        row.last_failure_utc = utcnow()
        row.last_error_class = error_class or "BAD_RESPONSE"
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.status = "FAILED"
    db.commit()
