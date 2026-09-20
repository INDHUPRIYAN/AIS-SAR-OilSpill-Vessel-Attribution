"""BAYES-TRACK hindcast tables.

Prefixed `hindcast_` because OceanTrace already has a `jobs` table with its own
meaning (a cancellable pipeline run). A hindcast job is a different thing: one
pass of the seven hindcast engines over one slick.

Geometry is stored as GeoJSON text. This database is SQLite, so there is no
spatial column type to use; the archive lookup filters by time in SQL and
intersects in shapely.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text)
from sqlalchemy.orm import Session, relationship

from backend.models.db import Base, SessionLocal, utcnow

ENGINE_STATUSES = ("pending", "running", "succeeded", "failed", "skipped")
JOB_STATUSES = ("pending", "running", "succeeded", "failed")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class HindcastJob(Base):
    __tablename__ = "hindcast_jobs"

    id = Column(String(32), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    created_by = Column(String(255))
    status = Column(String(16), default="pending", index=True)
    scene_time = Column(DateTime(timezone=True))
    aoi = Column(Text)                         # GeoJSON polygon
    label = Column(String(120))
    # The request as received: scene_meta, slick polygon, oil type, forcing, truth (demo only).
    request = Column(JSON, default=dict)
    config = Column(JSON, default=dict)
    result = Column(JSON)
    error = Column(Text)

    engine_runs = relationship("HindcastEngineRun", back_populates="job",
                               cascade="all, delete-orphan", order_by="HindcastEngineRun.stage_order")


class HindcastEngineRun(Base):
    __tablename__ = "hindcast_engine_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(32), ForeignKey("hindcast_jobs.id", ondelete="CASCADE"), index=True)
    engine_id = Column(String(32), index=True)
    stage_order = Column(Integer, default=0)
    status = Column(String(16), default="pending")
    percent = Column(Float, default=0.0)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    duration_ms = Column(Integer)
    current_step = Column(Text)
    metrics = Column(JSON, default=dict)
    error = Column(Text)
    logs = Column(JSON, default=list)          # list[str]

    job = relationship("HindcastJob", back_populates="engine_runs")

    __table_args__ = (Index("ix_hindcast_engine_runs_job_engine", "job_id", "engine_id", unique=True),)


class HindcastArchiveScene(Base):
    """A prior acquisition over some area, and whether a slick was seen in it."""

    __tablename__ = "hindcast_archive_scenes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aoi = Column(Text, nullable=False)         # GeoJSON polygon
    scene_time = Column(DateTime(timezone=True), index=True, nullable=False)
    slick_present = Column(Boolean, default=False)


class HindcastParticleSnapshot(Base):
    __tablename__ = "hindcast_particle_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(32), ForeignKey("hindcast_jobs.id", ondelete="CASCADE"))
    member = Column(Integer)
    tau_hours = Column(Integer)
    parquet_path = Column(Text)

    __table_args__ = (Index("ix_hindcast_particle_snapshots_job_tau", "job_id", "tau_hours"),)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sweep_interrupted() -> int:
    """Hindcast jobs run in a thread of this process, so none survives a restart.
    Anything still pending or running at boot is marked failed rather than left
    showing RUNNING forever."""
    with session_scope() as db:
        stuck = db.query(HindcastJob).filter(HindcastJob.status.in_(("pending", "running"))).all()
        for job in stuck:
            job.status, job.error = "failed", "interrupted: the server restarted while this job was running"
            for run in job.engine_runs:
                if run.status == "running":
                    run.status, run.error = "failed", "interrupted by a server restart"
                elif run.status == "pending":
                    run.status, run.current_step = "skipped", "skipped: the job was interrupted"
        return len(stuck)


def dumps(obj: object) -> str:
    return json.dumps(obj)
