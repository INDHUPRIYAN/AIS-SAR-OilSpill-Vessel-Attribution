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
    status = Column(String(32), default="pending", index=True)  # pending|running|complete|failed
    started_utc = Column(DateTime(timezone=True), default=utcnow, index=True)
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

    # Denormalised at seal time so the history table can show an outcome
    # without opening 90-odd artefact bundles to render one page. Recomputed
    # only by the backfill; a sealed run's summary never changes on its own.
    top_suspect_mmsi = Column(Integer, nullable=True)
    top_score = Column(Float, nullable=True)
    slick_area_km2 = Column(Float, nullable=True)
    archived = Column(Boolean, nullable=False, default=False, index=True)
    region = Column(String(120), nullable=True, index=True)

    # How this row came to exist. The distinction is not bookkeeping: it says
    # whether the metadata beside it was OBSERVED or DERIVED.
    #
    #   "api"         the API watched this run happen and recorded its real
    #                 start time, its investigation and its incident;
    #   "reconciled"  the run was produced by the CLI and this row was rebuilt
    #                 afterwards from the sealed manifest, so every field here
    #                 is a derivation from the artefacts and some facts the API
    #                 would have recorded (which investigation, which incident)
    #                 are simply absent;
    #   NULL          the row predates this column and cannot say which it was.
    #
    # The artefacts remain the evidence either way. This column exists so a
    # reader can tell an index entry apart from a witness statement.
    registry_source = Column(String(16), nullable=True, index=True)

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


