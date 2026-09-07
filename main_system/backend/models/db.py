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
    # Nullable: investigations predate incidents, and one can legitimately be
    # started before anyone decides it belongs to a case.
    incident_id = Column(String(32), ForeignKey("incidents.id"), nullable=True,
                         index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    runs = relationship("Run", back_populates="investigation",
                        cascade="all, delete-orphan")
    incident = relationship("Incident", back_populates="investigations")


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

    # Stamped from the investigation when the run is created rather than joined
    # at read time: an investigation can be re-filed under a different incident
    # later, and a sealed run must keep saying which case it was evidence for.
    incident_id = Column(String(32), ForeignKey("incidents.id"), nullable=True,
                         index=True)

    investigation = relationship("Investigation", back_populates="runs")


class Decision(Base):
    """STAGE 9 -- what a human concluded about a run, and about which vessel.

    Design doc v2 §4 Stage 9 and §14:

        "Analyst accepts / rejects / annotates. Stored with the run.
         -> audit trail, and your future feedback corpus."

        "No auto-accusation. Ranked candidates + evidence; a human decides."

    This table is the second half of Standing Rule 8. Without it the system
    ranks candidates and then simply stops -- the decision that actually matters
    happens in someone's head and leaves no record, so an investigation cannot
    be reconstructed and there is no feedback corpus to learn from later.

    `verdict` deliberately does not include "guilty". §2: the interface says
    *suspect*, *candidate*, *evidence*. What an analyst records here is whether
    the SYSTEM'S RANKING was sound and worth pursuing, not whether a named
    operator polluted:

        accepted     the ranking is sound; this candidate is worth pursuing
        rejected     the ranking is wrong; this candidate is not the source
        inconclusive the evidence does not support a call either way
        annotated    a note, no verdict

    `mmsi` is null for a verdict on the run as a whole (e.g. "no slick here at
    all"), and set when the analyst is judging one specific candidate.

    Rows are append-only by convention: a changed mind is a new row, so the
    sequence of decisions is itself part of the audit trail.
    """

    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), ForeignKey("runs.id"), index=True, nullable=False)
    investigation_id = Column(String(64), index=True)

    # Null = a verdict on the run itself rather than on one candidate vessel.
    mmsi = Column(Integer, index=True)
    suspect_rank = Column(Integer)
    # The score the analyst was looking at when they decided. Recorded rather
    # than looked up later, because a re-run with different weights would
    # otherwise silently rewrite the context of a past decision.
    total_score_at_decision = Column(Float)

    verdict = Column(String(16), nullable=False)   # accepted|rejected|inconclusive|annotated
    note = Column(Text)
    actor = Column(String(64), default="analyst")
    decided_utc = Column(DateTime(timezone=True), default=utcnow, index=True)

    # What the analyst was actually shown. §14 requires full parameter capture,
    # and a decision is only reproducible if you know which artefacts produced
    # the screen it was made from.
    artefact_digest = Column(String(64))
    weights_used = Column(Text)                    # JSON, as displayed in the UI


VERDICTS = ("accepted", "rejected", "inconclusive", "annotated")


