"""The engine contract, the context engines share, and progress reporting."""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Optional

from backend.services.hindcast.config import PipelineConfig


class EngineError(RuntimeError):
    """An engine cannot produce its output. The message is shown to the operator."""


@dataclass
class EngineResult:
    metrics: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


class Emitter:
    """Progress sink. This base class only records, which is what unit tests use;
    `app.events.DbEmitter` persists to `engine_runs` and pushes to subscribers."""

    def __init__(self) -> None:
        self.status = "pending"
        self.percent = 0.0
        self.current_step: Optional[str] = None
        self.metrics: dict[str, Any] = {}
        self.logs: list[str] = []
        self.error: Optional[str] = None
        self._last_flush = 0.0

    # -- what engines call --------------------------------------------------
    def step(self, text: str, percent: Optional[float] = None) -> None:
        self.current_step = text
        if percent is not None:
            self.percent = float(min(100.0, max(0.0, percent)))
        self._flush(force=False)

    def log(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logs.append(f"{stamp} {message}")
        self._flush(force=True)

    def metric(self, **values: Any) -> None:
        self.metrics.update(values)
        self._flush(force=False)

    def increment(self, deltas: dict[str, float], progress: Optional[tuple[str, str]] = None) -> None:
        """Add to counters. Safe when several workers report into one engine run
        (the DB emitter does the read-modify-write under a row lock).
        `progress=(done_key, total_key)` also derives the percent from them."""
        for key, delta in deltas.items():
            self.metrics[key] = self.metrics.get(key, 0) + delta
        if progress and self.metrics.get(progress[1]):
            self.percent = 3.0 + 92.0 * self.metrics[progress[0]] / self.metrics[progress[1]]
        self._flush(force=True)

    # -- lifecycle (called by the runner, not by engines) -------------------
    def started(self) -> None:
        self.status, self.percent = "running", 0.0
        self._flush(force=True)

    def succeeded(self) -> None:
        self.status, self.percent = "succeeded", 100.0
        self._flush(force=True)

    def failed(self, error: str) -> None:
        self.status, self.error = "failed", error
        self._flush(force=True)

    def _flush(self, force: bool) -> None:
        now = time.monotonic()
        if force or now - self._last_flush >= 0.25:
            self._last_flush = now
            self._write()

    def _write(self) -> None:   # overridden by DbEmitter
        return None


@dataclass
class PipelineContext:
    """What one job's engines share.

    `state` is small JSON (numbers, file names, feature dicts) and is persisted
    to `<workdir>/context.json` after every engine, because under Celery each
    engine may run in a different process. Arrays live in files in `workdir`.
    """

    job_id: str
    workdir: Path
    config: PipelineConfig
    request: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)
    emit: Emitter = field(default_factory=Emitter)

    def path(self, name: str) -> Path:
        return self.workdir / name

    def require(self, key: str) -> Any:
        if key not in self.state:
            raise EngineError(f"pipeline state has no '{key}'; an upstream engine did not run")
        return self.state[key]

    def save(self) -> None:
        tmp = self.path("context.json.tmp")
        tmp.write_text(json.dumps(self.state, default=str), encoding="utf-8")
        tmp.replace(self.path("context.json"))

    @classmethod
    def load(cls, job_id: str, workdir: Path, config: PipelineConfig,
             request: dict[str, Any], emit: Optional[Emitter] = None) -> "PipelineContext":
        state_file = workdir / "context.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
        return cls(job_id=job_id, workdir=workdir, config=config, request=request,
                   state=state, emit=emit or Emitter())

    @property
    def scene_time(self) -> datetime:
        return datetime.fromisoformat(self.require("scene_time"))


class Engine(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    stage: ClassVar[str]          # "Ingest", "Stage 0" ... "Stage 5"
    order: ClassVar[int]
    icon: ClassVar[str]           # a lucide icon name, for the monitoring page
    metric_keys: ClassVar[list[dict[str, str]]] = []   # which metrics the tile shows

    @abstractmethod
    def run(self, ctx: PipelineContext) -> EngineResult: ...

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {"id": cls.id, "name": cls.name, "description": cls.description,
                "stage": cls.stage, "order": cls.order, "icon": cls.icon,
                "metric_keys": cls.metric_keys}