class Aoi(Base):
    """The definition of one watched area (design v2 SS21).

    Definitions used to live only in ``config/aois.yaml``. That is fine for a
    fixed deployment and wrong for an operator who wants to draw a box on a map:
    a UI cannot edit a file the server reads at import time, and two operators
    editing the same YAML have no way to merge. The table is now the source of
    truth and the YAML is migrated in once, so existing deployments keep their
    AOIs and nothing has to be re-registered by hand.

    ``AoiWatch`` still holds the mutable poll state, keyed by the same id. The
    split is deliberate: deleting an AOI definition must not silently reset a
    watch high-water mark that another AOI could later inherit by reusing the id.
    """

    __tablename__ = "aois"

    id = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    # [lon_min, lat_min, lon_max, lat_max] as JSON text. LONGITUDE FIRST, per
    # the frozen convention in aois.yaml.
    bbox_json = Column(Text, nullable=False)
    # Optional GeoJSON Polygon. The bbox is what the scene search uses; the
    # polygon is what the operator actually drew, kept so the map can show the
    # shape rather than its bounding box. Absent for YAML-migrated rows, which
    # only ever had a bbox -- absent, not a fabricated rectangle.
    geometry_json = Column(Text)
    ais_region = Column(String(64))
    poll_minutes = Column(Integer, default=60, nullable=False)
    lookback_hours = Column(Integer, default=24, nullable=False)
    auto_run = Column(Boolean, default=True, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    notes = Column(Text, default="")
    source = Column(String(16), default="api")      # api | yaml
    created_utc = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_utc = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class Job(Base):
    """One cancellable execution of the pipeline.

    A run used to be started by handing a daemon thread to `threading.Thread`
    and forgetting about it: nothing could report progress, and nothing could
    stop it. An operator who launched a run against the wrong scene had to wait
    out a full pipeline -- minutes on a real Sentinel-1 frame -- or restart the
    server, which is how you lose the other runs in flight.

    Cancellation is COOPERATIVE and checked between stages, never by killing a
    thread mid-write. A half-written GeoTIFF that survives into a sealed run is
    a far worse outcome than a run that takes ten more seconds to stop.
    """

    __tablename__ = "jobs"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id"), index=True, nullable=False)
    investigation_id = Column(String(64), ForeignKey("investigations.id"),
                              nullable=True, index=True)
    # pending | running | cancelling | cancelled | complete | failed
    status = Column(String(16), default="pending", nullable=False, index=True)
    # Name of the stage currently executing, straight from status.json. Not a
    # percentage: the stages have wildly different durations and a made-up
    # percentage would be a worse answer than the truth.
    current_stage = Column(String(32))
    stages_done = Column(Integer, default=0)
    stages_total = Column(Integer, default=5)
    created_utc = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_utc = Column(DateTime(timezone=True))
    finished_utc = Column(DateTime(timezone=True))
    cancel_requested_utc = Column(DateTime(timezone=True))
    cancelled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    error = Column(Text)
    # What this job was launched with, so `/runs/{id}/rerun` can reproduce it
    # exactly rather than guessing from the manifest.
    inputs_json = Column(Text)


class Report(Base):
    """A composed investigation report, versioned against the run it describes.

    Versioning is keyed to `artefact_digest`, not to a timestamp. That is the
    hash of the run's sealed artefact list, so a report can state which exact
    bytes it was composed from -- and a report whose digest no longer matches
    its run is detectably stale rather than quietly wrong.

    The review states are draft -> in_review -> published, and **published is
    immutable**. Editing a published report does not change it; it creates the
    next version as a draft. A report that could be edited after approval is
    not an approved report, it is a document that once had approval.
    """

    __tablename__ = "reports"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id"), index=True, nullable=False)
    investigation_id = Column(String(64), ForeignKey("investigations.id"),
                              nullable=True, index=True)
    version = Column(Integer, default=1, nullable=False)
    # draft | in_review | published
    status = Column(String(16), default="draft", nullable=False, index=True)
    # The composed document. Stored rather than recomposed on read: a report is
    # a statement made at a moment, and recomposing it later would silently
    # rewrite history whenever the composer changed.
    body_json = Column(Text, nullable=False)
    # Hash of the run's artefact list at compose time.
    artefact_digest = Column(String(64), index=True)
    title = Column(String(300))
    created_utc = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_utc = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    submitted_utc = Column(DateTime(timezone=True))
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    published_utc = Column(DateTime(timezone=True))
    review_note = Column(Text)


class Alert(Base):
    """Something the system noticed that a human has not yet dealt with.

    The watcher already opened investigations by itself; what it could not do
    was tell anyone. An investigation appearing in a list is not a notification
    -- nobody watches a list. An alert is the row that says "this happened, it
    is yours, and here is how long it has been waiting".

    Two design choices worth stating.

    **Dismissal requires a reason.** An alert that can be cleared with one
    unexplained click becomes a queue people clear rather than read, and the
    record of why nobody acted disappears with it. The reason is stored and the
    dismissal is audited.

    **Severity is derived, never typed in.** It comes from what was detected --
    a scene with oil candidates outranks a scene without. A free-text severity
    field would drift into a mood ring.
    """

    __tablename__ = "alerts"

    id = Column(String(64), primary_key=True)
    # new_scene | run_failed | detection | provider_down
    kind = Column(String(32), nullable=False, index=True)
    # info | warning | critical
    severity = Column(String(16), default="info", nullable=False, index=True)
    # open | acknowledged | assigned | dismissed
    status = Column(String(16), default="open", nullable=False, index=True)
    title = Column(String(300), nullable=False)
    detail = Column(Text)

    aoi_id = Column(String(64), nullable=True, index=True)
    scene_id = Column(String(200), nullable=True)
    run_id = Column(String(64), ForeignKey("runs.id"), nullable=True, index=True)
    investigation_id = Column(String(64), ForeignKey("investigations.id"),
                              nullable=True, index=True)

    created_utc = Column(DateTime(timezone=True), default=utcnow, nullable=False,
                         index=True)
    acknowledged_utc = Column(DateTime(timezone=True))
    acknowledged_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_utc = Column(DateTime(timezone=True))
    dismissed_utc = Column(DateTime(timezone=True))
    dismissed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Required to dismiss. Never nullable in practice, nullable in the column
    # so a row can exist before it is dismissed.
    dismiss_reason = Column(Text)


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
# vessels
# --------------------------------------------------------------------------


class Vessel(Base):
    """What is known about one MMSI, across every run it appeared in.

    Identity (`name`, `imo`, `call_sign`) lives HERE and not in
    `vessels.parquet`. That file is a frozen 14-column contract validated by
    five modules, and `validate_vessels_df` rejects extra columns outright --
    so identity is carried beside it rather than smuggled into it. The values
    come from the MarineCadastre archive at ingest, before the contract
    projection drops them.

    Every field is nullable and stays null when the source did not supply it.
    Synthetic AIS has no names at all, and inventing one for a vessel the
    system may go on to rank as a suspect is the single worst thing this table
    could do.
    """

    __tablename__ = "vessels"

    mmsi = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=True)
    imo = Column(String(20), nullable=True)
    call_sign = Column(String(20), nullable=True)
    flag = Column(String(64), nullable=True)
    vessel_type = Column(String(32), nullable=True)
    length_m = Column(Float, nullable=True)
    width_m = Column(Float, nullable=True)
    draught_m = Column(Float, nullable=True)
    # real | synthetic. A vessel seen in both is 'real': the synthetic
    # generator reuses MMSI ranges, and downgrading a real vessel because a
    # scenario borrowed its number would mislabel actual evidence.
    source = Column(String(16), nullable=False, default="synthetic", index=True)
    first_seen_utc = Column(DateTime(timezone=True), nullable=True)
    last_seen_utc = Column(DateTime(timezone=True), nullable=True)

    appearances = relationship("VesselAppearance", back_populates="vessel",
                               cascade="all, delete-orphan")