class AoiWatch(Base):
    """Persisted watch state for one registered AOI -- STAGE 0 (design v2 §4).

    The scheduler is the one component that must survive a restart without
    doing its work twice. Without this table, every process start would look
    like a first poll and re-open an investigation for every scene inside the
    lookback window -- a duplicate investigation per restart, each one a full
    pipeline run.

    `last_scene_time_utc` is the high-water mark: the acquisition time of the
    newest scene already handled. A scene counts as new only if it was acquired
    strictly after it. Acquisition time is used rather than "when we saw it"
    because providers publish out of order and a slow ingest must not cause a
    scene to be skipped.

    The AOI definitions themselves live in config/aois.yaml (§21) and are NOT
    duplicated here -- only the mutable state. `aoi_id` is the join key, which
    is why renaming an id in the YAML resets that AOI's watch.
    """

    __tablename__ = "aoi_watch"

    aoi_id = Column(String(64), primary_key=True)
    last_polled_utc = Column(DateTime(timezone=True))
    last_scene_id = Column(String(200))
    last_scene_time_utc = Column(DateTime(timezone=True))

    # WORKING | DEGRADED | FAILED | UNKNOWN -- mirrors the provider vocabulary
    # so the monitoring page can render AOIs and providers with one component.
    status = Column(String(16), default="UNKNOWN")
    last_error_class = Column(String(32), default="NONE")
    last_error = Column(Text)
    consecutive_failures = Column(Integer, default=0)

    polls = Column(Integer, default=0)
    scenes_seen = Column(Integer, default=0)
    investigations_opened = Column(Integer, default=0)


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
    """Who did what, when, and from where. Never records a credential value.

    Started life as a credential-change log, which is why `provider`/`field`
    are still here. It now covers every mutating action, and two things make
    it evidence rather than decoration:

    * `actor_user_id` is taken from the session, never from a request body.
      An actor a client can choose is an actor nobody can rely on.
    * `row_hash` chains each row to the one before it, so deleting or editing
      history breaks the chain at that point instead of leaving no trace.

    The chain is tamper-EVIDENT, not tamper-proof: anyone with write access to
    the database file can rewrite rows, but not without `/api/audit/verify`
    noticing. That is the honest claim, and the one worth making.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(64))
    provider = Column(String(64))
    field = Column(String(64))
    # Free-text actor, kept for the pre-session rows already on disk. New rows
    # set it from the resolved account, so it agrees with actor_user_id.
    actor = Column(String(64), default="admin")
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    resource = Column(String(200), nullable=True, index=True)
    ip = Column(String(64), nullable=True)
    detail = Column(Text)
    occurred_utc = Column(DateTime(timezone=True), default=utcnow)
    # sha256(prev_hash || canonical(row)). NULL on the legacy rows written
    # before chaining existed; verification reports where the chain starts
    # rather than pretending those rows were covered.
    prev_hash = Column(String(64), nullable=True)
    row_hash = Column(String(64), nullable=True, index=True)


# --------------------------------------------------------------------------
# incidents
# --------------------------------------------------------------------------

# A spill event may be examined by several runs across several scenes, so the
# incident -- not the run -- is the unit of accountability. Runs stay immutable
# evidence; the incident is the mutable case file they attach to.
INCIDENT_STATUSES = ("open", "investigating", "attributed", "closed", "archived")

# Moving to these says the case is concluded, so they are reviewer/admin only
# (master plan section 8). Kept next to the vocabulary rather than in the route,
# so the rule is visible wherever the statuses are.
INCIDENT_REVIEWER_STATUSES = ("attributed", "closed")


class Incident(Base):
    """One spill event, and everything concluded about it.

    `geometry_json` holds GeoJSON rather than a lat/lon pair because an
    incident is an area of interest, not a point -- and because the promote
    action seeds it from a slick's own footprint.
    """

    __tablename__ = "incidents"

    id = Column(String(32), primary_key=True)         # INC-2026-001
    title = Column(String(200), nullable=False)
    geometry_json = Column(Text, nullable=True)
    detected_utc = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(24), nullable=False, default="open", index=True)
    assignee_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    region = Column(String(120), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_utc = Column(DateTime(timezone=True), default=utcnow)
    updated_utc = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    investigations = relationship("Investigation", back_populates="incident")


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

# The five roles from the production spec. Kept as a plain tuple rather than a
# DB enum: SQLite does not enforce enums anyway, and a CHECK constraint would
# have to be rewritten to add a role, which is exactly the kind of migration
# this schema is trying to avoid.
ROLES = ("admin", "investigator", "analyst", "reviewer", "auditor")


class User(Base):
    """An operator of the system.

    `password_hash` is nullable on purpose: the schema already accommodates an
    OIDC account that never has a local password, so adding SSO later needs no
    migration. A null hash cannot log in by password -- `verify_password`
    returns False rather than raising.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)
    display_name = Column(String(120))
    role = Column(String(32), nullable=False, default="investigator")
    active = Column(Boolean, nullable=False, default=True)
    created_utc = Column(DateTime(timezone=True), default=utcnow)
    last_login_utc = Column(DateTime(timezone=True), nullable=True)


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