class VesselAppearance(Base):
    """One vessel's presence in one run, ranked or excluded.

    Filtered vessels are recorded too, with the gate that excluded them. A
    dossier that showed only the runs where a vessel scored highly would be a
    prosecution file rather than a record.
    """

    __tablename__ = "vessel_appearances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mmsi = Column(Integer, ForeignKey("vessels.mmsi"), nullable=False, index=True)
    run_id = Column(String(64), ForeignKey("runs.id"), nullable=False, index=True)
    incident_id = Column(String(32), nullable=True, index=True)

    rank = Column(Integer, nullable=True)
    total_score = Column(Float, nullable=True)
    filtered = Column(Boolean, nullable=False, default=False)
    filter_reason = Column(String(120), nullable=True)
    ais_gap_minutes = Column(Float, nullable=True)
    source = Column(String(16), nullable=False, default="synthetic")
    seen_utc = Column(DateTime(timezone=True), default=utcnow)

    vessel = relationship("Vessel", back_populates="appearances")


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


def _fill_column_defaults() -> None:
    """Give rows that predate a NOT NULL-by-intent column its default.

    `ALTER TABLE ... ADD COLUMN` leaves existing rows NULL, and a Python-side
    `default=` only applies to rows inserted afterwards. `runs.archived` was
    added that way, so 92 of 110 runs carried NULL -- and the runs listing,
    which filters `archived IS FALSE`, silently hid every one of them
    (found during P20 acceptance: the default list showed 18 runs). NULL means
    "never archived", and this makes the column say so.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("UPDATE runs SET archived = 0 WHERE archived IS NULL"))


def init_db() -> None:
    """Create tables and seed the provider registry."""
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _fill_column_defaults()
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
            # A NOT_DEPLOYED adapter has no fallback chain, so it has no active
            # member either. Defaulting it to itself would put a provider that
            # feeds nothing at the head of a chain that does not exist.
            if row.active_provider is None and spec["chain"]:
                row.active_provider = spec["chain"][0]
            if spec.get("deployment") == "NOT_DEPLOYED":
                row.status = "NOT_DEPLOYED"
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
